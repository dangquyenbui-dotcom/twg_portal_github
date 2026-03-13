"""
Bookings Summary Service
Fetches and caches MTD / QTD / YTD bookings data for the Bookings Summary report.

Data sources:
  - sotran  → current month line items (live transactional data)
  - soytrn  → historical line items (completed months, identical schema)

Monthly Frozen File Strategy (current year only):
  Completed months in the CURRENT YEAR are frozen as tiny gzip JSON files in
  bookings_summary_data/. Each file stores both the simple summary format (for Bookings
  Summary rankings) and the dashboard format (for Executive Dashboard sharing).

  Auto-freeze: When the app detects a new month has started (e.g., it's March
  but no frozen file exists for February), it automatically fetches and freezes
  February from soytrn. No admin action needed.

  File structure:
    bookings_summary_data/
    ├── us_2026_01.json.gz   # Jan 2026 US (auto-frozen when Feb started)
    ├── us_2026_02.json.gz   # Feb 2026 US (auto-frozen when Mar started)
    ├── ca_2026_01.json.gz   # Jan 2026 CA
    └── ca_2026_02.json.gz   # Feb 2026 CA

Prior Year Data (for YoY comparison):
  Prior year data is read from the DASHBOARD's existing yearly frozen files
  (bookings_dashboard_data/us_2025.json.gz, etc.) which are already downloaded by the
  admin via the Dashboard Data Management page. These files contain monthly_totals
  breakdowns, so we can extract the exact months needed for MTD/QTD/YTD YoY.

  If a prior year frozen file doesn't exist, YoY gracefully shows "no data"
  instead of hitting SQL Server.

Time horizons:
  - MTD (Month-to-Date):   sotran only (current month, always live)
  - QTD (Quarter-to-Date): frozen months in quarter + current month from sotran
  - YTD (Year-to-Date):    frozen months Jan→last month + current month from sotran

Startup sequence (~3-5 seconds on normal restart):
  1. Auto-freeze any missing completed months in current year
  2. Read frozen files from disk (<1ms each)
  3. Query sotran for current month only (one small query per region)
  4. Read dashboard yearly files for prior year YoY (<1ms)
  5. Assemble MTD/QTD/YTD and cache

Cache sharing with Executive Dashboard:
  - Frozen files include dashboard-format aggregations
  - On refresh, dashboard cache keys for the current year are populated
    from frozen files + current month — zero extra SQL
"""

import calendar
import gzip
import json
import logging
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from config import Config
from services.db_connection import get_connection
from services.constants import BOOKINGS_EXCLUDED_CUSTOMERS, map_territory
from extensions import cache

logger = logging.getLogger(__name__)

# ── Directories ──
SUMMARY_DATA_DIR = Path(__file__).resolve().parent.parent / 'bookings_summary_data'
DASHBOARD_DATA_DIR = Path(__file__).resolve().parent.parent / 'bookings_dashboard_data'

# ── Cache keys ──
CACHE_KEY_PREFIX = "bookings_summary"
CACHE_KEY_UPDATED = "bookings_summary_last_updated"
CACHE_TIMEOUT = 2100  # 35 min (refresh every 30 min)

# ── Horizons ──
HORIZONS = ('mtd', 'qtd', 'ytd')


# ═══════════════════════════════════════════════════════════════
# Cache key helpers
# ═══════════════════════════════════════════════════════════════

def _cache_key(horizon):
    return f"{CACHE_KEY_PREFIX}_{horizon}"

def _cache_key_prior(horizon):
    return f"{CACHE_KEY_PREFIX}_{horizon}_prior"


# ═══════════════════════════════════════════════════════════════
# Date range helpers
# ═══════════════════════════════════════════════════════════════

def _get_quarter_start(d):
    q_month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, q_month, 1)


def _get_date_ranges(today=None):
    if today is None:
        today = date.today()

    month_start = date(today.year, today.month, 1)
    quarter_start = _get_quarter_start(today)
    year_start = date(today.year, 1, 1)

    try:
        prior_today = today.replace(year=today.year - 1)
    except ValueError:
        prior_today = date(today.year - 1, today.month, today.day - 1)

    prior_month_start = date(today.year - 1, today.month, 1)
    prior_quarter_start = _get_quarter_start(prior_today)
    prior_year_start = date(today.year - 1, 1, 1)

    return {
        'mtd': {
            'start': month_start, 'end': today,
            'prior_start': prior_month_start, 'prior_end': prior_today,
            'label': today.strftime('%B %Y'),
        },
        'qtd': {
            'start': quarter_start, 'end': today,
            'prior_start': prior_quarter_start, 'prior_end': prior_today,
            'label': f"Q{(today.month - 1) // 3 + 1} {today.year}",
        },
        'ytd': {
            'start': year_start, 'end': today,
            'prior_start': prior_year_start, 'prior_end': prior_today,
            'label': str(today.year),
        },
    }


def _months_in_range(start_date, end_date):
    """Yield (year, month) tuples for each month in the range."""
    d = date(start_date.year, start_date.month, 1)
    end = date(end_date.year, end_date.month, 1)
    while d <= end:
        yield d.year, d.month
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)


# ═══════════════════════════════════════════════════════════════
# Monthly frozen file I/O (current year only — bookings_summary_data/)
# ═══════════════════════════════════════════════════════════════

def _ensure_data_dir():
    SUMMARY_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _frozen_month_path(region, year, month):
    return SUMMARY_DATA_DIR / f"{region.lower()}_{year}_{month:02d}.json.gz"


def save_frozen_month(region, year, month, summary_data, dashboard_data):
    _ensure_data_dir()
    filepath = _frozen_month_path(region, year, month)

    payload = {
        'meta': {
            'region': region, 'year': year, 'month': month,
            'frozen_at': datetime.now().isoformat(), 'version': 1,
        },
        'summary': summary_data,
        'dashboard': dashboard_data,
    }

    with gzip.open(filepath, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump(payload, f, separators=(',', ':'), default=str)

    file_size = filepath.stat().st_size
    logger.info(
        f"SummaryData: Frozen {region} {year}-{month:02d} "
        f"(${summary_data['summary']['total_amount']:,}, {file_size:,} bytes)"
    )
    return file_size


def load_frozen_month(region, year, month):
    """Returns (summary_data, dashboard_data) or (None, None)."""
    filepath = _frozen_month_path(region, year, month)
    if not filepath.exists():
        return None, None
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            payload = json.load(f)
        return payload.get('summary'), payload.get('dashboard')
    except Exception as e:
        logger.error(f"SummaryData: Failed to load {filepath.name}: {e}")
        return None, None


def frozen_month_exists(region, year, month):
    return _frozen_month_path(region, year, month).exists()


def delete_frozen_month(region, year, month):
    filepath = _frozen_month_path(region, year, month)
    if filepath.exists():
        filepath.unlink()
        logger.info(f"SummaryData: Deleted {filepath.name}")
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# Dashboard yearly frozen file reader (prior year — bookings_dashboard_data/)
# ═══════════════════════════════════════════════════════════════

def _load_dashboard_yearly_file(region, year):
    """
    Load the dashboard's yearly frozen file (bookings_dashboard_data/{region}_{year}.json.gz).
    Returns the 'data' dict with monthly_totals, by_territory, etc., or None.
    """
    filepath = DASHBOARD_DATA_DIR / f"{region.lower()}_{year}.json.gz"
    if not filepath.exists():
        return None
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            wrapper = json.load(f)
        return wrapper.get('data')
    except Exception as e:
        logger.error(f"SummaryData: Failed to load dashboard file {filepath.name}: {e}")
        return None


def _extract_prior_year_summary(region, prior_start, prior_end):
    """
    Extract a date-range subset from the dashboard's yearly frozen file
    for prior-year YoY comparison.

    The dashboard file has monthly_totals with yr/mo/amount/units/orders,
    plus by_territory, by_salesman, by_customer with full-year totals.

    For MTD/QTD comparisons we filter monthly_totals to the matching months,
    then scale territory/salesman/customer rankings proportionally.
    For YTD we can use the full year data (up to prior_end month).

    Returns a summary dict in the simple Bookings Summary format, or None.
    """
    year = prior_start.year
    data = _load_dashboard_yearly_file(region, year)
    if data is None:
        return None

    # Filter monthly totals to the prior date range
    start_mo = prior_start.month
    end_mo = prior_end.month
    monthly = data.get('monthly_totals', [])
    matching_months = [
        m for m in monthly
        if m.get('yr') == year and start_mo <= m.get('mo', 0) <= end_mo
    ]

    if not matching_months:
        return _empty_single_region()

    # Sum matching months
    total_amount = sum(m.get('amount', 0) for m in matching_months)
    total_units = sum(m.get('units', 0) for m in matching_months)
    total_orders = sum(m.get('orders', 0) for m in matching_months)

    # For territory/salesman/customer: if the range covers the full year,
    # use the dashboard's full rankings. Otherwise, scale proportionally.
    full_year_amount = data.get('summary', {}).get('total_amount', 0)

    if full_year_amount > 0 and total_amount > 0:
        ratio = total_amount / full_year_amount
    else:
        ratio = 0

    # Build approximate rankings by scaling from full-year data
    territory_ranking = []
    for i, t in enumerate(data.get('by_territory', []), start=1):
        territory_ranking.append({
            "location": t.get('name', ''),
            "total": math.ceil(t.get('amount', 0) * ratio),
            "rank": i,
        })

    salesman_ranking = []
    for i, s in enumerate(data.get('by_salesman', []), start=1):
        salesman_ranking.append({
            "salesman": s.get('name', ''),
            "total": math.ceil(s.get('amount', 0) * ratio),
            "rank": i,
        })

    customer_ranking = []
    for i, c in enumerate(data.get('by_customer', [])[:50], start=1):
        customer_ranking.append({
            "customer": c.get('name', ''),
            "custno": c.get('custno', ''),
            "total": math.ceil(c.get('amount', 0) * ratio),
            "rank": i,
        })

    return {
        "summary": {
            "total_amount": math.ceil(total_amount),
            "total_units": total_units,
            "total_orders": total_orders,
            "total_territories": len(territory_ranking),
            "total_lines": 0,  # Not tracked in dashboard format
        },
        "territory_ranking": territory_ranking,
        "salesman_ranking": salesman_ranking,
        "customer_ranking": customer_ranking,
    }


# ═══════════════════════════════════════════════════════════════
# SQL Queries
# ═══════════════════════════════════════════════════════════════

def _build_month_query(database, table, year, month):
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end_exclusive = f"{year + 1}-01-01"
    else:
        end_exclusive = f"{year}-{month + 1:02d}-01"

    return f"""
    SELECT
        tr.sono,
        tr.origqtyord                           AS units,
        tr.origqtyord * tr.price * (1 - tr.disc / 100.0)
                                                AS amount,
        tr.ordate,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE sm.terr
        END                                     AS terr_code,
        tr.custno,
        cu.company                              AS cust_name,
        tr.salesmn,
        ic.plinid
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.ordate >= '{start}'
      AND tr.ordate < '{end_exclusive}'
      AND tr.currhist <> 'X'
      AND tr.sostat  NOT IN ('V', 'X')
      AND tr.sotype  NOT IN ('B', 'R')
    """


def _build_raw_export_query(database, table, start_date, end_date):
    return f"""
    SELECT
        tr.sono              AS SalesOrder,
        tr.tranlineno        AS [LineNo],
        tr.ordate            AS OrderDate,
        tr.custno            AS CustomerNo,
        cu.company           AS CustomerName,
        tr.item              AS Item,
        tr.descrip           AS Description,
        ic.plinid            AS ProductLine,
        tr.origqtyord        AS QtyOrdered,
        tr.qtyshp            AS QtyShipped,
        tr.price             AS UnitPrice,
        tr.disc              AS Discount,
        tr.origqtyord * tr.price * (1 - tr.disc / 100.0)
                             AS ExtAmount,
        tr.extprice          AS ExtPrice,
        tr.sostat            AS LineStatus,
        tr.sotype            AS OrderType,
        tr.currhist          AS CurrHist,
        tr.terr              AS TranTerr,
        tr.salesmn           AS Salesman,
        sm.terr              AS SOMastTerr,
        cu.terr              AS CustTerr,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE sm.terr
        END                  AS TerrCode,
        tr.loctid            AS Location,
        tr.rqdate            AS RequestDate,
        tr.shipdate          AS ShipDate,
        sm.shipvia           AS ShipVia
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.ordate >= '{start_date}'
      AND tr.ordate <= '{end_date}'
      AND tr.currhist <> 'X'
      AND tr.sostat  NOT IN ('V', 'X')
      AND tr.sotype  NOT IN ('B', 'R')
    """


# ═══════════════════════════════════════════════════════════════
# Aggregation — simple format (for Bookings Summary)
# ═══════════════════════════════════════════════════════════════

def _aggregate_rows(rows, region='US'):
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_orders = set()
    territory_totals = defaultdict(float)
    salesman_totals = defaultdict(float)
    customer_totals = defaultdict(lambda: {'name': '', 'amount': 0.0})

    for sono, units, amount, ordate, terr_code, custno, cust_name, salesmn, plinid in rows:
        custno_clean = (custno or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue
        if (plinid or '').strip().upper() == 'TAX':
            continue

        territory = map_territory(terr_code, region)
        salesman = (salesmn or '').strip() or 'Unassigned'
        customer_display = (cust_name or '').strip() or custno_clean
        amt = float(amount or 0)
        qty = int(units or 0)

        total_amount += amt
        total_units += qty
        total_lines += 1
        if sono:
            distinct_orders.add(sono)
        territory_totals[territory] += amt
        salesman_totals[salesman] += amt
        customer_totals[custno_clean]['amount'] += amt
        customer_totals[custno_clean]['name'] = customer_display

    terr_sorted = sorted(territory_totals.items(), key=lambda x: x[1], reverse=True)
    territory_ranking = [
        {"location": loc, "total": math.ceil(total), "rank": rank}
        for rank, (loc, total) in enumerate(terr_sorted, start=1)
    ]

    sm_sorted = sorted(salesman_totals.items(), key=lambda x: x[1], reverse=True)
    salesman_ranking = [
        {"salesman": sm, "total": math.ceil(total), "rank": rank}
        for rank, (sm, total) in enumerate(sm_sorted, start=1)
    ]

    cust_sorted = sorted(customer_totals.items(), key=lambda x: x[1]['amount'], reverse=True)
    customer_ranking = [
        {"customer": v['name'], "custno": k, "total": math.ceil(v['amount']), "rank": rank}
        for rank, (k, v) in enumerate(cust_sorted, start=1)
    ]

    return {
        "summary": {
            "total_amount": math.ceil(total_amount),
            "total_units": total_units,
            "total_orders": len(distinct_orders),
            "total_territories": len(territory_totals),
            "total_lines": total_lines,
        },
        "territory_ranking": territory_ranking,
        "salesman_ranking": salesman_ranking,
        "customer_ranking": customer_ranking,
    }


# ═══════════════════════════════════════════════════════════════
# Aggregation — dashboard format (for Executive Dashboard sharing)
# ═══════════════════════════════════════════════════════════════

def _aggregate_rows_dashboard_format(rows, region='US'):
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_orders = set()
    monthly = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    terr_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    sm_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    pl_data = defaultdict(lambda: {'amount': 0.0, 'units': 0})
    cust_data = defaultdict(lambda: {'name': '', 'amount': 0.0, 'units': 0, 'orders': set()})

    for sono, units, amount, ordate, terr_code, custno, cust_name, salesmn, plinid in rows:
        custno_clean = (custno or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue
        if (plinid or '').strip().upper() == 'TAX':
            continue

        territory = map_territory(terr_code, region)
        salesman = (salesmn or '').strip() or 'Unassigned'
        product_line = (plinid or '').strip() or 'Other'
        customer_display = (cust_name or '').strip() or custno_clean
        amt = float(amount or 0)
        qty = int(units or 0)

        if hasattr(ordate, 'year'):
            yr, mo = ordate.year, ordate.month
        else:
            try:
                dt = datetime.strptime(str(ordate)[:10], '%Y-%m-%d')
                yr, mo = dt.year, dt.month
            except (ValueError, TypeError):
                continue

        total_amount += amt
        total_units += qty
        total_lines += 1
        if sono:
            distinct_orders.add(sono)

        mk = (yr, mo)
        monthly[mk]['amount'] += amt
        monthly[mk]['units'] += qty
        monthly[mk]['orders'].add(sono)
        terr_data[territory]['amount'] += amt
        terr_data[territory]['units'] += qty
        terr_data[territory]['orders'].add(sono)
        sm_data[salesman]['amount'] += amt
        sm_data[salesman]['units'] += qty
        sm_data[salesman]['orders'].add(sono)
        pl_data[product_line]['amount'] += amt
        pl_data[product_line]['units'] += qty
        cust_data[custno_clean]['name'] = customer_display
        cust_data[custno_clean]['amount'] += amt
        cust_data[custno_clean]['units'] += qty
        cust_data[custno_clean]['orders'].add(sono)

    monthly_totals = sorted([
        {'yr': yr, 'mo': mo, 'amount': math.ceil(v['amount']),
         'units': v['units'], 'orders': len(v['orders'])}
        for (yr, mo), v in monthly.items()
    ], key=lambda x: (x['yr'], x['mo']))

    def _build_ranked(data_dict, key_field='name'):
        result = sorted([
            {key_field: k, 'amount': math.ceil(v['amount']), 'units': v['units'],
             **({'orders': len(v['orders'])} if 'orders' in v else {})}
            for k, v in data_dict.items()
        ], key=lambda x: x['amount'], reverse=True)
        for i, r in enumerate(result):
            r['rank'] = i + 1
        return result

    by_product_line = sorted([
        {'name': k, 'amount': math.ceil(v['amount']), 'units': v['units']}
        for k, v in pl_data.items()
    ], key=lambda x: x['amount'], reverse=True)
    for i, p in enumerate(by_product_line):
        p['rank'] = i + 1

    by_customer = sorted([
        {'custno': k, 'name': v['name'], 'amount': math.ceil(v['amount']),
         'units': v['units'], 'orders': len(v['orders'])}
        for k, v in cust_data.items()
    ], key=lambda x: x['amount'], reverse=True)[:50]
    for i, c in enumerate(by_customer):
        c['rank'] = i + 1

    return {
        'summary': {
            'total_amount': math.ceil(total_amount),
            'total_units': total_units,
            'total_orders': len(distinct_orders),
            'total_lines': total_lines,
        },
        'monthly_totals': monthly_totals,
        'by_territory': _build_ranked(terr_data),
        'by_salesman': _build_ranked(sm_data),
        'by_product_line': by_product_line,
        'by_customer': by_customer,
    }


# ═══════════════════════════════════════════════════════════════
# Fetch a single month from SQL and freeze it
# ═══════════════════════════════════════════════════════════════

def _fetch_and_freeze_month(region, year, month):
    """
    Fetch a single completed month from soytrn, aggregate in both formats, save to disk.
    Only called for current-year completed months (not prior years).
    """
    database = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA
    query = _build_month_query(database, 'soytrn', year, month)
    label = f"{region} {year}-{month:02d}"

    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        if not rows:
            logger.info(f"SummaryData: {label} has 0 rows in soytrn — saving empty file")
            empty_summary = _empty_single_region()
            empty_dashboard = _empty_dashboard_region()
            save_frozen_month(region, year, month, empty_summary, empty_dashboard)
            return empty_summary, empty_dashboard

        summary_data = _aggregate_rows(rows, region=region)
        dashboard_data = _aggregate_rows_dashboard_format(rows, region=region)
        save_frozen_month(region, year, month, summary_data, dashboard_data)

        logger.info(
            f"SummaryData: Auto-frozen {label} — "
            f"${summary_data['summary']['total_amount']:,} ({len(rows):,} rows)"
        )
        return summary_data, dashboard_data

    except Exception as e:
        logger.error(f"SummaryData: Failed to fetch/freeze {label}: {e}")
        return None, None


def _fetch_current_month_live(region):
    """Fetch current month from sotran (live data, always fresh)."""
    today = date.today()
    database = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA
    query = _build_month_query(database, 'sotran', today.year, today.month)
    label = f"{region} sotran {today.year}-{today.month:02d}"

    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        logger.info(f"SummaryData: {label} fetched {len(rows):,} live rows")
        return rows
    except Exception as e:
        logger.error(f"SummaryData: {label} query failed: {e}")
        return []


# ═══════════════════════════════════════════════════════════════
# Auto-freeze: only current year completed months
# ═══════════════════════════════════════════════════════════════

def auto_freeze_completed_months():
    """
    Freeze any missing completed months in the CURRENT YEAR only.
    Prior years use the dashboard's yearly frozen files — no individual months needed.
    """
    today = date.today()
    current_year = today.year
    current_month = today.month
    frozen_count = 0

    for month in range(1, current_month):  # Don't freeze current month
        for region in ('US', 'CA'):
            if not frozen_month_exists(region, current_year, month):
                logger.info(f"SummaryData: Auto-freezing {region} {current_year}-{month:02d}...")
                _fetch_and_freeze_month(region, current_year, month)
                frozen_count += 1

    if frozen_count > 0:
        logger.info(f"SummaryData: Auto-freeze complete — {frozen_count} month(s) frozen")
    else:
        logger.info("SummaryData: All current-year completed months already frozen")


# ═══════════════════════════════════════════════════════════════
# Merge multiple summary dicts
# ═══════════════════════════════════════════════════════════════

def _merge_summary_dicts(dicts):
    """Merge multiple single-region simple summary dicts into one."""
    total_amount = 0
    total_units = 0
    total_orders = 0
    total_lines = 0
    territory_totals = defaultdict(float)
    salesman_totals = defaultdict(float)
    customer_totals = defaultdict(lambda: {'name': '', 'amount': 0.0})

    for d in dicts:
        if d is None:
            continue
        s = d.get('summary', {})
        total_amount += s.get('total_amount', 0)
        total_units += s.get('total_units', 0)
        total_orders += s.get('total_orders', 0)
        total_lines += s.get('total_lines', 0)

        for t in d.get('territory_ranking', []):
            territory_totals[t['location']] += t['total']
        for sm in d.get('salesman_ranking', []):
            salesman_totals[sm['salesman']] += sm['total']
        for c in d.get('customer_ranking', []):
            customer_totals[c['custno']]['name'] = c['customer']
            customer_totals[c['custno']]['amount'] += c['total']

    terr_sorted = sorted(territory_totals.items(), key=lambda x: x[1], reverse=True)
    territory_ranking = [
        {"location": loc, "total": math.ceil(total), "rank": rank}
        for rank, (loc, total) in enumerate(terr_sorted, start=1)
    ]
    sm_sorted = sorted(salesman_totals.items(), key=lambda x: x[1], reverse=True)
    salesman_ranking = [
        {"salesman": sm, "total": math.ceil(total), "rank": rank}
        for rank, (sm, total) in enumerate(sm_sorted, start=1)
    ]
    cust_sorted = sorted(customer_totals.items(), key=lambda x: x[1]['amount'], reverse=True)
    customer_ranking = [
        {"customer": v['name'], "custno": k, "total": math.ceil(v['amount']), "rank": rank}
        for rank, (k, v) in enumerate(cust_sorted, start=1)
    ]

    return {
        "summary": {
            "total_amount": math.ceil(total_amount),
            "total_units": total_units,
            "total_orders": total_orders,
            "total_territories": len(territory_totals),
            "total_lines": total_lines,
        },
        "territory_ranking": territory_ranking,
        "salesman_ranking": salesman_ranking,
        "customer_ranking": customer_ranking,
    }


def _empty_single_region():
    return {
        "summary": {
            "total_amount": 0, "total_units": 0, "total_orders": 0,
            "total_territories": 0, "total_lines": 0,
        },
        "territory_ranking": [], "salesman_ranking": [], "customer_ranking": [],
    }


def _empty_dashboard_region():
    return {
        'summary': {'total_amount': 0, 'total_units': 0, 'total_orders': 0, 'total_lines': 0},
        'monthly_totals': [], 'by_territory': [], 'by_salesman': [],
        'by_product_line': [], 'by_customer': [],
    }


# ═══════════════════════════════════════════════════════════════
# Assemble current-year horizon from frozen files + live sotran
# ═══════════════════════════════════════════════════════════════

def _assemble_current_year_region(region, completed_months, include_current, current_rows=None):
    """
    Assemble data for a single region for a current-year horizon.
    Reads frozen monthly files for completed months, live rows for current month.
    Returns (summary_data, dashboard_data).
    """
    summary_parts = []
    dashboard_parts = []

    for year, month in completed_months:
        summary, dashboard = load_frozen_month(region, year, month)
        if summary is not None:
            summary_parts.append(summary)
        if dashboard is not None:
            dashboard_parts.append(dashboard)

    if include_current:
        rows = current_rows if current_rows is not None else _fetch_current_month_live(region)
        if rows:
            summary_parts.append(_aggregate_rows(rows, region=region))
            dashboard_parts.append(_aggregate_rows_dashboard_format(rows, region=region))

    merged_summary = _merge_summary_dicts(summary_parts) if summary_parts else _empty_single_region()

    # Merge dashboard parts using dashboard_data_service's merge
    merged_dashboard = None
    if dashboard_parts:
        from services.bookings_dashboard_data_service import _merge_summaries
        merged_dashboard = dashboard_parts[0]
        for part in dashboard_parts[1:]:
            merged_dashboard = _merge_summaries(merged_dashboard, part)

    return merged_summary, merged_dashboard or _empty_dashboard_region()


# ═══════════════════════════════════════════════════════════════
# Assemble prior-year horizon from dashboard yearly files
# ═══════════════════════════════════════════════════════════════

def _assemble_prior_year_region(region, prior_start, prior_end):
    """
    Assemble prior-year comparison data for a single region.
    Reads from the dashboard's yearly frozen file (bookings_dashboard_data/).
    Returns summary_data or None if file doesn't exist.
    """
    return _extract_prior_year_summary(region, prior_start, prior_end)


# ═══════════════════════════════════════════════════════════════
# Merge US + CA (CAD → USD)
# ═══════════════════════════════════════════════════════════════

def _merge_regions(us_data, ca_data, cad_rate):
    rate = cad_rate or 0.72

    if us_data is None and ca_data is None:
        return _empty_result()

    us_amount = us_data['summary']['total_amount'] if us_data else 0
    ca_amount = ca_data['summary']['total_amount'] if ca_data else 0
    ca_amount_usd = math.ceil(ca_amount * rate)

    us_units = us_data['summary']['total_units'] if us_data else 0
    ca_units = ca_data['summary']['total_units'] if ca_data else 0
    us_orders = us_data['summary']['total_orders'] if us_data else 0
    ca_orders = ca_data['summary']['total_orders'] if ca_data else 0
    us_territories = us_data['summary']['total_territories'] if us_data else 0
    ca_territories = ca_data['summary']['total_territories'] if ca_data else 0
    us_lines = us_data['summary']['total_lines'] if us_data else 0
    ca_lines = ca_data['summary']['total_lines'] if ca_data else 0

    terr_merged = defaultdict(float)
    for item in (us_data['territory_ranking'] if us_data else []):
        terr_merged[item['location']] += item['total']
    for item in (ca_data['territory_ranking'] if ca_data else []):
        terr_merged[item['location']] += math.ceil(item['total'] * rate)
    terr_sorted = sorted(terr_merged.items(), key=lambda x: x[1], reverse=True)
    territory_ranking = [
        {"location": loc, "total": total, "rank": rank}
        for rank, (loc, total) in enumerate(terr_sorted, start=1)
    ]

    sm_merged = defaultdict(float)
    for item in (us_data['salesman_ranking'] if us_data else []):
        sm_merged[item['salesman']] += item['total']
    for item in (ca_data['salesman_ranking'] if ca_data else []):
        sm_merged[item['salesman']] += math.ceil(item['total'] * rate)
    sm_sorted = sorted(sm_merged.items(), key=lambda x: x[1], reverse=True)
    salesman_ranking = [
        {"salesman": sm, "total": total, "rank": rank}
        for rank, (sm, total) in enumerate(sm_sorted, start=1)
    ]

    cust_merged = defaultdict(lambda: {'name': '', 'amount': 0.0})
    for item in (us_data['customer_ranking'] if us_data else []):
        cust_merged[item['custno']]['name'] = item['customer']
        cust_merged[item['custno']]['amount'] += item['total']
    for item in (ca_data['customer_ranking'] if ca_data else []):
        cust_merged[item['custno']]['name'] = item['customer']
        cust_merged[item['custno']]['amount'] += math.ceil(item['total'] * rate)
    cust_sorted = sorted(cust_merged.items(), key=lambda x: x[1]['amount'], reverse=True)
    customer_ranking = [
        {"customer": v['name'], "custno": k, "total": math.ceil(v['amount']), "rank": rank}
        for rank, (k, v) in enumerate(cust_sorted, start=1)
    ]

    return {
        "summary": {
            "total_amount": math.ceil(us_amount + ca_amount_usd),
            "total_units": us_units + ca_units,
            "total_orders": us_orders + ca_orders,
            "total_territories": us_territories + ca_territories,
            "total_lines": us_lines + ca_lines,
        },
        "territory_ranking": territory_ranking,
        "salesman_ranking": salesman_ranking,
        "customer_ranking": customer_ranking,
        "region_split": {
            "us_amount": us_amount, "ca_amount": ca_amount, "ca_amount_usd": ca_amount_usd,
        },
    }


def _empty_result():
    return {
        "summary": {
            "total_amount": 0, "total_units": 0, "total_orders": 0,
            "total_territories": 0, "total_lines": 0,
        },
        "territory_ranking": [], "salesman_ranking": [], "customer_ranking": [],
        "region_split": {"us_amount": 0, "ca_amount": 0, "ca_amount_usd": 0},
    }


# ═══════════════════════════════════════════════════════════════
# Year-over-Year comparison
# ═══════════════════════════════════════════════════════════════

def _compute_yoy(current_summary, prior_summary):
    if prior_summary is None:
        prior_summary = _empty_result()

    def _pct_change(current, prior):
        if prior == 0:
            return (100.0, 'up') if current > 0 else (0.0, 'flat')
        pct = ((current - prior) / prior) * 100
        if pct > 0.5:
            return (pct, 'up')
        elif pct < -0.5:
            return (abs(pct), 'down')
        return (0.0, 'flat')

    c = current_summary['summary']
    p = prior_summary['summary']

    amt_pct, amt_dir = _pct_change(c['total_amount'], p['total_amount'])
    units_pct, units_dir = _pct_change(c['total_units'], p['total_units'])
    orders_pct, orders_dir = _pct_change(c['total_orders'], p['total_orders'])

    return {
        'amount': {'pct': round(amt_pct, 1), 'direction': amt_dir, 'prior': p['total_amount']},
        'units': {'pct': round(units_pct, 1), 'direction': units_dir, 'prior': p['total_units']},
        'orders': {'pct': round(orders_pct, 1), 'direction': orders_dir, 'prior': p['total_orders']},
    }


# ═══════════════════════════════════════════════════════════════
# Core refresh — assemble all horizons
# ═══════════════════════════════════════════════════════════════

def refresh_bookings_summary(cad_rate=None):
    """
    Main refresh: assemble MTD/QTD/YTD from:
      - Current year completed months: frozen files in bookings_summary_data/
      - Current month: live from sotran
      - Prior year (YoY): dashboard yearly files in bookings_dashboard_data/

    Also populates dashboard cache for current year as a side effect.
    """
    logger.info("BookingsSummary: === Refreshing MTD / QTD / YTD ===")

    if cad_rate is None:
        from services.data_worker import CACHE_KEY_CAD_RATE, DEFAULT_CAD_TO_USD
        cad_rate = cache.get(CACHE_KEY_CAD_RATE) or DEFAULT_CAD_TO_USD

    today = date.today()
    current_year = today.year
    current_month = today.month
    date_ranges = _get_date_ranges(today)

    # Step 1: Auto-freeze current year completed months only
    auto_freeze_completed_months()

    # Step 2: Fetch current month live (reused across all horizons)
    us_current_rows = _fetch_current_month_live('US')
    ca_current_rows = _fetch_current_month_live('CA')

    # Step 3: Assemble each horizon
    for horizon in HORIZONS:
        dr = date_ranges[horizon]

        # Current year completed months in this horizon's range
        completed_months = [
            (yr, mo) for yr, mo in _months_in_range(dr['start'], dr['end'])
            if yr == current_year and mo < current_month
        ]
        include_current = (dr['end'].year == current_year and dr['end'].month == current_month)

        # Current period — US + CA from frozen files + live
        us_summary, _ = _assemble_current_year_region(
            'US', completed_months, include_current, us_current_rows)
        ca_summary, _ = _assemble_current_year_region(
            'CA', completed_months, include_current, ca_current_rows)
        current_merged = _merge_regions(us_summary, ca_summary, cad_rate)

        # Prior year — from dashboard yearly frozen files (zero SQL)
        us_prior = _assemble_prior_year_region('US', dr['prior_start'], dr['prior_end'])
        ca_prior = _assemble_prior_year_region('CA', dr['prior_start'], dr['prior_end'])

        if us_prior is not None or ca_prior is not None:
            prior_merged = _merge_regions(us_prior, ca_prior, cad_rate)
        else:
            prior_merged = None
            logger.info(
                f"BookingsSummary: No prior year data for {horizon.upper()} YoY "
                f"(download {dr['prior_start'].year} via admin page)"
            )

        # YoY — only compute if prior year data actually exists
        yoy = _compute_yoy(current_merged, prior_merged) if prior_merged is not None else {}
        current_merged['label'] = dr['label']
        current_merged['start_date'] = dr['start'].isoformat()
        current_merged['end_date'] = dr['end'].isoformat()
        current_merged['yoy'] = yoy

        # Build prior period label — the dashboard frozen files give us full months,
        # so when the current period is partial (mid-month), we label the prior
        # period clearly so the user knows it's a full-month comparison.
        prior_year = dr['prior_start'].year
        if horizon == 'mtd':
            # Prior is a full month vs our partial month
            month_name = calendar.month_name[dr['prior_start'].month]
            current_merged['prior_label'] = f"vs {month_name} {prior_year} (full month)"
        elif horizon == 'qtd':
            q_num = (dr['prior_start'].month - 1) // 3 + 1
            prior_end_month = dr['prior_end'].month
            current_end_month = dr['end'].month
            # If we're mid-month in the last month of the quarter range,
            # the prior covers full months
            if today.day < 28:  # Not near end of month
                current_merged['prior_label'] = (
                    f"vs Q{q_num} {prior_year} (full months)"
                )
            else:
                current_merged['prior_label'] = f"vs Q{q_num} {prior_year}"
        elif horizon == 'ytd':
            current_merged['prior_label'] = (
                f"vs Jan–{calendar.month_abbr[dr['prior_end'].month]} {prior_year} (full months)"
            )

        # Cache
        cache.set(_cache_key(horizon), current_merged, timeout=CACHE_TIMEOUT)
        if prior_merged:
            cache.set(_cache_key_prior(horizon), prior_merged, timeout=CACHE_TIMEOUT)

        logger.info(
            f"BookingsSummary: {horizon.upper()} cached — "
            f"${current_merged['summary']['total_amount']:,}"
        )

    # Step 4: Populate dashboard cache for current year
    _populate_dashboard_cache(current_year, current_month, us_current_rows, ca_current_rows)

    cache.set(CACHE_KEY_UPDATED, datetime.now(), timeout=CACHE_TIMEOUT)
    logger.info("BookingsSummary: === Refresh complete ===")


def _populate_dashboard_cache(current_year, current_month, us_current_rows, ca_current_rows):
    """Populate the Executive Dashboard's cache for the current year from frozen files + live."""
    from services.bookings_dashboard_data_service import (
        _cache_key_hist, _cache_key_current,
        DASH_HIST_TIMEOUT, DASH_CURRENT_TIMEOUT,
        CACHE_KEY_DASH_UPDATED, _merge_summaries,
    )

    for region, current_rows in [('US', us_current_rows), ('CA', ca_current_rows)]:
        # Merge completed months' dashboard data for historical cache
        hist_parts = []
        for month in range(1, current_month):
            _, dashboard = load_frozen_month(region, current_year, month)
            if dashboard is not None:
                hist_parts.append(dashboard)

        if hist_parts:
            hist_merged = hist_parts[0]
            for part in hist_parts[1:]:
                hist_merged = _merge_summaries(hist_merged, part)
            cache.set(_cache_key_hist(region, current_year), hist_merged, timeout=DASH_HIST_TIMEOUT)
            logger.info(
                f"BookingsSummary → Dashboard: {region} hist {current_year} "
                f"(${hist_merged['summary']['total_amount']:,})"
            )

        # Current month
        if current_rows:
            curr_dash = _aggregate_rows_dashboard_format(current_rows, region=region)
            cache.set(_cache_key_current(region), curr_dash, timeout=DASH_CURRENT_TIMEOUT)
            logger.info(
                f"BookingsSummary → Dashboard: {region} current month "
                f"(${curr_dash['summary']['total_amount']:,})"
            )

    cache.set(CACHE_KEY_DASH_UPDATED, datetime.now(), timeout=DASH_CURRENT_TIMEOUT)


# ═══════════════════════════════════════════════════════════════
# Raw export (for Excel)
# ═══════════════════════════════════════════════════════════════

def _process_raw_rows(cursor, rows, region='US'):
    columns = [col[0] for col in cursor.description]
    results = []
    for row in rows:
        record = dict(zip(columns, row))
        custno_clean = (record.get('CustomerNo') or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue
        if (record.get('ProductLine') or '').strip().upper() == 'TAX':
            continue
        terr_code = (record.get('TerrCode') or '').strip()
        record['Territory'] = map_territory(terr_code, region)
        for key in ('CustomerNo', 'CustomerName', 'Item', 'Description',
                    'ProductLine', 'Salesman', 'Location', 'ShipVia'):
            if record.get(key):
                record[key] = str(record[key]).strip()
        results.append(record)
    return results


def fetch_raw_export_data(horizon, cad_rate=None):
    """Fetch raw line-item data for Excel export. Returns (us_rows, ca_rows)."""
    today = date.today()
    date_ranges = _get_date_ranges(today)
    dr = date_ranges.get(horizon)
    if not dr:
        return [], []

    current_month_start = date(today.year, today.month, 1)
    us_rows = []
    ca_rows = []

    for db_name, region, result_list in [
        (Config.DB_ORDERS, 'US', us_rows),
        (Config.DB_ORDERS_CA, 'CA', ca_rows),
    ]:
        if dr['start'] < current_month_start:
            hist_end = min(dr['end'], current_month_start - timedelta(days=1))
            query = _build_raw_export_query(db_name, 'soytrn', dr['start'], hist_end)
            try:
                conn = get_connection(db_name)
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                result_list.extend(_process_raw_rows(cursor, rows, region))
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"BookingsSummary: {region} raw soytrn export failed: {e}")

        if dr['end'] >= current_month_start:
            curr_start = max(dr['start'], current_month_start)
            query = _build_raw_export_query(db_name, 'sotran', curr_start, dr['end'])
            try:
                conn = get_connection(db_name)
                cursor = conn.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()
                result_list.extend(_process_raw_rows(cursor, rows, region))
                cursor.close()
                conn.close()
            except Exception as e:
                logger.error(f"BookingsSummary: {region} raw sotran export failed: {e}")

    return us_rows, ca_rows


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def get_bookings_summary_from_cache(cad_rate=None):
    mtd = cache.get(_cache_key('mtd'))
    qtd = cache.get(_cache_key('qtd'))
    ytd = cache.get(_cache_key('ytd'))
    updated = cache.get(CACHE_KEY_UPDATED)

    if mtd is None and qtd is None and ytd is None:
        logger.info("BookingsSummary: Cache miss — running synchronous refresh.")
        refresh_bookings_summary(cad_rate)
        mtd = cache.get(_cache_key('mtd'))
        qtd = cache.get(_cache_key('qtd'))
        ytd = cache.get(_cache_key('ytd'))
        updated = cache.get(CACHE_KEY_UPDATED)

    date_ranges = _get_date_ranges()

    return {
        'mtd': mtd or _empty_result(),
        'qtd': qtd or _empty_result(),
        'ytd': ytd or _empty_result(),
        'last_updated': updated,
        'date_ranges': {
            h: {
                'start': date_ranges[h]['start'].isoformat(),
                'end': date_ranges[h]['end'].isoformat(),
                'prior_start': date_ranges[h]['prior_start'].isoformat(),
                'prior_end': date_ranges[h]['prior_end'].isoformat(),
                'label': date_ranges[h]['label'],
            }
            for h in HORIZONS
        },
    }


def refresh_bookings_summary_scheduled():
    from services.health_monitor import report_success, report_failure
    try:
        refresh_bookings_summary()
        report_success('bookings_summary')
    except Exception as e:
        logger.error(f"BookingsSummary: Scheduled refresh failed: {e}")
        report_failure('bookings_summary', str(e))