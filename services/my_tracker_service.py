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
SALESMEN_CACHE_TIMEOUT = 900  # 15 minutes


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

    Returns dict with:
      - total_sales, total_margin, margin_pct
      - by_product_line: [{name, amount, margin, pct}, ...]
      - by_day: [{day, date_str, sales, margin, margin_pct}, ...]
      - total_invoices, total_units
    """
    db = Config.DB_ORDERS_CA if region == 'CA' else Config.DB_ORDERS
    rows = _fetch_region(salesman, year, month, db, region)

    total = sum(r['amount'] for r in rows)
    logger.info(
        f"MyTracker: {salesman} {year}-{month:02d} {region} | "
        f"{len(rows)} rows, ${total:,.0f}"
    )

    return _aggregate_tracker(rows, year, month)


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
            })

        cursor.close()
        conn.close()
        logger.info(f"MyTracker: Fetched {len(rows)} rows from {database} {table} for {salesman} {year}-{month:02d}")
    except Exception as e:
        logger.error(f"MyTracker: Error fetching {region} data: {e}")

    return rows


def _aggregate_tracker(rows, year, month):
    """
    Aggregate raw rows into tracker summary.
    Returns dict with KPIs, product line breakdown, and daily trend.
    """
    total_sales = 0.0
    total_margin = 0.0
    total_units = 0
    invoices = set()

    product_totals = defaultdict(lambda: {'amount': 0.0, 'margin': 0.0})
    day_totals = defaultdict(lambda: {'sales': 0.0, 'margin': 0.0})

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

    return {
        'total_sales': _financial_round(total_sales),
        'total_margin': _financial_round(total_margin),
        'margin_pct': round(margin_pct, 1),
        'total_units': round(total_units, 2),
        'total_invoices': len(invoices),
        'by_product_line': by_product_line,
        'by_day': by_day,
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
