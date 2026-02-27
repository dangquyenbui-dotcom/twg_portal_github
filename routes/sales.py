"""
Sales Blueprint
Routes for all sales reports: bookings dashboard, open orders dashboard, and Excel exports.
"""

import math
from datetime import date

from flask import Blueprint, render_template, session, redirect, url_for

from services.data_worker import get_bookings_from_cache, get_open_orders_from_cache
from services.bookings_service import fetch_bookings_raw_us, fetch_bookings_raw_ca
from services.open_orders_service import fetch_open_orders_raw_us, fetch_open_orders_raw_ca
from services.excel_helper import build_export_workbook, send_workbook

sales_bp = Blueprint('sales', __name__, url_prefix='/sales')


# ═══════════════════════════════════════════════════════════════
# Column definitions for Excel exports
# ═══════════════════════════════════════════════════════════════

BOOKINGS_EXPORT_COLUMNS = [
    ('Sales Order (sono)',        'SalesOrder',    20, None),
    ('Line# (tranlineno)',        'LineNo',        18, '#,##0'),
    ('Order Date (ordate)',       'OrderDate',     18, 'MM/DD/YYYY'),
    ('Customer No (custno)',      'CustomerNo',    20, None),
    ('Customer Name (company)',   'CustomerName',  30, None),
    ('Item (item)',               'Item',          18, None),
    ('Description (descrip)',     'Description',   32, None),
    ('Product Line (plinid)',     'ProductLine',   18, None),
    ('Qty Ordered (origqtyord)',  'QtyOrdered',    20, '#,##0'),
    ('Qty Shipped (qtyshp)',      'QtyShipped',    18, '#,##0'),
    ('Unit Price (price)',        'UnitPrice',     16, '$#,##0.00'),
    ('Discount % (disc)',         'Discount',      14, '0.000'),
    ('Ext Amount (calculated)',   'ExtAmount',     20, '$#,##0.00'),
    ('Ext Price (extprice)',      'ExtPrice',      18, '$#,##0.00'),
    ('Line Status (sostat)',      'LineStatus',    18, None),
    ('Order Type (sotype)',       'OrderType',     16, None),
    ('Territory (mapped)',        'Territory',     18, None),
    ('Terr Code (resolved)',      'TerrCode',      16, None),
    ('Tran Terr (tr.terr)',       'TranTerr',      16, None),
    ('SO Mast Terr (sm.terr)',    'SOMastTerr',    18, None),
    ('Cust Terr (cu.terr)',       'CustTerr',      16, None),
    ('Salesman (salesmn)',        'Salesman',      16, None),
    ('Location (loctid)',         'Location',      16, None),
    ('Request Date (rqdate)',     'RequestDate',   18, 'MM/DD/YYYY'),
    ('Ship Date (shipdate)',      'ShipDate',      18, 'MM/DD/YYYY'),
    ('Ship Via (shipvia)',        'ShipVia',       16, None),
]

OPEN_ORDERS_EXPORT_COLUMNS = [
    ('Sales Order (sono)',        'SalesOrder',    20, None),
    ('Line# (tranlineno)',        'LineNo',        10, '#,##0'),
    ('Order Date (ordate)',       'OrderDate',     16, 'MM/DD/YYYY'),
    ('Customer No (custno)',      'CustomerNo',    18, None),
    ('Customer Name (company)',   'CustomerName',  30, None),
    ('Item (item)',               'Item',          18, None),
    ('Description (descrip)',     'Description',   32, None),
    ('Product Line (plinid)',     'ProductLine',   16, None),
    ('Orig Qty Ordered (origqtyord)', 'OrigQtyOrd', 22, '#,##0'),
    ('Open Qty (qtyord)',         'OpenQty',       18, '#,##0'),
    ('Qty Shipped (qtyshp)',      'QtyShipped',    18, '#,##0'),
    ('Unit Price (price)',        'UnitPrice',     16, '$#,##0.00'),
    ('Discount % (disc)',         'Discount',      16, '0.000'),
    ('Open Amount (calculated)',  'OpenAmount',    20, '$#,##0.00'),
    ('Line Status (sostat)',      'LineStatus',    18, None),
    ('Order Type (sotype)',       'OrderType',     16, None),
    ('Release (release)',         'Release',       16, None),
    ('Salesman (salesmn)',        'Salesman',      16, None),
    ('Territory (mapped)',        'Territory',     18, None),
    ('Terr Code (resolved)',      'TerrCode',      16, None),
    ('SO Mast Terr (sm.terr)',    'SOMastTerr',    18, None),
    ('Cust Terr (cu.terr)',       'CustTerr',      16, None),
    ('Location (loctid)',         'Location',      16, None),
    ('Request Date (rqdate)',     'RequestDate',   18, 'MM/DD/YYYY'),
    ('Ship Date (shipdate)',      'ShipDate',      18, 'MM/DD/YYYY'),
    ('Ship Via (shipvia)',        'ShipVia',       16, None),
]


# ═══════════════════════════════════════════════════════════════
# Helper: build region data dict with USD conversion for Canada
# ═══════════════════════════════════════════════════════════════

def _build_region_data(snapshot, cad_rate=None, is_canada=False):
    """
    Build a template-ready data dict from a cache snapshot.
    Handles both bookings and open orders shapes.
    For Canada, adds USD equivalents to monetary fields.
    """
    if snapshot is None:
        # Return empty defaults — works for both bookings and open orders
        return {
            "total_amount": 0, "total_amount_usd": 0,
            "total_units": 0, "total_orders": 0,
            "total_territories": 0, "total_lines": 0,
            "territory_ranking": [],
            "salesman_ranking": [],
            "order_date": None,
        }

    summary = snapshot["summary"]
    data = {
        "total_amount": summary["total_amount"],
        "total_units": summary["total_units"],
        "total_orders": summary["total_orders"],
        "total_territories": summary.get("total_territories", 0),
        "total_lines": summary.get("total_lines", 0),
        "territory_ranking": snapshot.get("ranking", snapshot.get("territory_ranking", [])),
        "salesman_ranking": snapshot.get("salesman_ranking", []),
        "order_date": summary.get("order_date"),
    }

    if is_canada and cad_rate:
        data["total_amount_usd"] = math.ceil(summary["total_amount"] * cad_rate)
        for terr in data["territory_ranking"]:
            terr["total_usd"] = math.ceil(terr["total"] * cad_rate)
        for sm in data["salesman_ranking"]:
            sm["total_usd"] = math.ceil(sm["total"] * cad_rate)
    else:
        data["total_amount_usd"] = 0

    return data


# ═══════════════════════════════════════════════════════════════
# Sales Home
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/')
def sales_home():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    return render_template('sales/index.html', user=session["user"])


# ═══════════════════════════════════════════════════════════════
# BOOKINGS — Dashboard + Exports
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/bookings')
def bookings():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    snapshot_us, snapshot_ca, last_updated, cad_rate = get_bookings_from_cache()

    us_data = _build_region_data(snapshot_us, cad_rate, is_canada=False)
    ca_data = _build_region_data(snapshot_ca, cad_rate, is_canada=True)

    error = None
    if snapshot_us is None and snapshot_ca is None:
        error = "Unable to load data. Please try again shortly."

    return render_template(
        'sales/bookings.html',
        user=session["user"],
        error=error,
        us=us_data,
        ca=ca_data,
        cad_rate=cad_rate,
        last_updated=last_updated,
    )


@sales_bp.route('/bookings/export')
def bookings_export():
    """Export today's raw bookings data (US + Canada combined)."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us = fetch_bookings_raw_us() or []
    rows_ca = fetch_bookings_raw_ca() or []

    if not rows_us and not rows_ca:
        return redirect(url_for('sales.bookings'))

    for row in rows_us:
        row['Region'] = 'US'
    for row in rows_ca:
        row['Region'] = 'CA'

    wb = build_export_workbook(
        rows=rows_us + rows_ca,
        title_label='Daily Bookings Raw Data (US + Canada)',
        columns=BOOKINGS_EXPORT_COLUMNS,
        include_region_col=True,
    )
    return send_workbook(wb, f'Bookings_Raw_US_CA_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/bookings/export/us')
def bookings_export_us():
    """Export today's raw US bookings data."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows = fetch_bookings_raw_us()
    if not rows:
        return redirect(url_for('sales.bookings'))

    wb = build_export_workbook(
        rows=rows,
        title_label='Daily Bookings Raw Data — United States',
        columns=BOOKINGS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Bookings_Raw_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/bookings/export/ca')
def bookings_export_ca():
    """Export today's raw Canada bookings data."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows = fetch_bookings_raw_ca()
    if not rows:
        return redirect(url_for('sales.bookings'))

    wb = build_export_workbook(
        rows=rows,
        title_label='Daily Bookings Raw Data — Canada',
        columns=BOOKINGS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Bookings_Raw_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# OPEN ORDERS — Dashboard + Exports
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/open-orders')
def open_orders():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    snapshot_us, snapshot_ca, last_updated, cad_rate = get_open_orders_from_cache()

    us_data = _build_region_data(snapshot_us, cad_rate, is_canada=False)
    ca_data = _build_region_data(snapshot_ca, cad_rate, is_canada=True)

    error = None
    if snapshot_us is None and snapshot_ca is None:
        error = "Unable to load data. Please try again shortly."

    return render_template(
        'sales/open_orders.html',
        user=session["user"],
        error=error,
        us=us_data,
        ca=ca_data,
        cad_rate=cad_rate,
        last_updated=last_updated,
    )


@sales_bp.route('/open-orders/export')
def open_orders_export():
    """Export open orders (US + Canada combined)."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us = fetch_open_orders_raw_us() or []
    rows_ca = fetch_open_orders_raw_ca() or []

    if not rows_us and not rows_ca:
        return redirect(url_for('sales.open_orders'))

    for row in rows_us:
        row['Region'] = 'US'
    for row in rows_ca:
        row['Region'] = 'CA'

    wb = build_export_workbook(
        rows=rows_us + rows_ca,
        title_label='Open Sales Orders (US + Canada)',
        columns=OPEN_ORDERS_EXPORT_COLUMNS,
        include_region_col=True,
    )
    return send_workbook(wb, f'Open_Orders_US_CA_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/open-orders/export/us')
def open_orders_export_us():
    """Export open orders US only."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows = fetch_open_orders_raw_us()
    if not rows:
        return redirect(url_for('sales.open_orders'))

    wb = build_export_workbook(
        rows=rows,
        title_label='Open Sales Orders — United States',
        columns=OPEN_ORDERS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Open_Orders_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/open-orders/export/ca')
def open_orders_export_ca():
    """Export open orders Canada only."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows = fetch_open_orders_raw_ca()
    if not rows:
        return redirect(url_for('sales.open_orders'))

    wb = build_export_workbook(
        rows=rows,
        title_label='Open Sales Orders — Canada',
        columns=OPEN_ORDERS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Open_Orders_CA_{date.today().strftime("%Y%m%d")}.xlsx')