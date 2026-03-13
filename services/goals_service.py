"""
Goals Service
Downloads and parses the stretch-goal spreadsheet from SharePoint,
caches the result, and provides lookup helpers.

The spreadsheet (e.g. 'TWG - April 2025 LE.xlsx') contains a sheet called
'Sales Stretch Goal.v2' with monthly columns like Jan-26A (Actual),
Mar-26LE (Latest Estimate), Apr-26B (Budget / Stretch Goal).

Usage:
    from services.goals_service import get_goals_from_cache, get_territory_goal

    goals = get_goals_from_cache()          # full cache dict or None
    info  = get_territory_goal('LA', 2026, 3)  # {'actual', 'le', 'budget', 'goal'}
"""

import logging
import math
import re
from datetime import datetime

from config import Config
from extensions import cache
from services.constants import GOAL_TERRITORY_MAP, GOAL_REGION_MAP

logger = logging.getLogger(__name__)

# ── Cache keys ──
CACHE_KEY_GOALS = 'goals_data'
GOALS_CACHE_TIMEOUT = 86400  # 24 hours

# ── Month name → number ──
_MONTH_NUM = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'may': 5, 'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

# ── Regex for column headers: "Jan-26A", "Mar-26LE", "Apr-26B" ──
_HEADER_RE = re.compile(
    r'^([A-Za-z]{3})-(\d{2})(A|LE|B)$'
)


def _report_failure(error_msg):
    """Report a goals refresh failure to the health monitor."""
    try:
        from services.health_monitor import report_failure
        report_failure('goals_refresh', error_msg)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# Core: download, parse, cache
# ═══════════════════════════════════════════════════════════════

def refresh_goals_cache():
    """
    Download the goals spreadsheet from SharePoint, parse it,
    and store the result in the Flask-Caching file cache.

    Returns True on success, False on failure.
    """
    file_name = Config.GOALS_FILE_NAME
    sheet_name = Config.GOALS_SHEET_NAME
    multiplier = Config.GOAL_MULTIPLIER

    if not file_name:
        logger.warning("Goals: GOALS_FILE_NAME not configured — skipping.")
        return False

    logger.info(f"Goals: Downloading '{file_name}' from SharePoint...")

    try:
        from services.sharepoint_service import read_excel_from_sharepoint
        wb = read_excel_from_sharepoint(file_name)
    except Exception as e:
        logger.error(f"Goals: Failed to download from SharePoint: {e}")
        _report_failure(f"Download failed: {e}")
        return False

    if wb is None:
        logger.error("Goals: SharePoint returned None — file not found or download failed.")
        _report_failure("SharePoint returned None — file not found or download failed")
        return False

    # ── Open the target sheet ──
    if sheet_name not in wb.sheetnames:
        logger.error(
            f"Goals: Sheet '{sheet_name}' not found. "
            f"Available sheets: {wb.sheetnames}"
        )
        wb.close()
        _report_failure(f"Sheet '{sheet_name}' not found in workbook")
        return False

    ws = wb[sheet_name]
    logger.info(f"Goals: Opened sheet '{sheet_name}' ({ws.max_row} rows × {ws.max_column} cols)")

    try:
        data = _parse_goals_sheet(ws, multiplier)
    except Exception as e:
        logger.error(f"Goals: Failed to parse sheet: {e}", exc_info=True)
        wb.close()
        _report_failure(f"Parse failed: {e}")
        return False

    wb.close()

    if not data or not data.get('territories'):
        logger.error("Goals: Parsed zero territories — something is wrong with the sheet.")
        _report_failure("Parsed zero territories from sheet")
        return False

    data['last_updated'] = datetime.now()
    data['file_name'] = file_name
    data['sheet_name'] = sheet_name

    cache.set(CACHE_KEY_GOALS, data, timeout=GOALS_CACHE_TIMEOUT)

    terr_count = len(data.get('territories', {}))
    region_count = len(data.get('regions', {}))
    logger.info(
        f"Goals: Cached {terr_count} territories + {region_count} regions "
        f"(multiplier={multiplier}x)"
    )

    # Report health
    try:
        from services.health_monitor import report_success
        report_success('goals_refresh')
    except Exception:
        pass

    return True


def _parse_goals_sheet(ws, multiplier):
    """
    Parse the 'Sales Stretch Goal.v2' sheet.

    Strategy:
      1. Scan rows 1–10 to find the header row (contains patterns like Jan-26A).
      2. Build a column map: {col_idx: (month_num, year_full, type)} where type is 'actual'/'le'/'budget'.
      3. Walk rows below header, read column B as territory name.
      4. Look up in GOAL_TERRITORY_MAP (individual) or GOAL_REGION_MAP (subtotals).
      5. Read each mapped column's value, multiply, and store.
    """
    # Step 1: Find the header row
    header_row = None
    col_map = {}  # {col_idx: (month_num, year_full, 'actual'|'le'|'budget')}

    for row_idx in range(1, min(ws.max_row + 1, 15)):
        row_col_map = {}
        for col_idx in range(1, ws.max_column + 1):
            cell_val = ws.cell(row=row_idx, column=col_idx).value
            if cell_val is None:
                continue
            cell_str = str(cell_val).strip()
            m = _HEADER_RE.match(cell_str)
            if m:
                mon_abbr = m.group(1).lower()
                year_2d = int(m.group(2))
                suffix = m.group(3)

                month_num = _MONTH_NUM.get(mon_abbr)
                if month_num is None:
                    continue

                year_full = 2000 + year_2d
                val_type = {'A': 'actual', 'LE': 'le', 'B': 'budget'}[suffix]
                row_col_map[col_idx] = (month_num, year_full, val_type)

        if len(row_col_map) >= 3:  # Need at least a few month columns to be confident
            header_row = row_idx
            col_map = row_col_map
            break

    if header_row is None:
        logger.error("Goals: Could not find header row with month columns.")
        return None

    logger.info(
        f"Goals: Header row = {header_row}, found {len(col_map)} month columns "
        f"(cols {min(col_map.keys())}–{max(col_map.keys())})"
    )

    # Determine the year from the headers
    years_found = set(y for _, y, _ in col_map.values())
    primary_year = max(years_found) if years_found else datetime.now().year

    # Step 2: Find the territory name column (usually B = col 2)
    # Verify by checking if known territory names appear
    name_col = _find_name_column(ws, header_row)

    # Step 3: Read data rows
    territories = {}  # portal_display_name → {month: {actual, le, budget}}
    regions = {}      # region_key → {month: {actual, le, budget}}

    for row_idx in range(header_row + 1, ws.max_row + 1):
        raw_name = ws.cell(row=row_idx, column=name_col).value
        if raw_name is None:
            continue
        raw_name = str(raw_name).strip()
        if not raw_name:
            continue

        # Determine if this is a territory or a region subtotal
        portal_name = GOAL_TERRITORY_MAP.get(raw_name)
        region_key = GOAL_REGION_MAP.get(raw_name)

        if portal_name is None and region_key is None:
            # Unknown row — skip silently (blank rows, header repeats, etc.)
            continue

        # Read monthly values from the mapped columns
        monthly = {}
        for col_idx, (month_num, year_full, val_type) in col_map.items():
            cell_val = ws.cell(row=row_idx, column=col_idx).value
            if cell_val is None:
                continue
            try:
                num_val = float(cell_val)
                num_val = math.ceil(num_val * multiplier)
            except (ValueError, TypeError):
                continue

            if month_num not in monthly:
                monthly[month_num] = {'actual': None, 'le': None, 'budget': None}
            monthly[month_num][val_type] = num_val

        if portal_name:
            # Individual territory — merge with existing if same display name (e.g., LA + LA-CORP)
            if portal_name in territories:
                _merge_monthly(territories[portal_name], monthly)
            else:
                territories[portal_name] = monthly

        if region_key:
            regions[region_key] = monthly

    return {
        'year': primary_year,
        'territories': territories,
        'regions': regions,
    }


def _find_name_column(ws, header_row):
    """
    Find which column contains territory names.
    Check columns A (1) and B (2) for known territory names in the rows below the header.
    """
    all_known = set(GOAL_TERRITORY_MAP.keys()) | set(GOAL_REGION_MAP.keys())

    for candidate_col in (2, 1, 3):  # B, A, C
        hits = 0
        for row_idx in range(header_row + 1, min(header_row + 20, ws.max_row + 1)):
            val = ws.cell(row=row_idx, column=candidate_col).value
            if val and str(val).strip() in all_known:
                hits += 1
        if hits >= 3:
            logger.info(f"Goals: Territory name column = {candidate_col} ({hits} matches)")
            return candidate_col

    # Default to column B
    logger.warning("Goals: Could not confirm territory name column — defaulting to B (col 2)")
    return 2


def _merge_monthly(existing, new):
    """
    Merge monthly goal data, adding values for duplicate territories
    (e.g., LA + LA-CORP both map to portal 'LA').
    """
    for month_num, vals in new.items():
        if month_num not in existing:
            existing[month_num] = vals
        else:
            for key in ('actual', 'le', 'budget'):
                if vals.get(key) is not None:
                    if existing[month_num].get(key) is not None:
                        existing[month_num][key] += vals[key]
                    else:
                        existing[month_num][key] = vals[key]


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def get_goals_from_cache():
    """
    Return the cached goals data, or attempt a refresh on cold cache.
    Returns the full goals dict or None if unavailable.
    """
    data = cache.get(CACHE_KEY_GOALS)
    if data is None:
        logger.info("Goals: Cache miss — attempting refresh from SharePoint...")
        refresh_goals_cache()
        data = cache.get(CACHE_KEY_GOALS)
    return data


def get_territory_goal(location, year, month):
    """
    Look up goal data for a specific territory and month.

    Args:
        location: Portal display name (e.g. 'LA', 'Seattle', 'Vancouver')
        year:     Calendar year (e.g. 2026)
        month:    Month number 1–12

    Returns:
        Dict with keys: actual, le, budget, goal (best available value)
        or None if not found.
    """
    data = get_goals_from_cache()
    if not data:
        return None

    terr_data = data.get('territories', {}).get(location)
    if not terr_data:
        return None

    month_data = terr_data.get(month)
    if not month_data:
        return None

    # "goal" = best available: prefer budget, then LE, then actual
    goal = month_data.get('budget') or month_data.get('le') or month_data.get('actual')

    return {
        'actual': month_data.get('actual'),
        'le': month_data.get('le'),
        'budget': month_data.get('budget'),
        'goal': goal,
    }


def get_region_goal(region_key, year, month):
    """
    Look up goal data for a region subtotal (e.g. 'WEST', 'US_TOTAL', 'CANADA').
    """
    data = get_goals_from_cache()
    if not data:
        return None

    region_data = data.get('regions', {}).get(region_key)
    if not region_data:
        return None

    month_data = region_data.get(month)
    if not month_data:
        return None

    goal = month_data.get('budget') or month_data.get('le') or month_data.get('actual')
    return {
        'actual': month_data.get('actual'),
        'le': month_data.get('le'),
        'budget': month_data.get('budget'),
        'goal': goal,
    }
