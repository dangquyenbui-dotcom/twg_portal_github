"""
Dashboard Data Service
Fetches and caches bookings data for the executive dashboard.

Data priority (fastest to slowest):
  1. Frozen disk files (dashboard_data/*.json.gz) - for completed years, <1ms read
  2. In-memory cache (Flask-Caching) - for current year, sub-millisecond
  3. SQL Server (soytrn + sotran) - on-demand fetch, ~15-20 seconds for a full year

Storage: dashboard_data/{region}_{year}.json.gz  (e.g., us_2025.json.gz)
Each file contains:
  - meta: region, year, frozen_at, version, row_count
  - raw_rows: list of dicts (same 26 columns as bookings Excel export)
  - data: pre-aggregated summary dict for instant dashboard rendering

The raw rows enable Excel exports directly from disk — zero SQL after the initial
one-time download. The hosting server does all the heavy lifting (aggregation,
compression) so the SQL Server is only queried once per region per year.
"""

import gzip
import json
import logging
import math
import os
from datetime import date, datetime
from collections import defaultdict
from pathlib import Path

from config import Config
from services.db_connection import get_connection
from services.constants import BOOKINGS_EXCLUDED_CUSTOMERS, map_territory
from extensions import cache

logger = logging.getLogger(__name__)

DASHBOARD_DATA_DIR = Path(__file__).resolve().parent.parent / 'dashboard_data'
CACHE_KEY_DASH_UPDATED = "dashboard_last_updated"
DASH_HIST_TIMEOUT = 86400       # 24 hours for historical year cache
DASH_CURRENT_TIMEOUT = 3900     # 65 min for current month cache
DASHBOARD_YEARS_BACK = 7


# ─────────────────────────────────────────────────────────────
# Cache key helpers
# ─────────────────────────────────────────────────────────────

def _cache_key_hist(region, year):
    """Cache key for historical year summary (dashboard rendering)."""
    return f"dash_hist_{region.lower()}_{year}"

def _cache_key_hist_raw(region, year):
    """Cache key for historical year raw rows (Excel export)."""
    return f"dash_hist_raw_{region.lower()}_{year}"

def _cache_key_current(region):
    """Cache key for current month summary."""
    return f"dash_current_{region.lower()}"

def _frozen_file_path(region, year):
    """Path to the frozen gzip JSON file for a region/year."""
    return DASHBOARD_DATA_DIR / f"{region.lower()}_{year}.json.gz"

def _ensure_data_dir():
    """Create the dashboard_data directory if it doesn't exist."""
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# Frozen File I/O
# ─────────────────────────────────────────────────────────────

def save_frozen_data(region, year, summary_dict, raw_rows=None):
    """
    Save frozen data to disk as gzip-compressed JSON.

    The file contains:
      - meta: region, year, frozen_at, version, row_count
      - data: pre-aggregated summary dict (for instant dashboard rendering)
      - raw_rows: list of dicts with 26 columns (for Excel export)

    Version 3 files include raw_rows. Version 2 files (legacy) do not.
    """
    _ensure_data_dir()
    filepath = _frozen_file_path(region, year)

    data_with_meta = {
        'meta': {
            'region': region,
            'year': year,
            'frozen_at': datetime.now().isoformat(),
            'version': 3,
            'row_count': len(raw_rows) if raw_rows else 0,
        },
        'data': summary_dict,
    }

    if raw_rows is not None:
        data_with_meta['raw_rows'] = raw_rows

    with gzip.open(filepath, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump(data_with_meta, f, separators=(',', ':'), default=str)

    file_size = filepath.stat().st_size
    row_info = f", {len(raw_rows):,} raw rows" if raw_rows else ""
    logger.info(f"Dashboard: Saved frozen data {filepath.name} ({file_size:,} bytes{row_info})")
    return file_size


def load_frozen_data(region, year):
    """
    Load frozen data from disk.
    Returns the summary dict (for dashboard rendering), or None if file doesn't exist.
    """
    filepath = _frozen_file_path(region, year)
    if not filepath.exists():
        return None
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            wrapper = json.load(f)
        return wrapper.get('data')
    except Exception as e:
        logger.error(f"Dashboard: Failed to load frozen file {filepath.name}: {e}")
        return None


def load_frozen_raw_rows(region, year):
    """
    Load raw rows from frozen file for Excel export.
    Returns list of dicts (26 columns each), or None if file doesn't exist
    or doesn't contain raw rows (legacy v2 file).
    """
    filepath = _frozen_file_path(region, year)
    if not filepath.exists():
        return None
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            wrapper = json.load(f)

        version = wrapper.get('meta', {}).get('version', 2)
        if version < 3:
            logger.info(f"Dashboard: Frozen file {filepath.name} is v{version} (no raw rows). "
                        f"Re-download via admin page to get raw data.")
            return None

        raw_rows = wrapper.get('raw_rows')
        if raw_rows:
            logger.info(f"Dashboard: Loaded {len(raw_rows):,} raw rows from {filepath.name}")
        return raw_rows

    except Exception as e:
        logger.error(f"Dashboard: Failed to load raw rows from {filepath.name}: {e}")
        return None


def delete_frozen_data(region, year):
    """Delete the frozen file for a region/year."""
    filepath = _frozen_file_path(region, year)
    if filepath.exists():
        filepath.unlink()
        logger.info(f"Dashboard: Deleted frozen data {filepath.name}")
        return True
    return False


def get_frozen_status():
    """
    Get the status of all frozen files for the admin page.
    Returns a list of dicts with year, region, exists, file_size, frozen_at, version, row_count.
    """
    _ensure_data_dir()
    current_year = date.today().year
    start_year = current_year - DASHBOARD_YEARS_BACK + 1
    statuses = []

    for year in range(current_year, start_year - 1, -1):
        for region in ('US', 'CA'):
            filepath = _frozen_file_path(region, year)
            entry = {
                'year': year,
                'region': region,
                'is_current_year': year == current_year,
                'exists': False,
                'file_size': 0,
                'frozen_at': None,
                'filename': filepath.name,
                'version': 0,
                'row_count': 0,
                'has_raw_rows': False,
            }

            if filepath.exists():
                entry['exists'] = True
                entry['file_size'] = filepath.stat().st_size
                try:
                    with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                        wrapper = json.load(f)
                    meta = wrapper.get('meta', {})
                    entry['frozen_at'] = meta.get('frozen_at')
                    entry['version'] = meta.get('version', 2)
                    entry['row_count'] = meta.get('row_count', 0)
                    entry['has_raw_rows'] = entry['version'] >= 3 and entry['row_count'] > 0
                except Exception:
                    pass

            statuses.append(entry)

    return statuses


# ─────────────────────────────────────────────────────────────
# SQL Queries — Dashboard Aggregation (for summary)
# ─────────────────────────────────────────────────────────────

def _build_dashboard_query(database, table, year=None, current_month_only=False):
    """
    Build the dashboard summary query (lean columns for aggregation).
    Used for current month data and as a fallback for historical years
    when no frozen file exists.
    """
    date_filter = ""
    if year and not current_month_only:
        date_filter = (f"\n      AND tr.ordate >= '{year}-01-01'"
                       f"\n      AND tr.ordate < '{year + 1}-01-01'")

    return f"""
    SELECT
        tr.sono,
        tr.origqtyord AS units,
        tr.origqtyord * tr.price * (1 - tr.disc / 100.0) AS amount,
        tr.ordate,
        CASE WHEN cu.terr = '900' THEN cu.terr ELSE sm.terr END AS terr_code,
        tr.custno, cu.company AS cust_name, tr.salesmn, ic.plinid
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.currhist <> 'X'
      AND tr.sostat NOT IN ('V', 'X')
      AND tr.sotype NOT IN ('B', 'R'){date_filter}
    """


# ─────────────────────────────────────────────────────────────
# SQL Queries — Raw Data Download (full 26 columns for export)
# ─────────────────────────────────────────────────────────────

def _build_raw_download_query(database, table, year):
    """
    Build the raw data download query — same 26 columns as the bookings Excel export.
    This is used during admin download to pull ALL line items for a year from soytrn.
    One single query, one pass, then we disconnect from SQL Server forever for this year.
    """
    next_year = year + 1
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
    WHERE tr.currhist <> 'X'
      AND tr.sostat NOT IN ('V', 'X')
      AND tr.sotype NOT IN ('B', 'R')
      AND tr.ordate >= '{year}-01-01'
      AND tr.ordate < '{next_year}-01-01'
    """


def _process_raw_download_rows(cursor, rows, region='US'):
    """
    Process raw SQL rows into a list of dicts with territory mapping and customer filtering.
    This runs on the hosting server — all the heavy lifting happens here, not on SQL Server.

    Returns a list of clean dicts ready for JSON serialization and Excel export.
    """
    columns = [col[0] for col in cursor.description]
    results = []

    for row in rows:
        record = dict(zip(columns, row))

        # Exclude internal/test customers
        custno_clean = (record.get('CustomerNo') or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue

        # Exclude TAX line items
        if (record.get('ProductLine') or '').strip().upper() == 'TAX':
            continue

        # Map territory code to display name
        terr_code = (record.get('TerrCode') or '').strip()
        record['Territory'] = map_territory(terr_code, region)

        # Clean up string fields (strip whitespace)
        for key in ('CustomerNo', 'CustomerName', 'Item', 'Description',
                    'ProductLine', 'Salesman', 'Location', 'ShipVia',
                    'SalesOrder', 'LineStatus', 'OrderType', 'CurrHist',
                    'TranTerr', 'SOMastTerr', 'CustTerr', 'TerrCode'):
            if record.get(key):
                record[key] = str(record[key]).strip()

        # Convert date objects to ISO strings for JSON serialization
        for key in ('OrderDate', 'RequestDate', 'ShipDate'):
            val = record.get(key)
            if val is not None and hasattr(val, 'isoformat'):
                record[key] = val.isoformat()

        # Convert Decimal types to float for JSON serialization
        for key in ('UnitPrice', 'Discount', 'ExtAmount', 'ExtPrice'):
            val = record.get(key)
            if val is not None:
                record[key] = float(val)

        # Convert integer fields
        for key in ('QtyOrdered', 'QtyShipped', 'LineNo'):
            val = record.get(key)
            if val is not None:
                try:
                    record[key] = int(val)
                except (ValueError, TypeError):
                    pass

        results.append(record)

    return results


# ─────────────────────────────────────────────────────────────
# Aggregation — from raw rows (Python does all the work)
# ─────────────────────────────────────────────────────────────

def _aggregate_rows(rows, region='US'):
    """
    Aggregate raw query result rows into dashboard summary.
    Takes tuples from the lean dashboard query (9 columns).

    Returns dict with: summary, monthly_totals, by_territory,
    by_salesman, by_product_line, by_customer
    """
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

    summary = {
        'total_amount': math.ceil(total_amount),
        'total_units': total_units,
        'total_orders': len(distinct_orders),
        'total_lines': total_lines,
    }

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

    by_territory = _build_ranked(terr_data)
    by_salesman = _build_ranked(sm_data)

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
        'summary': summary,
        'monthly_totals': monthly_totals,
        'by_territory': by_territory,
        'by_salesman': by_salesman,
        'by_product_line': by_product_line,
        'by_customer': by_customer,
    }


def _aggregate_from_raw_dicts(raw_rows, region='US'):
    """
    Aggregate from raw row dicts (as stored in frozen files).
    This is used when we have raw rows on disk and need to build a summary
    without touching SQL Server.

    The raw_rows are already filtered (no excluded customers, no TAX).
    Each dict has keys like SalesOrder, QtyOrdered, ExtAmount, Territory, etc.
    """
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_orders = set()
    monthly = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    terr_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    sm_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    pl_data = defaultdict(lambda: {'amount': 0.0, 'units': 0})
    cust_data = defaultdict(lambda: {'name': '', 'amount': 0.0, 'units': 0, 'orders': set()})

    for row in raw_rows:
        sono = row.get('SalesOrder')
        amt = float(row.get('ExtAmount') or 0)
        qty = int(row.get('QtyOrdered') or 0)
        territory = row.get('Territory', 'Others')
        salesman = (row.get('Salesman') or '').strip() or 'Unassigned'
        product_line = (row.get('ProductLine') or '').strip() or 'Other'
        custno = (row.get('CustomerNo') or '').strip().upper()
        cust_name = (row.get('CustomerName') or '').strip() or custno
        ordate_str = row.get('OrderDate', '')

        # Parse date for monthly grouping
        try:
            if isinstance(ordate_str, str) and len(ordate_str) >= 10:
                yr = int(ordate_str[:4])
                mo = int(ordate_str[5:7])
            else:
                continue
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
        cust_data[custno]['name'] = cust_name
        cust_data[custno]['amount'] += amt
        cust_data[custno]['units'] += qty
        cust_data[custno]['orders'].add(sono)

    summary = {
        'total_amount': math.ceil(total_amount),
        'total_units': total_units,
        'total_orders': len(distinct_orders),
        'total_lines': total_lines,
    }

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

    by_territory = _build_ranked(terr_data)
    by_salesman = _build_ranked(sm_data)

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
        'summary': summary,
        'monthly_totals': monthly_totals,
        'by_territory': by_territory,
        'by_salesman': by_salesman,
        'by_product_line': by_product_line,
        'by_customer': by_customer,
    }


# ─────────────────────────────────────────────────────────────
# Merge summaries (historical + current month)
# ─────────────────────────────────────────────────────────────

def _merge_summaries(hist_summary, current_summary):
    """Merge historical (soytrn) and current month (sotran) summaries."""
    if hist_summary is None and current_summary is None:
        return None
    if hist_summary is None:
        return current_summary
    if current_summary is None:
        return hist_summary

    all_monthly = hist_summary['monthly_totals'] + current_summary['monthly_totals']
    all_monthly.sort(key=lambda x: (x['yr'], x['mo']))
    total_lines = hist_summary['summary']['total_lines'] + current_summary['summary']['total_lines']

    def _merge_ranked(h, c, key_field):
        merged = defaultdict(lambda: {'amount': 0, 'units': 0, 'orders': 0})
        for item in h + c:
            k = item[key_field]
            merged[k]['amount'] += item['amount']
            merged[k]['units'] += item['units']
            merged[k]['orders'] += item.get('orders', 0)
            if 'name' in item and key_field != 'name':
                merged[k]['name'] = item['name']
            if 'custno' in item:
                merged[k]['custno'] = item['custno']
        result = sorted([{key_field: k, **v} for k, v in merged.items()],
                        key=lambda x: x['amount'], reverse=True)
        for i, r in enumerate(result):
            r['rank'] = i + 1
        return result

    by_territory = _merge_ranked(
        hist_summary['by_territory'], current_summary['by_territory'], 'name')
    by_salesman = _merge_ranked(
        hist_summary['by_salesman'], current_summary['by_salesman'], 'name')
    by_product_line = _merge_ranked(
        hist_summary['by_product_line'], current_summary['by_product_line'], 'name')

    cust_merged = defaultdict(lambda: {'name': '', 'custno': '', 'amount': 0, 'units': 0, 'orders': 0})
    for item in hist_summary['by_customer'] + current_summary['by_customer']:
        k = item['custno']
        cust_merged[k]['name'] = item['name']
        cust_merged[k]['custno'] = k
        cust_merged[k]['amount'] += item['amount']
        cust_merged[k]['units'] += item['units']
        cust_merged[k]['orders'] += item.get('orders', 0)
    by_customer = sorted(cust_merged.values(), key=lambda x: x['amount'], reverse=True)[:50]
    for i, c in enumerate(by_customer):
        c['rank'] = i + 1

    total_amount = sum(m['amount'] for m in all_monthly)
    total_units = sum(m['units'] for m in all_monthly)
    total_orders = sum(m['orders'] for m in all_monthly)

    return {
        'summary': {
            'total_amount': math.ceil(total_amount),
            'total_units': total_units,
            'total_orders': total_orders,
            'total_lines': total_lines,
        },
        'monthly_totals': all_monthly,
        'by_territory': by_territory,
        'by_salesman': by_salesman,
        'by_product_line': by_product_line,
        'by_customer': by_customer,
    }


# ─────────────────────────────────────────────────────────────
# Fetch from SQL Server
# ─────────────────────────────────────────────────────────────

def _fetch_and_aggregate(database, table, region, year=None, current_month_only=False):
    """
    Fetch lean data from SQL and aggregate into dashboard summary.
    Used for current month and as a fallback for historical years
    when no frozen file exists.
    """
    query = _build_dashboard_query(database, table, year=year,
                                   current_month_only=current_month_only)
    label = f"{'US' if region == 'US' else 'CA'} {table}"
    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        logger.info(f"Dashboard: Fetching {label} year={year or 'current'}...")
        cursor.execute(query)
        rows = cursor.fetchall()
        row_count = len(rows)
        cursor.close()
        conn.close()
        logger.info(f"Dashboard: {label} fetched {row_count:,} raw rows. Aggregating...")
        summary = _aggregate_rows(rows, region=region)
        logger.info(
            f"Dashboard: {label} aggregated — "
            f"${summary['summary']['total_amount']:,} "
            f"across {len(summary['monthly_totals'])} months"
        )
        return summary
    except Exception as e:
        logger.error(f"Dashboard: {label} query failed: {e}")
        return None


def _fetch_raw_rows_from_sql(database, table, region, year):
    """
    Fetch full 26-column raw rows from SQL Server for a year.
    This is the ONE TIME we hit SQL Server for this region/year.
    All processing (filtering, mapping, type conversion) happens on the hosting server.

    Returns list of dicts, or None on failure.
    """
    query = _build_raw_download_query(database, table, year)
    label = f"{'US' if region == 'US' else 'CA'} {table} {year}"

    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        logger.info(f"Dashboard: Fetching RAW {label}... (this may take 15-30 seconds)")
        cursor.execute(query)
        rows = cursor.fetchall()
        raw_count = len(rows)
        logger.info(f"Dashboard: {label} fetched {raw_count:,} raw rows from SQL. "
                     f"Processing on hosting server...")

        results = _process_raw_download_rows(cursor, rows, region=region)
        cursor.close()
        conn.close()

        logger.info(f"Dashboard: {label} processed → {len(results):,} rows "
                     f"(filtered from {raw_count:,})")
        return results

    except Exception as e:
        logger.error(f"Dashboard: RAW {label} query failed: {e}")
        return None


def fetch_historical_year(year, region='US'):
    """Fetch historical year summary via lean query (fallback when no frozen file)."""
    db = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA
    return _fetch_and_aggregate(db, 'soytrn', region, year=year)


def fetch_current_month(region='US'):
    """Fetch current month summary from sotran."""
    db = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA
    return _fetch_and_aggregate(db, 'sotran', region, current_month_only=True)


# ─────────────────────────────────────────────────────────────
# Admin: Download a year (raw rows + summary)
# ─────────────────────────────────────────────────────────────

def download_year_data(year, region='US'):
    """
    Admin action: Download a full year of data from SQL Server.

    1. Fetch all raw rows (26 columns) from soytrn — ONE SQL query
    2. Aggregate into dashboard summary on the hosting server
    3. Save BOTH raw rows + summary to a gzip JSON file on disk
    4. Cache the summary for immediate dashboard use

    After this, SQL Server is never queried again for this region/year.
    """
    logger.info(f"Dashboard Admin: Downloading {region} {year} (raw + summary)...")

    db = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA

    # Step 1: Fetch all raw rows from SQL (one query, one pass)
    raw_rows = _fetch_raw_rows_from_sql(db, 'soytrn', region, year)
    if raw_rows is None:
        raise RuntimeError(f"Failed to fetch raw data for {region} {year} from SQL Server")

    # Step 2: Aggregate on the hosting server (Python does the heavy lifting)
    logger.info(f"Dashboard Admin: Aggregating {len(raw_rows):,} rows for {region} {year}...")
    summary = _aggregate_from_raw_dicts(raw_rows, region=region)

    # Step 3: Save both raw rows + summary to disk
    file_size = save_frozen_data(region, year, summary, raw_rows=raw_rows)

    # Step 4: Cache the summary for immediate dashboard use
    cache.set(_cache_key_hist(region, year), summary, timeout=DASH_HIST_TIMEOUT)

    logger.info(
        f"Dashboard Admin: {region} {year} complete — "
        f"${summary['summary']['total_amount']:,}, "
        f"{len(summary['monthly_totals'])} months, "
        f"{len(raw_rows):,} raw rows, "
        f"{file_size:,} bytes on disk"
    )

    return {
        'region': region,
        'year': year,
        'total_amount': summary['summary']['total_amount'],
        'total_orders': summary['summary']['total_orders'],
        'total_lines': summary['summary']['total_lines'],
        'months': len(summary['monthly_totals']),
        'row_count': len(raw_rows),
        'file_size': file_size,
    }


# ─────────────────────────────────────────────────────────────
# Data Resolution: Disk → Cache → SQL
# ─────────────────────────────────────────────────────────────

def _get_historical_data(year, region):
    """
    Get historical year summary for dashboard rendering.
    Resolution: frozen file → cache → SQL (lean query as fallback).
    """
    frozen = load_frozen_data(region, year)
    if frozen is not None:
        return frozen

    cache_key = _cache_key_hist(region, year)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    logger.info(f"Dashboard: No frozen/cache for {region} {year} — fetching SQL...")
    summary = fetch_historical_year(year, region)
    if summary is not None:
        cache.set(cache_key, summary, timeout=DASH_HIST_TIMEOUT)
    return summary


def _get_current_month_data(region):
    """Get current month summary. Resolution: cache → SQL."""
    cache_key = _cache_key_current(region)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    summary = fetch_current_month(region)
    if summary is not None:
        cache.set(cache_key, summary, timeout=DASH_CURRENT_TIMEOUT)
        cache.set(CACHE_KEY_DASH_UPDATED, datetime.now(), timeout=DASH_CURRENT_TIMEOUT)
    return summary


def refresh_dashboard_current_month():
    """Scheduler job: refresh current month data from sotran every 60 min."""
    logger.info("Dashboard Worker: === Refreshing current month (sotran) ===")
    for region in ('US', 'CA'):
        summary = fetch_current_month(region)
        if summary is not None:
            cache.set(_cache_key_current(region), summary, timeout=DASH_CURRENT_TIMEOUT)
            logger.info(f"Dashboard Worker: {region} current month updated.")
    cache.set(CACHE_KEY_DASH_UPDATED, datetime.now(), timeout=DASH_CURRENT_TIMEOUT)
    logger.info("Dashboard Worker: === Current month refresh complete ===")


# ─────────────────────────────────────────────────────────────
# Historical Raw Rows for Excel Export
# ─────────────────────────────────────────────────────────────

def get_historical_raw_rows(year, region='US'):
    """
    Get raw rows for historical year Excel export.
    Reads from frozen file on disk — zero SQL queries.

    Returns list of dicts (26 columns each), or None if not available.
    If the frozen file is a legacy v2 file (no raw rows), returns None
    and the caller should show a message to re-download via admin page.
    """
    return load_frozen_raw_rows(region, year)


# ─────────────────────────────────────────────────────────────
# Public API — Dashboard Data
# ─────────────────────────────────────────────────────────────

def get_dashboard_data(year=None, cad_rate=None):
    """
    Get the full merged dashboard data for a given year.
    Merges US + CA, converts CA amounts to USD.
    Returns a dict ready for template rendering + Chart.js.
    """
    if year is None:
        year = date.today().year
    rate = cad_rate or 0.72
    current_year = date.today().year

    # Get US data
    us_hist = _get_historical_data(year, 'US')
    us_current = _get_current_month_data('US') if year == current_year else None
    us_merged = _merge_summaries(us_hist, us_current)

    # Get CA data
    ca_hist = _get_historical_data(year, 'CA')
    ca_current = _get_current_month_data('CA') if year == current_year else None
    ca_merged = _merge_summaries(ca_hist, ca_current)

    if us_merged is None and ca_merged is None:
        return _empty_dashboard(year)

    # Region amounts
    us_amount = us_merged['summary']['total_amount'] if us_merged else 0
    ca_amount = ca_merged['summary']['total_amount'] if ca_merged else 0
    ca_amount_usd = math.ceil(ca_amount * rate)

    # Merge monthly totals (CA converted to USD)
    monthly_merged = defaultdict(lambda: {'yr': 0, 'mo': 0, 'amount': 0, 'units': 0, 'orders': 0})
    if us_merged:
        for m in us_merged['monthly_totals']:
            k = (m['yr'], m['mo'])
            monthly_merged[k]['yr'] = m['yr']
            monthly_merged[k]['mo'] = m['mo']
            monthly_merged[k]['amount'] += m['amount']
            monthly_merged[k]['units'] += m['units']
            monthly_merged[k]['orders'] += m['orders']
    if ca_merged:
        for m in ca_merged['monthly_totals']:
            k = (m['yr'], m['mo'])
            monthly_merged[k]['yr'] = m['yr']
            monthly_merged[k]['mo'] = m['mo']
            monthly_merged[k]['amount'] += math.ceil(m['amount'] * rate)
            monthly_merged[k]['units'] += m['units']
            monthly_merged[k]['orders'] += m['orders']
    monthly_totals = sorted(monthly_merged.values(), key=lambda x: (x['yr'], x['mo']))

    # Merge dimension breakdowns
    def _merge_dim(us_list, ca_list, r):
        merged = defaultdict(lambda: {'amount': 0, 'units': 0, 'orders': 0})
        for item in (us_list or []):
            merged[item['name']]['amount'] += item['amount']
            merged[item['name']]['units'] += item['units']
            merged[item['name']]['orders'] += item.get('orders', 0)
        for item in (ca_list or []):
            merged[item['name']]['amount'] += math.ceil(item['amount'] * r)
            merged[item['name']]['units'] += item['units']
            merged[item['name']]['orders'] += item.get('orders', 0)
        result = sorted([{'name': k, **v} for k, v in merged.items()],
                        key=lambda x: x['amount'], reverse=True)
        for i, rr in enumerate(result):
            rr['rank'] = i + 1
        return result

    by_territory = _merge_dim(
        us_merged['by_territory'] if us_merged else [],
        ca_merged['by_territory'] if ca_merged else [], rate)
    by_salesman = _merge_dim(
        us_merged['by_salesman'] if us_merged else [],
        ca_merged['by_salesman'] if ca_merged else [], rate)
    by_product_line = _merge_dim(
        us_merged['by_product_line'] if us_merged else [],
        ca_merged['by_product_line'] if ca_merged else [], rate)

    # Merge customers
    cust_merged = defaultdict(lambda: {'name': '', 'custno': '', 'amount': 0, 'units': 0, 'orders': 0})
    for src, r in [(us_merged, 1.0), (ca_merged, rate)]:
        if src:
            for item in src['by_customer']:
                k = item['custno']
                cust_merged[k]['name'] = item['name']
                cust_merged[k]['custno'] = k
                cust_merged[k]['amount'] += math.ceil(item['amount'] * r)
                cust_merged[k]['units'] += item['units']
                cust_merged[k]['orders'] += item.get('orders', 0)
    by_customer = sorted(cust_merged.values(), key=lambda x: x['amount'], reverse=True)[:50]
    for i, c in enumerate(by_customer):
        c['rank'] = i + 1

    # Grand totals
    total_amount = us_amount + ca_amount_usd
    total_units = ((us_merged['summary']['total_units'] if us_merged else 0) +
                   (ca_merged['summary']['total_units'] if ca_merged else 0))
    total_orders = ((us_merged['summary']['total_orders'] if us_merged else 0) +
                    (ca_merged['summary']['total_orders'] if ca_merged else 0))
    total_lines = ((us_merged['summary']['total_lines'] if us_merged else 0) +
                   (ca_merged['summary']['total_lines'] if ca_merged else 0))
    avg_order = math.ceil(total_amount / total_orders) if total_orders > 0 else 0

    return {
        'summary': {
            'total_amount': math.ceil(total_amount),
            'total_units': total_units,
            'total_orders': total_orders,
            'total_lines': total_lines,
            'avg_order_value': avg_order,
        },
        'monthly_totals': monthly_totals,
        'by_territory': by_territory,
        'by_salesman': by_salesman,
        'by_product_line': by_product_line,
        'by_customer': by_customer,
        'region_split': {
            'us_amount': us_amount,
            'ca_amount': ca_amount,
            'ca_amount_usd': ca_amount_usd,
        },
        'last_updated': cache.get(CACHE_KEY_DASH_UPDATED),
        'year': year,
    }


def get_available_years():
    """Get the list of available years (current year back N years)."""
    current_year = date.today().year
    return list(range(current_year, current_year - DASHBOARD_YEARS_BACK, -1))


def invalidate_historical_cache(year=None, region=None):
    """Invalidate historical cache entries (does NOT touch frozen files on disk)."""
    if year and region:
        cache.delete(_cache_key_hist(region, year))
        cache.delete(_cache_key_hist_raw(region, year))
    elif year:
        for r in ('US', 'CA'):
            cache.delete(_cache_key_hist(r, year))
            cache.delete(_cache_key_hist_raw(r, year))
    else:
        for y in get_available_years():
            for r in ('US', 'CA'):
                cache.delete(_cache_key_hist(r, y))
                cache.delete(_cache_key_hist_raw(r, y))


def _empty_dashboard(year):
    """Return an empty dashboard data structure."""
    return {
        'summary': {
            'total_amount': 0, 'total_units': 0, 'total_orders': 0,
            'total_lines': 0, 'avg_order_value': 0,
        },
        'monthly_totals': [],
        'by_territory': [],
        'by_salesman': [],
        'by_product_line': [],
        'by_customer': [],
        'region_split': {'us_amount': 0, 'ca_amount': 0, 'ca_amount_usd': 0},
        'last_updated': None,
        'year': year,
    }