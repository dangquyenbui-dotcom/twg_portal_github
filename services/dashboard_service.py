"""
Dashboard Aggregation Service
Processes raw cached bookings and open orders data into dashboard-ready metrics.
Supports filtering by territory, salesman, product line, and customer.

All data comes from the existing cache — zero SQL queries at request time.
"""

import math
from collections import defaultdict


def _safe_float(val):
    """Safely convert a value to float."""
    try:
        return float(val or 0)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val):
    """Safely convert a value to int."""
    try:
        return int(val or 0)
    except (ValueError, TypeError):
        return 0


def build_filter_options(rows_us, rows_ca):
    """
    Extract all unique filter values from raw data.
    Returns dict of filter_name → sorted list of unique values.
    """
    territories = set()
    salesmen = set()
    product_lines = set()
    customers = set()

    for row in (rows_us or []) + (rows_ca or []):
        terr = (row.get('Territory') or '').strip()
        if terr:
            territories.add(terr)

        sm = (row.get('Salesman') or '').strip()
        if sm:
            salesmen.add(sm)

        pl = (row.get('ProductLine') or '').strip()
        if pl:
            product_lines.add(pl)

        cust = (row.get('CustomerName') or '').strip()
        custno = (row.get('CustomerNo') or '').strip()
        if cust and custno:
            customers.add(f"{custno} — {cust}")

    return {
        'territories': sorted(territories),
        'salesmen': sorted(salesmen),
        'product_lines': sorted(product_lines),
        'customers': sorted(customers),
    }


def aggregate_dashboard_data(rows_us, rows_ca, filters=None, cad_rate=None):
    """
    Aggregate raw line-item data into dashboard metrics.

    Args:
        rows_us:   List of raw US bookings dicts (from cache)
        rows_ca:   List of raw CA bookings dicts (from cache)
        filters:   Optional dict with keys: territories, salesmen, product_lines, customers
                   Each value is a list of selected filter values (empty = all)
        cad_rate:  CAD → USD exchange rate for converting Canadian amounts

    Returns dict with:
        summary:            {total_amount, total_units, total_orders, avg_order_value, total_lines}
        by_territory:       [{name, amount, units, orders}, ...] sorted by amount desc
        by_salesman:        [{name, amount, units, orders}, ...] sorted by amount desc
        by_product_line:    [{name, amount, units}, ...] sorted by amount desc
        by_customer:        [{custno, name, amount, units, orders}, ...] sorted by amount desc (top 20)
        region_split:       {us_amount, ca_amount, ca_amount_usd}
    """
    filters = filters or {}
    f_territories = set(filters.get('territories', []))
    f_salesmen = set(filters.get('salesmen', []))
    f_product_lines = set(filters.get('product_lines', []))
    f_customers = set(filters.get('customers', []))

    rate = cad_rate or 0.72

    # Accumulators
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_orders = set()
    us_amount = 0.0
    ca_amount = 0.0

    terr_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    sm_data = defaultdict(lambda: {'amount': 0.0, 'units': 0, 'orders': set()})
    pl_data = defaultdict(lambda: {'amount': 0.0, 'units': 0})
    cust_data = defaultdict(lambda: {'name': '', 'amount': 0.0, 'units': 0, 'orders': set()})

    all_rows = []
    for row in (rows_us or []):
        row['_region'] = 'US'
        all_rows.append(row)
    for row in (rows_ca or []):
        row['_region'] = 'CA'
        all_rows.append(row)

    for row in all_rows:
        territory = (row.get('Territory') or '').strip()
        salesman = (row.get('Salesman') or '').strip() or 'Unassigned'
        product_line = (row.get('ProductLine') or '').strip() or 'Other'
        custno = (row.get('CustomerNo') or '').strip()
        cust_name = (row.get('CustomerName') or '').strip()
        cust_key = f"{custno} — {cust_name}" if custno and cust_name else custno
        sono = row.get('SalesOrder')
        region = row.get('_region', 'US')

        # Apply filters
        if f_territories and territory not in f_territories:
            continue
        if f_salesmen and salesman not in f_salesmen:
            continue
        if f_product_lines and product_line not in f_product_lines:
            continue
        if f_customers and cust_key not in f_customers:
            continue

        amt = _safe_float(row.get('ExtAmount') or row.get('OpenAmount'))
        qty = _safe_int(row.get('QtyOrdered') or row.get('OpenQty'))

        # Convert CA amounts to USD for unified totals
        if region == 'CA':
            amt_usd = amt * rate
            ca_amount += amt
        else:
            amt_usd = amt
            us_amount += amt

        total_amount += amt_usd
        total_units += qty
        total_lines += 1
        if sono:
            distinct_orders.add(sono)

        # By territory
        terr_data[territory]['amount'] += amt_usd
        terr_data[territory]['units'] += qty
        terr_data[territory]['orders'].add(sono)

        # By salesman
        sm_data[salesman]['amount'] += amt_usd
        sm_data[salesman]['units'] += qty
        sm_data[salesman]['orders'].add(sono)

        # By product line
        pl_data[product_line]['amount'] += amt_usd
        pl_data[product_line]['units'] += qty

        # By customer
        cust_data[custno]['name'] = cust_name
        cust_data[custno]['amount'] += amt_usd
        cust_data[custno]['units'] += qty
        cust_data[custno]['orders'].add(sono)

    # Build sorted results
    order_count = len(distinct_orders)
    avg_order = math.ceil(total_amount / order_count) if order_count > 0 else 0

    by_territory = sorted([
        {
            'name': k,
            'amount': math.ceil(v['amount']),
            'units': v['units'],
            'orders': len(v['orders']),
        }
        for k, v in terr_data.items()
    ], key=lambda x: x['amount'], reverse=True)

    by_salesman = sorted([
        {
            'name': k,
            'amount': math.ceil(v['amount']),
            'units': v['units'],
            'orders': len(v['orders']),
        }
        for k, v in sm_data.items()
    ], key=lambda x: x['amount'], reverse=True)

    by_product_line = sorted([
        {
            'name': k,
            'amount': math.ceil(v['amount']),
            'units': v['units'],
        }
        for k, v in pl_data.items()
    ], key=lambda x: x['amount'], reverse=True)

    by_customer = sorted([
        {
            'custno': k,
            'name': v['name'],
            'amount': math.ceil(v['amount']),
            'units': v['units'],
            'orders': len(v['orders']),
        }
        for k, v in cust_data.items()
    ], key=lambda x: x['amount'], reverse=True)[:20]  # Top 20

    return {
        'summary': {
            'total_amount': math.ceil(total_amount),
            'total_units': total_units,
            'total_orders': order_count,
            'avg_order_value': avg_order,
            'total_lines': total_lines,
        },
        'by_territory': by_territory,
        'by_salesman': by_salesman,
        'by_product_line': by_product_line,
        'by_customer': by_customer,
        'region_split': {
            'us_amount': math.ceil(us_amount),
            'ca_amount': math.ceil(ca_amount),
            'ca_amount_usd': math.ceil(ca_amount * rate),
        },
    }