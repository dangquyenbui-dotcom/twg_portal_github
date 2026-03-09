"""
Dashboard Data Service
Fetches and caches pre-aggregated bookings data for the executive dashboard.

Data priority (fastest to slowest):
  1. Frozen disk files (dashboard_data/*.json.gz) - for completed years, <1ms read
  2. In-memory cache (Flask-Caching) - for current year, sub-millisecond
  3. SQL Server (soytrn + sotran) - on-demand fetch, ~15-20 seconds for a full year

Storage: dashboard_data/{region}_{year}.json.gz  (e.g., us_2025.json.gz)
Each file contains the pre-aggregated summary dict (~1-2KB compressed).
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
DASH_HIST_TIMEOUT = 86400
DASH_CURRENT_TIMEOUT = 3900
DASHBOARD_YEARS_BACK = 5


def _cache_key_hist(region, year):
    return f"dash_hist_{region.lower()}_{year}"

def _cache_key_current(region):
    return f"dash_current_{region.lower()}"

def _frozen_file_path(region, year):
    return DASHBOARD_DATA_DIR / f"{region.lower()}_{year}.json.gz"

def _ensure_data_dir():
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)


# --- Frozen File I/O ---

def save_frozen_data(region, year, summary_dict):
    _ensure_data_dir()
    filepath = _frozen_file_path(region, year)
    data_with_meta = {
        'meta': {'region': region, 'year': year, 'frozen_at': datetime.now().isoformat(), 'version': 2},
        'data': summary_dict,
    }
    with gzip.open(filepath, 'wt', encoding='utf-8', compresslevel=9) as f:
        json.dump(data_with_meta, f, separators=(',', ':'), default=str)
    file_size = filepath.stat().st_size
    logger.info(f"Dashboard: Saved frozen data {filepath.name} ({file_size:,} bytes)")
    return file_size


def load_frozen_data(region, year):
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


def delete_frozen_data(region, year):
    filepath = _frozen_file_path(region, year)
    if filepath.exists():
        filepath.unlink()
        logger.info(f"Dashboard: Deleted frozen data {filepath.name}")
        return True
    return False


def get_frozen_status():
    _ensure_data_dir()
    current_year = date.today().year
    start_year = current_year - DASHBOARD_YEARS_BACK + 1
    statuses = []
    for year in range(current_year, start_year - 1, -1):
        for region in ('US', 'CA'):
            filepath = _frozen_file_path(region, year)
            entry = {
                'year': year, 'region': region,
                'is_current_year': year == current_year,
                'exists': False, 'file_size': 0,
                'frozen_at': None, 'filename': filepath.name,
            }
            if filepath.exists():
                entry['exists'] = True
                entry['file_size'] = filepath.stat().st_size
                try:
                    with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                        wrapper = json.load(f)
                    entry['frozen_at'] = wrapper.get('meta', {}).get('frozen_at')
                except Exception:
                    pass
            statuses.append(entry)
    return statuses


# --- SQL Queries ---

def _build_dashboard_query(database, table, year=None, current_month_only=False):
    date_filter = ""
    if year and not current_month_only:
        date_filter = f"\n      AND tr.ordate >= '{year}-01-01'\n      AND tr.ordate < '{year + 1}-01-01'"
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


def _aggregate_rows(rows, region='US'):
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

    summary = {'total_amount': math.ceil(total_amount), 'total_units': total_units,
               'total_orders': len(distinct_orders), 'total_lines': total_lines}

    monthly_totals = sorted([
        {'yr': yr, 'mo': mo, 'amount': math.ceil(v['amount']), 'units': v['units'], 'orders': len(v['orders'])}
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

    return {'summary': summary, 'monthly_totals': monthly_totals, 'by_territory': by_territory,
            'by_salesman': by_salesman, 'by_product_line': by_product_line, 'by_customer': by_customer}


def _merge_summaries(hist_summary, current_summary):
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

    by_territory = _merge_ranked(hist_summary['by_territory'], current_summary['by_territory'], 'name')
    by_salesman = _merge_ranked(hist_summary['by_salesman'], current_summary['by_salesman'], 'name')
    by_product_line = _merge_ranked(hist_summary['by_product_line'], current_summary['by_product_line'], 'name')

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

    return {'summary': {'total_amount': math.ceil(total_amount), 'total_units': total_units,
                        'total_orders': total_orders, 'total_lines': total_lines},
            'monthly_totals': all_monthly, 'by_territory': by_territory,
            'by_salesman': by_salesman, 'by_product_line': by_product_line, 'by_customer': by_customer}


# --- Fetch from SQL ---

def _fetch_and_aggregate(database, table, region, year=None, current_month_only=False):
    query = _build_dashboard_query(database, table, year=year, current_month_only=current_month_only)
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
        logger.info(f"Dashboard: {label} aggregated - ${summary['summary']['total_amount']:,} "
                     f"across {len(summary['monthly_totals'])} months")
        return summary
    except Exception as e:
        logger.error(f"Dashboard: {label} query failed: {e}")
        return None

def fetch_historical_year(year, region='US'):
    db = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA
    return _fetch_and_aggregate(db, 'soytrn', region, year=year)

def fetch_current_month(region='US'):
    db = Config.DB_ORDERS if region == 'US' else Config.DB_ORDERS_CA
    return _fetch_and_aggregate(db, 'sotran', region, current_month_only=True)


# --- Admin: Download a year ---

def download_year_data(year, region='US'):
    logger.info(f"Dashboard Admin: Downloading {region} {year}...")
    summary = fetch_historical_year(year, region)
    if summary is None:
        raise RuntimeError(f"Failed to fetch {region} {year} from SQL Server")
    file_size = save_frozen_data(region, year, summary)
    cache.set(_cache_key_hist(region, year), summary, timeout=DASH_HIST_TIMEOUT)
    return {
        'region': region, 'year': year,
        'total_amount': summary['summary']['total_amount'],
        'total_orders': summary['summary']['total_orders'],
        'months': len(summary['monthly_totals']),
        'file_size': file_size,
    }


# --- Data Resolution: Disk -> Cache -> SQL ---

def _get_historical_data(year, region):
    frozen = load_frozen_data(region, year)
    if frozen is not None:
        return frozen
    cache_key = _cache_key_hist(region, year)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    logger.info(f"Dashboard: No frozen/cache for {region} {year} - fetching SQL...")
    summary = fetch_historical_year(year, region)
    if summary is not None:
        cache.set(cache_key, summary, timeout=DASH_HIST_TIMEOUT)
    return summary

def _get_current_month_data(region):
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
    logger.info("Dashboard Worker: === Refreshing current month (sotran) ===")
    for region in ('US', 'CA'):
        summary = fetch_current_month(region)
        if summary is not None:
            cache.set(_cache_key_current(region), summary, timeout=DASH_CURRENT_TIMEOUT)
            logger.info(f"Dashboard Worker: {region} current month updated.")
    cache.set(CACHE_KEY_DASH_UPDATED, datetime.now(), timeout=DASH_CURRENT_TIMEOUT)
    logger.info("Dashboard Worker: === Current month refresh complete ===")


# --- Public API ---

def get_dashboard_data(year=None, cad_rate=None):
    if year is None:
        year = date.today().year
    rate = cad_rate or 0.72
    current_year = date.today().year

    us_hist = _get_historical_data(year, 'US')
    us_current = _get_current_month_data('US') if year == current_year else None
    us_merged = _merge_summaries(us_hist, us_current)

    ca_hist = _get_historical_data(year, 'CA')
    ca_current = _get_current_month_data('CA') if year == current_year else None
    ca_merged = _merge_summaries(ca_hist, ca_current)

    if us_merged is None and ca_merged is None:
        return _empty_dashboard(year)

    us_amount = us_merged['summary']['total_amount'] if us_merged else 0
    ca_amount = ca_merged['summary']['total_amount'] if ca_merged else 0
    ca_amount_usd = math.ceil(ca_amount * rate)

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

    by_territory = _merge_dim(us_merged['by_territory'] if us_merged else [],
                               ca_merged['by_territory'] if ca_merged else [], rate)
    by_salesman = _merge_dim(us_merged['by_salesman'] if us_merged else [],
                              ca_merged['by_salesman'] if ca_merged else [], rate)
    by_product_line = _merge_dim(us_merged['by_product_line'] if us_merged else [],
                                  ca_merged['by_product_line'] if ca_merged else [], rate)

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

    total_amount = us_amount + ca_amount_usd
    total_units = (us_merged['summary']['total_units'] if us_merged else 0) + (ca_merged['summary']['total_units'] if ca_merged else 0)
    total_orders = (us_merged['summary']['total_orders'] if us_merged else 0) + (ca_merged['summary']['total_orders'] if ca_merged else 0)
    total_lines = (us_merged['summary']['total_lines'] if us_merged else 0) + (ca_merged['summary']['total_lines'] if ca_merged else 0)
    avg_order = math.ceil(total_amount / total_orders) if total_orders > 0 else 0

    return {
        'summary': {'total_amount': math.ceil(total_amount), 'total_units': total_units,
                     'total_orders': total_orders, 'total_lines': total_lines, 'avg_order_value': avg_order},
        'monthly_totals': monthly_totals, 'by_territory': by_territory, 'by_salesman': by_salesman,
        'by_product_line': by_product_line, 'by_customer': by_customer,
        'region_split': {'us_amount': us_amount, 'ca_amount': ca_amount, 'ca_amount_usd': ca_amount_usd},
        'last_updated': cache.get(CACHE_KEY_DASH_UPDATED), 'year': year,
    }

def get_available_years():
    current_year = date.today().year
    return list(range(current_year, current_year - DASHBOARD_YEARS_BACK, -1))

def invalidate_historical_cache(year=None, region=None):
    if year and region:
        cache.delete(_cache_key_hist(region, year))
    elif year:
        for r in ('US', 'CA'):
            cache.delete(_cache_key_hist(r, year))
    else:
        for y in get_available_years():
            for r in ('US', 'CA'):
                cache.delete(_cache_key_hist(r, y))

def _empty_dashboard(year):
    return {'summary': {'total_amount': 0, 'total_units': 0, 'total_orders': 0, 'total_lines': 0, 'avg_order_value': 0},
            'monthly_totals': [], 'by_territory': [], 'by_salesman': [], 'by_product_line': [], 'by_customer': [],
            'region_split': {'us_amount': 0, 'ca_amount': 0, 'ca_amount_usd': 0}, 'last_updated': None, 'year': year}