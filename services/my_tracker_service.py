"""
My Sales Tracker Service
Fetches per-salesman monthly shipments data with margin analysis.

Data source: artran (current month) / arytrn (historical months)
Key columns:
  - extprice: invoice line amount (sales)
  - cost: unit cost per line
  - qtyshp: quantity shipped
  - invdte: invoice date
  - salesmn: salesman code
  - plinid: product line (via icitem join)

Margin formula: extprice - (cost × qtyshp) per line item
"""

import logging
import math
from datetime import date, datetime
from calendar import monthrange
from collections import defaultdict

from config import Config
from extensions import cache
from services.db_connection import get_connection
from services.constants import TRACKER_EXCLUDED_CUSTOMERS, map_territory, map_product_line

logger = logging.getLogger(__name__)

TRACKER_YEARS_BACK = 3  # How many years back the month selector goes
SALESMEN_CACHE_TIMEOUT = 900   # 15 minutes
TRACKER_DATA_CACHE_TTL = 3600  # 60 minutes — per-salesman data cache
LEADERBOARD_CACHE_TTL = 900   # 15 minutes — shared leaderboard (same for all users)


def _financial_round(value):
    """Round financial value away from zero (ceil for positive, floor for negative)."""
    if value >= 0:
        return math.ceil(value)
    return math.floor(value)


# ─────────────────────────────────────────────────────────────
# Salesman list
# ─────────────────────────────────────────────────────────────

def get_salesmen_list(year, month, region='US'):
    """
    Get distinct salesman codes that have shipments in the given month.
    Queries a single region (US → PRO05, CA → PRO06) based on the region parameter.
    Results are cached for 15 minutes to avoid repeated scans.
    Returns a sorted list of salesman code strings.
    """
    cache_key = f'tracker_salesmen_{region}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    db = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    start_date = f'{year}-{month:02d}-01'
    _, last_day = monthrange(year, month)
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    all_salesmen = set()

    query = f"""
    SELECT DISTINCT tr.salesmn
    FROM {db}.dbo.{table} tr WITH (NOLOCK)
    WHERE tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
      AND tr.salesmn IS NOT NULL
      AND LTRIM(RTRIM(tr.salesmn)) <> ''
    """
    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query, start_date, end_date)
        for row in cursor.fetchall():
            code = (row[0] or '').strip()
            if code:
                all_salesmen.add(code)
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"MyTracker: Error fetching salesmen from {db}: {e}")

    result = sorted(all_salesmen)
    cache.set(cache_key, result, timeout=SALESMEN_CACHE_TIMEOUT)
    logger.info(f"MyTracker: Cached {len(result)} salesmen for {region} {year}-{month:02d}")
    return result


# ─────────────────────────────────────────────────────────────
# Main tracker data
# ─────────────────────────────────────────────────────────────

def get_tracker_data(salesman, year, month, region='US'):
    """
    Fetch and aggregate tracker data for a specific salesman and month.
    Queries a single region (US or CA) based on the region parameter.
    All amounts are in the region's native currency (USD for US, CAD for CA).

    Results are cached for TRACKER_DATA_CACHE_TTL (60 min) to reduce SQL load.
    Each cached entry includes a 'fetched_at' timestamp so the UI can show
    when the data was actually pulled from ERP.

    Returns dict with:
      - total_sales, total_margin, margin_pct
      - by_product_line: [{name, amount, margin, pct}, ...]
      - by_day: [{day, date_str, sales, margin, margin_pct}, ...]
      - total_invoices, total_units
      - top_customers: [{custno, name, amount, margin, margin_pct}, ...]
      - fetched_at: datetime when data was fetched from SQL
    """
    cache_key = f'tracker_data_{region}_{salesman}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None and 'fetched_at' in cached:
        logger.info(
            f"MyTracker: Cache HIT for {salesman} {year}-{month:02d} {region} "
            f"(fetched {cached['fetched_at']})"
        )
        return cached

    db = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    rows = _fetch_region(salesman, year, month, db, region)

    total = sum(r['amount'] for r in rows)
    logger.info(
        f"MyTracker: {salesman} {year}-{month:02d} {region} | "
        f"{len(rows)} rows, ${total:,.0f}"
    )

    result = _aggregate_tracker(rows, year, month, region)
    result['fetched_at'] = datetime.now()

    cache.set(cache_key, result, timeout=TRACKER_DATA_CACHE_TTL)
    logger.info(f"MyTracker: Cached data for {salesman} {year}-{month:02d} {region} (TTL {TRACKER_DATA_CACHE_TTL}s)")

    return result


def _fetch_region(salesman, year, month, database, region):
    """Fetch processed rows for a single region."""
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    start_date = f'{year}-{month:02d}-01'
    _, last_day = monthrange(year, month)
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    query = f"""
    SELECT
        tr.invno,
        tr.sono,
        tr.item,
        tr.qtyshp,
        tr.extprice,
        tr.cost,
        tr.invdte,
        tr.terr,
        tr.custno,
        tr.salesmn,
        cu.company        AS cust_name,
        ic.plinid         AS product_line
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.salesmn = ?
      AND tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
    """

    rows = []
    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query, salesman, start_date, end_date)

        for invno, sono, item, qtyshp, extprice, cost, invdte, terr, custno, salesmn, cust_name, plinid in cursor.fetchall():
            custno_clean = (custno or '').strip().upper()
            if custno_clean in TRACKER_EXCLUDED_CUSTOMERS:
                continue

            amt = float(extprice or 0)
            unit_cost = float(cost or 0)
            qty = float(qtyshp or 0)
            margin = amt - (unit_cost * qty)

            rows.append({
                'invno': (invno or '').strip(),
                'sono': (sono or '').strip(),
                'item': (item or '').strip(),
                'qtyshp': qty,
                'amount': amt,
                'margin': margin,
                'invdte': invdte,
                'product_line': map_product_line(plinid),
                'custno': custno_clean,
                'cust_name': (cust_name or '').strip() or custno_clean,
                'terr_code': (terr or '').strip(),
            })

        cursor.close()
        conn.close()
        logger.info(f"MyTracker: Fetched {len(rows)} rows from {database} {table} for {salesman} {year}-{month:02d}")
    except Exception as e:
        logger.error(f"MyTracker: Error fetching {region} data: {e}")

    return rows


def _aggregate_tracker(rows, year, month, region='US'):
    """
    Aggregate raw rows into tracker summary.
    Returns dict with KPIs, product line breakdown, daily trend, and primary territory.
    """
    total_sales = 0.0
    total_margin = 0.0
    total_units = 0
    invoices = set()

    product_totals = defaultdict(lambda: {'amount': 0.0, 'margin': 0.0})
    day_totals = defaultdict(lambda: {'sales': 0.0, 'margin': 0.0})
    cust_totals = defaultdict(lambda: {'name': '', 'amount': 0.0, 'margin': 0.0})
    terr_counts = defaultdict(int)  # territory code → invoice line count

    for row in rows:
        amt = row['amount']
        mgn = row['margin']

        total_sales += amt
        total_margin += mgn
        total_units += row['qtyshp']
        invoices.add(row['invno'])

        # Product line breakdown
        pl = row['product_line']
        product_totals[pl]['amount'] += amt
        product_totals[pl]['margin'] += mgn

        # Daily breakdown
        if row['invdte']:
            day = row['invdte'].day if hasattr(row['invdte'], 'day') else int(str(row['invdte']).split('-')[2][:2])
            day_totals[day]['sales'] += amt
            day_totals[day]['margin'] += mgn

        # Customer breakdown
        cno = row['custno']
        if cno:
            cust_totals[cno]['name'] = row['cust_name']
            cust_totals[cno]['amount'] += amt
            cust_totals[cno]['margin'] += mgn

        # Territory tracking (for goal integration)
        tc = row.get('terr_code', '')
        if tc:
            terr_counts[tc] += 1

    # Margin %
    margin_pct = (total_margin / total_sales * 100) if total_sales != 0 else 0.0

    # Product line list sorted by amount desc
    by_product_line = []
    for name, totals in sorted(product_totals.items(), key=lambda x: abs(x[1]['amount']), reverse=True):
        pct = (totals['amount'] / total_sales * 100) if total_sales != 0 else 0.0
        by_product_line.append({
            'name': name,
            'amount': _financial_round(totals['amount']),
            'margin': _financial_round(totals['margin']),
            'pct': round(pct, 2),
        })

    # Daily trend sorted by day
    _, last_day = monthrange(year, month)
    today = date.today()
    # Only include days up to today if current month, else all days
    max_day = today.day if _is_current_month(year, month) else last_day

    by_day = []
    for d in range(1, max_day + 1):
        dt = day_totals.get(d, {'sales': 0.0, 'margin': 0.0})
        day_sales = dt['sales']
        day_margin = dt['margin']
        day_margin_pct = (day_margin / day_sales * 100) if day_sales != 0 else 0.0

        by_day.append({
            'day': d,
            'date_str': f"{_month_abbr(month)} {d}",
            'sales': round(day_sales, 2),
            'margin': round(day_margin, 2),
            'margin_pct': round(day_margin_pct, 1),
        })

    # All customers sorted by amount desc (for win-back comparisons)
    all_customers_sorted = sorted(cust_totals.items(),
                                  key=lambda x: abs(x[1]['amount']),
                                  reverse=True)

    # Top 10 customers by sales amount
    top_customers = []
    for cno, ct in all_customers_sorted[:10]:
        mgn_pct = (ct['margin'] / ct['amount'] * 100) if ct['amount'] != 0 else 0.0
        top_customers.append({
            'custno': cno,
            'name': ct['name'] or cno,
            'amount': _financial_round(ct['amount']),
            'margin': _financial_round(ct['margin']),
            'margin_pct': round(mgn_pct, 1),
        })

    # Full customer list (used by win-back opportunity comparison)
    all_customers = []
    for cno, ct in all_customers_sorted:
        all_customers.append({
            'custno': cno,
            'name': ct['name'] or cno,
            'amount': _financial_round(ct['amount']),
        })

    # Determine primary territory (most common territory code)
    primary_terr_code = ''
    primary_territory = ''
    if terr_counts:
        primary_terr_code = max(terr_counts, key=terr_counts.get)
        primary_territory = map_territory(primary_terr_code, region)

    return {
        'total_sales': _financial_round(total_sales),
        'total_margin': _financial_round(total_margin),
        'margin_pct': round(margin_pct, 1),
        'total_units': round(total_units, 2),
        'total_invoices': len(invoices),
        'by_product_line': by_product_line,
        'by_day': by_day,
        'top_customers': top_customers,
        'all_customers': all_customers,
        'primary_territory': primary_territory,
        'primary_terr_code': primary_terr_code,
    }


# ─────────────────────────────────────────────────────────────
# Raw export for Excel download
# ─────────────────────────────────────────────────────────────

def fetch_raw_tracker_export(salesman, year, month, region='US'):
    """
    Fetch raw line-item data for Excel export.
    Queries a single region (US or CA) based on the region parameter.
    Returns list of dicts with 26+ columns.
    """
    db = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    start_date = f'{year}-{month:02d}-01'
    _, last_day = monthrange(year, month)
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    all_rows = []

    query = f"""
    SELECT
        tr.invno             AS InvoiceNo,
        tr.sono              AS SalesOrder,
        tr.tranlineno        AS [LineNo],
        tr.invdte            AS InvoiceDate,
        tr.custno            AS CustomerNo,
        cu.company           AS CustomerName,
        tr.item              AS Item,
        tr.descrip           AS Description,
        ic.plinid            AS ProductLine,
        tr.qtyord            AS QtyOrdered,
        tr.qtyshp            AS QtyShipped,
        tr.price             AS UnitPrice,
        tr.disc              AS Discount,
        tr.extprice          AS ExtPrice,
        tr.cost              AS UnitCost,
        tr.arstat            AS InvoiceStatus,
        tr.artype            AS InvoiceType,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE tr.terr
        END                  AS TerrCode,
        tr.terr              AS TranTerr,
        cu.terr              AS CustTerr,
        tr.salesmn           AS Salesman,
        tr.loctid            AS Location,
        tr.ponum             AS PONumber,
        tr.batch             AS Batch,
        tr.currid            AS Currency,
        tr.exchrat           AS ExchangeRate
    FROM {db}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {db}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {db}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.salesmn = ?
      AND tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
    ORDER BY tr.invdte, tr.invno, tr.tranlineno
    """
    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query, salesman, start_date, end_date)
        columns = [col[0] for col in cursor.description]
        for row in cursor.fetchall():
            record = dict(zip(columns, row))
            custno_clean = (record.get('CustomerNo') or '').strip().upper()
            if custno_clean in TRACKER_EXCLUDED_CUSTOMERS:
                continue

            terr_code = (record.get('TerrCode') or '').strip()
            record['Territory'] = map_territory(terr_code, region)
            record['Region'] = region
            all_rows.append(record)

        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"MyTracker export: Error fetching {region}: {e}")

    return all_rows


# ─────────────────────────────────────────────────────────────
# Leaderboard — all salesmen ranked by total invoiced
# ─────────────────────────────────────────────────────────────

def get_leaderboard_data(year, month, region='US'):
    """
    Get total invoiced amount per salesman for the given month/region.
    Shared across all users viewing the same month/region.

    Returns list of dicts sorted by total desc:
      [{'rank': 1, 'salesman': 'ABC', 'total': 150000}, ...]
    """
    cache_key = f'tracker_leaderboard_{region}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    database = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    _, last_day = monthrange(year, month)
    start_date = f'{year}-{month:02d}-01'
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    # Build excluded-customers placeholders
    excluded = list(TRACKER_EXCLUDED_CUSTOMERS)
    placeholders = ','.join(['?'] * len(excluded))

    query = f"""
    SELECT tr.salesmn, SUM(tr.extprice) AS total_invoiced
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    WHERE tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
      AND tr.salesmn IS NOT NULL
      AND LTRIM(RTRIM(tr.salesmn)) <> ''
      AND tr.custno NOT IN ({placeholders})
    GROUP BY tr.salesmn
    ORDER BY SUM(tr.extprice) DESC
    """
    params = [start_date, end_date] + excluded

    results = []
    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query, params)
        rank = 0
        for row in cursor.fetchall():
            code = (row[0] or '').strip()
            if not code:
                continue
            rank += 1
            results.append({
                'rank': rank,
                'salesman': code,
                'total': _financial_round(float(row[1] or 0)),
            })
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"MyTracker leaderboard: Error fetching {region} {year}-{month:02d}: {e}")

    cache.set(cache_key, results, timeout=LEADERBOARD_CACHE_TTL)
    logger.info(
        f"MyTracker: Cached leaderboard for {region} {year}-{month:02d} "
        f"({len(results)} salesmen)"
    )
    return results


# ─────────────────────────────────────────────────────────────
# Territory-wide invoiced total (for goal progress)
# ─────────────────────────────────────────────────────────────

TERRITORY_TOTAL_CACHE_TTL = 900  # 15 minutes

def get_territory_invoiced(territory_name, year, month, region='US'):
    """
    Get total invoiced amount for ALL salesmen in a territory for a given month.
    Used for territory goal progress (team effort, not individual).

    Args:
        territory_name: Portal display name (e.g. 'LA', 'Seattle')
        year, month: Target period
        region: 'US' or 'CA'

    Returns:
        int (rounded amount) or None on failure
    """
    cache_key = f'tracker_terr_total_{region}_{territory_name}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Build reverse map: territory display name → list of DB territory codes
    from services.constants import TERRITORY_MAP_US, TERRITORY_MAP_CA
    terr_map = TERRITORY_MAP_CA if region == 'CA' else TERRITORY_MAP_US
    codes = [code for code, name in terr_map.items() if name == territory_name]

    if not codes:
        logger.warning(f"MyTracker: No territory codes found for '{territory_name}' in {region}")
        return None

    database = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    _, last_day = monthrange(year, month)
    start_date = f'{year}-{month:02d}-01'
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    # Build excluded-customers and territory-codes placeholders
    excluded = list(TRACKER_EXCLUDED_CUSTOMERS)
    excl_ph = ','.join(['?'] * len(excluded))
    terr_ph = ','.join(['?'] * len(codes))

    query = f"""
    SELECT SUM(tr.extprice) AS total_invoiced
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    WHERE tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
      AND tr.terr IN ({terr_ph})
      AND tr.custno NOT IN ({excl_ph})
    """
    params = [start_date, end_date] + codes + excluded

    result = None
    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row and row[0] is not None:
            result = _financial_round(float(row[0]))
        else:
            result = 0
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"MyTracker: Error fetching territory total for {territory_name} {region} {year}-{month:02d}: {e}")
        return None

    cache.set(cache_key, result, timeout=TERRITORY_TOTAL_CACHE_TTL)
    logger.info(f"MyTracker: Territory total for {territory_name} {region} {year}-{month:02d}: ${result:,}")
    return result


def get_region_invoiced(region_key, year, month, region='US'):
    """
    Get total invoiced amount for ALL territories in a region for a given month.
    Used for region goal progress on the My Sales Tracker page.

    Args:
        region_key: Region key (e.g. 'WEST', 'SOUTHEAST', 'MIDWEST', 'NORTHEAST', 'CANADA')
        year, month: Target period
        region: 'US' or 'CA' (database selector)

    Returns:
        int (rounded amount) or None on failure
    """
    cache_key = f'tracker_region_total_{region}_{region_key}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    # Collect ALL territory codes that belong to this region
    from services.constants import TERRITORY_MAP_US, TERRITORY_MAP_CA, TERRITORY_TO_REGION
    terr_map = TERRITORY_MAP_CA if region == 'CA' else TERRITORY_MAP_US

    # Find all territory display names in this region
    region_territories = [name for name, rkey in TERRITORY_TO_REGION.items() if rkey == region_key]
    if not region_territories:
        logger.warning(f"MyTracker: No territories found for region '{region_key}'")
        return None

    # Collect all DB codes for those territory names
    codes = [code for code, name in terr_map.items() if name in region_territories]
    if not codes:
        logger.warning(f"MyTracker: No DB territory codes found for region '{region_key}' territories: {region_territories}")
        return None

    database = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    _, last_day = monthrange(year, month)
    start_date = f'{year}-{month:02d}-01'
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    excluded = list(TRACKER_EXCLUDED_CUSTOMERS)
    excl_ph = ','.join(['?'] * len(excluded))
    terr_ph = ','.join(['?'] * len(codes))

    query = f"""
    SELECT SUM(tr.extprice) AS total_invoiced
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    WHERE tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
      AND tr.terr IN ({terr_ph})
      AND tr.custno NOT IN ({excl_ph})
    """
    params = [start_date, end_date] + codes + excluded

    result = None
    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query, params)
        row = cursor.fetchone()
        if row and row[0] is not None:
            result = _financial_round(float(row[0]))
        else:
            result = 0
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"MyTracker: Error fetching region total for {region_key} {region} {year}-{month:02d}: {e}")
        return None

    cache.set(cache_key, result, timeout=TERRITORY_TOTAL_CACHE_TTL)
    logger.info(f"MyTracker: Region total for {region_key} {region} {year}-{month:02d}: ${result:,}")
    return result


# ─────────────────────────────────────────────────────────────
# Territory / Region daily invoiced — for cumulative chart
# ─────────────────────────────────────────────────────────────

def _daily_invoiced_by_codes(codes, year, month, region='US'):
    """
    Get daily invoiced totals for a set of territory DB codes.
    Returns list of floats indexed by day (0=day1, 1=day2, ...).
    """
    database = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    table = 'artran' if _is_current_month(year, month) else 'arytrn'
    _, last_day = monthrange(year, month)
    start_date = f'{year}-{month:02d}-01'
    end_date = f'{year}-{month:02d}-{last_day:02d}'

    excluded = list(TRACKER_EXCLUDED_CUSTOMERS)
    excl_ph = ','.join(['?'] * len(excluded))
    terr_ph = ','.join(['?'] * len(codes))

    query = f"""
    SELECT DAY(tr.invdte) AS inv_day, SUM(tr.extprice) AS daily_total
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    WHERE tr.invdte BETWEEN ? AND ?
      AND tr.currhist <> 'X'
      AND tr.terr IN ({terr_ph})
      AND tr.custno NOT IN ({excl_ph})
    GROUP BY DAY(tr.invdte)
    ORDER BY DAY(tr.invdte)
    """
    params = [start_date, end_date] + codes + excluded

    today = date.today()
    max_day = today.day if _is_current_month(year, month) else last_day

    daily = [0.0] * max_day
    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        cursor.execute(query, params)
        for row in cursor.fetchall():
            d = int(row[0])
            if 1 <= d <= max_day:
                daily[d - 1] = round(float(row[1]), 2)
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"MyTracker: Error fetching daily invoiced: {e}")
        return None

    return daily


def get_territory_daily_invoiced(territory_name, year, month, region='US'):
    """
    Get daily invoiced totals for all salesmen in a territory.
    Used for cumulative MTD chart (team-level territory line).
    Returns list of daily amounts or None.
    """
    cache_key = f'tracker_terr_daily_{region}_{territory_name}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from services.constants import TERRITORY_MAP_US, TERRITORY_MAP_CA
    terr_map = TERRITORY_MAP_CA if region == 'CA' else TERRITORY_MAP_US
    codes = [code for code, name in terr_map.items() if name == territory_name]
    if not codes:
        return None

    result = _daily_invoiced_by_codes(codes, year, month, region)
    if result is not None:
        cache.set(cache_key, result, timeout=TERRITORY_TOTAL_CACHE_TTL)
    return result


def get_region_daily_invoiced(region_key, year, month, region='US'):
    """
    Get daily invoiced totals for all territories in a region.
    Used for cumulative MTD chart (team-level region line).
    Returns list of daily amounts or None.
    """
    cache_key = f'tracker_region_daily_{region}_{region_key}_{year}_{month:02d}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from services.constants import TERRITORY_MAP_US, TERRITORY_MAP_CA, TERRITORY_TO_REGION
    terr_map = TERRITORY_MAP_CA if region == 'CA' else TERRITORY_MAP_US
    region_territories = [name for name, rkey in TERRITORY_TO_REGION.items() if rkey == region_key]
    codes = [code for code, name in terr_map.items() if name in region_territories]
    if not codes:
        return None

    result = _daily_invoiced_by_codes(codes, year, month, region)
    if result is not None:
        cache.set(cache_key, result, timeout=TERRITORY_TOTAL_CACHE_TTL)
    return result


# ─────────────────────────────────────────────────────────────
# Win-back opportunities — lapsed customers from last year
# ─────────────────────────────────────────────────────────────

def get_winback_customers(salesman, year, month, region='US'):
    """
    Identify customers who bought from this salesman in the same month
    last year but have NOT purchased this month yet.

    Uses cached get_tracker_data() calls — zero additional SQL.

    Returns list sorted by last year amount desc (biggest opportunities first):
      [{'custno': 'XYZ', 'name': 'Acme Corp', 'ly_amount': 50000}, ...]
    """
    # Current year customer set
    cy_data = get_tracker_data(salesman, year, month, region=region)
    cy_customers = set()
    if cy_data:
        for c in cy_data.get('all_customers', []):
            cy_customers.add(c['custno'])

    # Last year same month customer set
    ly_year = year - 1
    ly_data = get_tracker_data(salesman, ly_year, month, region=region)

    winback = []
    if ly_data:
        for c in ly_data.get('all_customers', []):
            if c['custno'] not in cy_customers and c['amount'] > 0:
                winback.append({
                    'custno': c['custno'],
                    'name': c['name'],
                    'ly_amount': c['amount'],
                })

    # Biggest opportunities first
    winback.sort(key=lambda x: abs(x['ly_amount']), reverse=True)
    return winback


# ─────────────────────────────────────────────────────────────
# Available months for the selector
# ─────────────────────────────────────────────────────────────

def get_available_months():
    """
    Returns list of (year, month, label) tuples going back TRACKER_YEARS_BACK years.
    Most recent first.
    """
    today = date.today()
    months = []

    for y in range(today.year, today.year - TRACKER_YEARS_BACK - 1, -1):
        start_m = today.month if y == today.year else 12
        end_m = 1
        for m in range(start_m, end_m - 1, -1):
            label = f"{_month_name(m)} {y}"
            months.append((y, m, label))

    return months


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _is_current_month(year, month):
    """Check if the given year/month is the current month."""
    today = date.today()
    return year == today.year and month == today.month


def _month_abbr(month):
    """Return 3-letter month abbreviation."""
    names = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
             'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    return names[month] if 1 <= month <= 12 else ''


def _month_name(month):
    """Return full month name."""
    names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
             'July', 'August', 'September', 'October', 'November', 'December']
    return names[month] if 1 <= month <= 12 else ''
