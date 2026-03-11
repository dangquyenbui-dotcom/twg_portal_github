"""
Shipments Dashboard Data Service
Fetches and caches shipments data for the executive dashboard and YoY comparisons.

Data priority (fastest to slowest):
  1. Frozen disk files (shipments_dashboard_data/*.json.gz) - for completed years, <1ms read
  2. In-memory cache (Flask-Caching) - for current year, sub-millisecond
  3. SQL Server (arytrn + artran) - on-demand fetch, ~15-20 seconds for a full year

Storage: shipments_dashboard_data/{region}_{year}.json.gz  (e.g., us_2025.json.gz)
Each file contains:
  - meta: region, year, frozen_at, version, row_count
  - raw_rows: list of dicts (same 26 columns as shipments Excel export)
  - data: pre-aggregated summary dict for instant dashboard rendering

Key differences from bookings_dashboard_data_service:
  - Amount = extprice (ERP pre-calculated) instead of origqtyord × price × (1 - disc/100)
  - Quantity = qtyshp (shipped) instead of origqtyord (ordered)
  - Date = invdte (invoice date) instead of ordate (order date)
  - Territory from artran.terr directly (no somast join)
  - Distinct count = invno (invoices) instead of sono (orders)
  - Credit memos excluded: artype <> 'C'
  - Tables: arytrn (historical) / artran (current) instead of soytrn / sotran
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

DASHBOARD_DATA_DIR = Path(__file__).resolve().parent.parent / 'shipments_dashboard_data'
CACHE_KEY_DASH_UPDATED = "shipments_dashboard_last_updated"
DASH_HIST_TIMEOUT = 86400       # 24 hours for historical year cache
DASH_CURRENT_TIMEOUT = 3900     # 65 min for current month cache
DASHBOARD_YEARS_BACK = 7


# ─────────────────────────────────────────────────────────────
# Cache key helpers
# ─────────────────────────────────────────────────────────────

def _cache_key_hist(region, year):
    """Cache key for historical year summary (dashboard rendering)."""
    return f"ship_dash_hist_{region.lower()}_{year}"

def _cache_key_hist_raw(region, year):
    """Cache key for historical year raw rows (Excel export)."""
    return f"ship_dash_hist_raw_{region.lower()}_{year}"

def _cache_key_current(region):
    """Cache key for current month summary."""
    return f"ship_dash_current_{region.lower()}"

def _frozen_file_path(region, year):
    """Path to the frozen gzip JSON file for a region/year."""
    return DASHBOARD_DATA_DIR / f"{region.lower()}_{year}.json.gz"

def _ensure_data_dir():
    """Create the shipments_dashboard_data directory if it doesn't exist."""
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
    logger.info(f"ShipmentsDashboard: Saved frozen data {filepath.name} ({file_size:,} bytes{row_info})")
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
        logger.error(f"ShipmentsDashboard: Failed to load frozen file {filepath.name}: {e}")
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
            logger.info(f"ShipmentsDashboard: Frozen file {filepath.name} is v{version} (no raw rows). "
                        f"Re-download via admin page to get raw data.")
            return None

        raw_rows = wrapper.get('raw_rows')
        if raw_rows:
            logger.info(f"ShipmentsDashboard: Loaded {len(raw_rows):,} raw rows from {filepath.name}")
        return raw_rows

    except Exception as e:
        logger.error(f"ShipmentsDashboard: Failed to load raw rows from {filepath.name}: {e}")
        return None


def delete_frozen_data(region, year):
    """Delete the frozen file for a region/year."""
    filepath = _frozen_file_path(region, year)
    if filepath.exists():
        filepath.unlink()
        logger.info(f"ShipmentsDashboard: Deleted frozen data {filepath.name}")
        return True
    return False


def _read_meta_only(filepath):
    """
    Read only the meta block from a frozen file without loading the full file.
    Decompresses ~2KB instead of the entire file (which can be 10-50MB).
    """
    try:
        with gzip.open(filepath, 'rt', encoding='utf-8') as f:
            chunk = f.read(2048)
        # meta is always the first key: {"meta":{...},"data":...
        meta_start = chunk.find('"meta"')
        if meta_start == -1:
            return None
        brace_start = chunk.find('{', meta_start + 6)
        if brace_start == -1:
            return None
        depth = 0
        for i in range(brace_start, len(chunk)):
            if chunk[i] == '{':
                depth += 1
            elif chunk[i] == '}':
                depth -= 1
                if depth == 0:
                    return json.loads(chunk[brace_start:i + 1])
        return None
    except Exception:
        return None


def get_frozen_status():
    """
    Get the status of all frozen files for the admin page.
    Returns a list of dicts with year, region, exists, file_size, frozen_at, version, row_count.
    Only reads the first ~2KB of each file (meta block) instead of the full contents.
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
                meta = _read_meta_only(filepath)
                if meta:
                    entry['frozen_at'] = meta.get('frozen_at')
                    entry['version'] = meta.get('version', 2)
                    entry['row_count'] = meta.get('row_count', 0)
                    entry['has_raw_rows'] = entry['version'] >= 3 and entry['row_count'] > 0

            statuses.append(entry)

    return statuses


# ─────────────────────────────────────────────────────────────
# SQL Queries — Dashboard Aggregation (lean columns for summary)
# ─────────────────────────────────────────────────────────────

def _build_dashboard_query(database, table, year=None, current_month_only=False):
    """
    Build the dashboard summary query (lean columns for aggregation).
    Used for current month data and as a fallback for historical years.
    """
    date_filter = ""
    if year and not current_month_only:
        date_filter = (f"\n      AND tr.invdte >= '{year}-01-01'"
                       f"\n      AND tr.invdte < '{year + 1}-01-01'")

    return f"""
    SELECT
        tr.invno,
        tr.sono,
        tr.qtyshp                                AS units,
        tr.extprice                               AS amount,
        tr.invdte,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE tr.terr
        END                                       AS terr_code,
        tr.custno, cu.company AS cust_name, tr.salesmn, ic.plinid
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.currhist <> 'X'
      AND tr.artype <> 'C'{date_filter}
    """


# ─────────────────────────────────────────────────────────────
# SQL Queries — Raw Data Download (full 26 columns for export)
# ─────────────────────────────────────────────────────────────

def _build_raw_download_query(database, table, year):
    """
    Build the raw data download query — same 26 columns as the shipments Excel export.
    This is used during admin download to pull ALL line items for a year from arytrn.
    """
    next_year = year + 1
    return f"""
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
    FROM {database}.dbo.{table} tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.currhist <> 'X'
      AND tr.artype <> 'C'
      AND tr.invdte >= '{year}-01-01'
      AND tr.invdte < '{next_year}-01-01'
    """


def _process_raw_download_rows(cursor, rows, region='US'):
    """
    Process raw SQL rows into a list of dicts with territory mapping and customer filtering.
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

        # Clean up string fields
        for key in ('CustomerNo', 'CustomerName', 'Item', 'Description',
                    'ProductLine', 'Salesman', 'Location',
                    'InvoiceNo', 'SalesOrder', 'InvoiceStatus', 'InvoiceType',
                    'TranTerr', 'CustTerr', 'TerrCode', 'PONumber', 'Batch',
                    'Currency'):
            if record.get(key):
                record[key] = str(record[key]).strip()

        # Convert date objects to ISO strings for JSON serialization
        for key in ('InvoiceDate',):
            val = record.get(key)
            if val is not None and hasattr(val, 'isoformat'):
                record[key] = val.isoformat()

        # Convert Decimal types to float for JSON serialization
        for key in ('UnitPrice', 'Discount', 'ExtPrice', 'UnitCost', 'ExchangeRate'):
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
    Takes tuples from the lean dashboard query (10 columns).
    """
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_invoices = set()
    distinct_orders = set()
    monthly = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'invoices': set(), 'orders': set()})
    terr_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'invoices': set()})
    sm_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'invoices': set()})
    pl_data = defaultdict(lambda: {'amount': 0.0, 'units': 0})
    cust_data = defaultdict(lambda: {'name': '', 'amount': 0.0, 'units': 0, 'invoices': set()})

    for invno, sono, units, amount, invdte, terr_code, custno, cust_name, salesmn, plinid in rows:
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

        if hasattr(invdte, 'year'):
            yr, mo = invdte.year, invdte.month
        else:
            try:
                dt = datetime.strptime(str(invdte)[:10], '%Y-%m-%d')
                yr, mo = dt.year, dt.month
            except (ValueError, TypeError):
                continue

        total_amount += amt
        total_units += qty
        total_lines += 1
        if invno:
            distinct_invoices.add(invno)
        if sono:
            distinct_orders.add(sono)

        mk = (yr, mo)
        monthly[mk]['amount'] += amt
        monthly[mk]['units'] += qty
        monthly[mk]['invoices'].add(invno)
        monthly[mk]['orders'].add(sono)
        terr_data[territory]['amount'] += amt
        terr_data[territory]['units'] += qty
        terr_data[territory]['invoices'].add(invno)
        sm_data[salesman]['amount'] += amt
        sm_data[salesman]['units'] += qty
        sm_data[salesman]['invoices'].add(invno)
        pl_data[product_line]['amount'] += amt
        pl_data[product_line]['units'] += qty
        cust_data[custno_clean]['name'] = customer_display
        cust_data[custno_clean]['amount'] += amt
        cust_data[custno_clean]['units'] += qty
        cust_data[custno_clean]['invoices'].add(invno)

    summary = {
        'total_amount': math.ceil(total_amount),
        'total_units': total_units,
        'total_invoices': len(distinct_invoices),
        'total_orders': len(distinct_orders),
        'total_lines': total_lines,
    }

    monthly_totals = sorted([
        {'yr': yr, 'mo': mo, 'amount': math.ceil(v['amount']),
         'units': v['units'], 'invoices': len(v['invoices']),
         'orders': len(v['orders'])}
        for (yr, mo), v in monthly.items()
    ], key=lambda x: (x['yr'], x['mo']))

    def _build_ranked(data_dict, key_field='name'):
        result = sorted([
            {key_field: k, 'amount': math.ceil(v['amount']), 'units': v['units'],
             **({'invoices': len(v['invoices'])} if 'invoices' in v else {})}
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
         'units': v['units'], 'invoices': len(v['invoices'])}
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
    Each dict has keys like InvoiceNo, QtyShipped, ExtPrice, Territory, etc.
    """
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_invoices = set()
    distinct_orders = set()
    monthly = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'invoices': set(), 'orders': set()})
    terr_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'invoices': set()})
    sm_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'invoices': set()})
    pl_data = defaultdict(lambda: {'amount': 0.0, 'units': 0})
    cust_data = defaultdict(lambda: {'name': '', 'amount': 0.0, 'units': 0, 'invoices': set()})

    for row in raw_rows:
        invno = row.get('InvoiceNo')
        sono = row.get('SalesOrder')
        amt = float(row.get('ExtPrice') or 0)
        qty = int(row.get('QtyShipped') or 0)
        territory = row.get('Territory', 'Others')
        salesman = (row.get('Salesman') or '').strip() or 'Unassigned'
        product_line = (row.get('ProductLine') or '').strip() or 'Other'
        custno = (row.get('CustomerNo') or '').strip().upper()
        cust_name = (row.get('CustomerName') or '').strip() or custno
        invdte_str = row.get('InvoiceDate', '')

        # Parse date for monthly grouping
        try:
            if isinstance(invdte_str, str) and len(invdte_str) >= 10:
                yr = int(invdte_str[:4])
                mo = int(invdte_str[5:7])
            else:
                continue
        except (ValueError, TypeError):
            continue

        total_amount += amt
        total_units += qty
        total_lines += 1
        if invno:
            distinct_invoices.add(invno)
        if sono:
            distinct_orders.add(sono)

        mk = (yr, mo)
        monthly[mk]['amount'] += amt
        monthly[mk]['units'] += qty
        monthly[mk]['invoices'].add(invno)
        monthly[mk]['orders'].add(sono)
        terr_data[territory]['amount'] += amt
        terr_data[territory]['units'] += qty
        terr_data[territory]['invoices'].add(invno)
        sm_data[salesman]['amount'] += amt
        sm_data[salesman]['units'] += qty
        sm_data[salesman]['invoices'].add(invno)
        pl_data[product_line]['amount'] += amt
        pl_data[product_line]['units'] += qty
        cust_data[custno]['name'] = cust_name
        cust_data[custno]['amount'] += amt
        cust_data[custno]['units'] += qty
        cust_data[custno]['invoices'].add(invno)

    summary = {
        'total_amount': math.ceil(total_amount),
        'total_units': total_units,
        'total_invoices': len(distinct_invoices),
        'total_orders': len(distinct_orders),
        'total_lines': total_lines,
    }

    monthly_totals = sorted([
        {'yr': yr, 'mo': mo, 'amount': math.ceil(v['amount']),
         'units': v['units'], 'invoices': len(v['invoices']),
         'orders': len(v['orders'])}
        for (yr, mo), v in monthly.items()
    ], key=lambda x: (x['yr'], x['mo']))

    def _build_ranked(data_dict, key_field='name'):
        result = sorted([
            {key_field: k, 'amount': math.ceil(v['amount']), 'units': v['units'],
             **({'invoices': len(v['invoices'])} if 'invoices' in v else {})}
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
         'units': v['units'], 'invoices': len(v['invoices'])}
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
# Fetch from SQL Server
# ─────────────────────────────────────────────────────────────

def _fetch_raw_rows_from_sql(database, table, region, year):
    """
    Fetch full 26-column raw rows from SQL Server for a year.
    Returns list of dicts, or None on failure.
    """
    query = _build_raw_download_query(database, table, year)
    label = f"{'US' if region == 'US' else 'CA'} {table} {year}"

    try:
        conn = get_connection(database)
        cursor = conn.cursor()
        logger.info(f"ShipmentsDashboard: Fetching RAW {label}... (this may take 15-30 seconds)")
        cursor.execute(query)
        rows = cursor.fetchall()
        raw_count = len(rows)
        logger.info(f"ShipmentsDashboard: {label} fetched {raw_count:,} raw rows from SQL. "
                     f"Processing on hosting server...")

        results = _process_raw_download_rows(cursor, rows, region=region)
        cursor.close()
        conn.close()

        logger.info(f"ShipmentsDashboard: {label} processed → {len(results):,} rows "
                     f"(filtered from {raw_count:,})")
        return results

    except Exception as e:
        logger.error(f"ShipmentsDashboard: RAW {label} query failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# Admin: Download a year (raw rows + summary)
# ─────────────────────────────────────────────────────────────

def download_year_data(year, region='US'):
    """
    Admin action: Download a full year of shipments data from SQL Server.

    1. Fetch all raw rows (26 columns) from arytrn — ONE SQL query
    2. Aggregate into dashboard summary on the hosting server
    3. Save BOTH raw rows + summary to a gzip JSON file on disk
    4. Cache the summary for immediate dashboard use

    After this, SQL Server is never queried again for this region/year.
    """
    logger.info(f"ShipmentsDashboard Admin: Downloading {region} {year} (raw + summary)...")

    db = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA

    # Step 1: Fetch all raw rows from SQL (one query, one pass)
    raw_rows = _fetch_raw_rows_from_sql(db, 'arytrn', region, year)
    if raw_rows is None:
        raise RuntimeError(f"Failed to fetch raw shipments data for {region} {year} from SQL Server")

    # Step 2: Aggregate on the hosting server
    logger.info(f"ShipmentsDashboard Admin: Aggregating {len(raw_rows):,} rows for {region} {year}...")
    summary = _aggregate_from_raw_dicts(raw_rows, region=region)

    # Step 3: Save both raw rows + summary to disk
    file_size = save_frozen_data(region, year, summary, raw_rows=raw_rows)

    # Step 4: Cache the summary for immediate dashboard use
    cache.set(_cache_key_hist(region, year), summary, timeout=DASH_HIST_TIMEOUT)

    logger.info(
        f"ShipmentsDashboard Admin: {region} {year} complete — "
        f"${summary['summary']['total_amount']:,}, "
        f"{len(summary['monthly_totals'])} months, "
        f"{len(raw_rows):,} raw rows, "
        f"{file_size:,} bytes on disk"
    )

    return {
        'region': region,
        'year': year,
        'total_amount': summary['summary']['total_amount'],
        'total_invoices': summary['summary'].get('total_invoices', 0),
        'total_lines': summary['summary']['total_lines'],
        'months': len(summary['monthly_totals']),
        'row_count': len(raw_rows),
        'file_size': file_size,
    }


# ─────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────

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
