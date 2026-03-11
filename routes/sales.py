"""
Sales Blueprint
Routes for all sales reports: bookings, bookings summary, shipments, shipments summary,
open orders, executive dashboard, and Excel exports.

Per-report role mapping (using Security Groups):
  /sales                                  → Sales.Base  (auto-implied by ANY Sales.*.View)

  -- BOOKINGS (orders placed) --
  /sales/bookings                         → Sales.Bookings.View
  /sales/bookings/export/*                → Sales.Bookings.Export
  /sales/bookings-summary                 → Sales.BookingsSummary.View
  /sales/bookings-summary/export/*        → Sales.BookingsSummary.Export

  -- SHIPMENTS (orders invoiced/shipped) --
  /sales/shipments                        → Sales.Shipments.View
  /sales/shipments/export/*               → Sales.Shipments.Export
  /sales/shipments-summary                → Sales.ShipmentsSummary.View
  /sales/shipments-summary/export/*       → Sales.ShipmentsSummary.Export

  -- OTHER --
  /sales/open-orders                      → Sales.OpenOrders.View
  /sales/open-orders/export/*             → Sales.OpenOrders.Export
  /sales/dashboard                        → Sales.Dashboard.View
  /sales/dashboard/export/*               → Sales.Dashboard.View  (export from frozen files)

Export roles do NOT grant view access — they only enable download buttons
on reports the user can already see. Admin bypasses all checks.
"""

import json
import math
from datetime import date, datetime

from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify

from services.data_worker import (
    get_bookings_from_cache, get_bookings_raw_from_cache,
    get_shipments_from_cache, get_shipments_raw_from_cache,
    get_open_orders_from_cache, get_open_orders_raw_from_cache,
)
from services.bookings_summary_service import (
    get_bookings_summary_from_cache,
    fetch_raw_export_data as fetch_bookings_raw_export_data,
)
from services.shipments_summary_service import (
    get_shipments_summary_from_cache,
    fetch_raw_export_data as fetch_shipments_raw_export_data,
)
from services.dashboard_data_service import (
    get_dashboard_data, get_available_years, invalidate_historical_cache,
    get_historical_raw_rows,
)
from services.excel_helper import build_export_workbook, send_workbook
from auth.decorators import require_role, user_has_role

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

SHIPMENTS_EXPORT_COLUMNS = [
    ('Invoice No (invno)',        'InvoiceNo',     20, None),
    ('Sales Order (sono)',        'SalesOrder',    20, None),
    ('Line# (tranlineno)',        'LineNo',        10, '#,##0'),
    ('Invoice Date (invdte)',     'InvoiceDate',   18, 'MM/DD/YYYY'),
    ('Customer No (custno)',      'CustomerNo',    20, None),
    ('Customer Name (company)',   'CustomerName',  30, None),
    ('Item (item)',               'Item',          18, None),
    ('Description (descrip)',     'Description',   32, None),
    ('Product Line (plinid)',     'ProductLine',   18, None),
    ('Qty Ordered (qtyord)',      'QtyOrdered',    18, '#,##0'),
    ('Qty Shipped (qtyshp)',      'QtyShipped',    18, '#,##0'),
    ('Unit Price (price)',        'UnitPrice',     16, '$#,##0.00'),
    ('Discount % (disc)',         'Discount',      14, '0.000'),
    ('Ext Price (extprice)',      'ExtPrice',      18, '$#,##0.00'),
    ('Unit Cost (cost)',          'UnitCost',      16, '$#,##0.00'),
    ('Invoice Status (arstat)',   'InvoiceStatus', 18, None),
    ('Invoice Type (artype)',     'InvoiceType',   16, None),
    ('Territory (mapped)',        'Territory',     18, None),
    ('Terr Code (resolved)',      'TerrCode',      16, None),
    ('Tran Terr (tr.terr)',       'TranTerr',      16, None),
    ('Cust Terr (cu.terr)',       'CustTerr',      16, None),
    ('Salesman (salesmn)',        'Salesman',      16, None),
    ('Location (loctid)',         'Location',      16, None),
    ('PO Number (ponum)',         'PONumber',      18, None),
    ('Batch (batch)',             'Batch',         14, None),
    ('Currency (currid)',         'Currency',      12, None),
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

# Dashboard and Bookings Summary historical exports use the same 26 columns as bookings
DASHBOARD_EXPORT_COLUMNS = BOOKINGS_EXPORT_COLUMNS
BOOKINGS_SUMMARY_EXPORT_COLUMNS = BOOKINGS_EXPORT_COLUMNS

# Shipments Summary export uses the shipments column layout
SHIPMENTS_SUMMARY_EXPORT_COLUMNS = SHIPMENTS_EXPORT_COLUMNS


# ═══════════════════════════════════════════════════════════════
# Helper: build region data dict with USD conversion for Canada
# ═══════════════════════════════════════════════════════════════

def _build_region_data(snapshot, cad_rate=None, is_canada=False):
    """
    Build a template-ready data dict from a cache snapshot.
    Handles bookings, shipments, and open orders shapes.
    For Canada, adds USD equivalents to monetary fields.
    """
    if snapshot is None:
        return {
            "total_amount": 0, "total_amount_usd": 0,
            "total_released_amount": 0, "total_released_amount_usd": 0,
            "total_units": 0, "total_orders": 0, "total_invoices": 0,
            "total_territories": 0, "total_lines": 0,
            "territory_ranking": [],
            "salesman_ranking": [],
            "customer_ranking": [],
            "order_date": None,
        }

    summary = snapshot["summary"]
    data = {
        "total_amount": summary["total_amount"],
        "total_released_amount": summary.get("total_released_amount", 0),
        "total_units": summary["total_units"],
        "total_orders": summary.get("total_orders", 0),
        "total_invoices": summary.get("total_invoices", 0),
        "total_territories": summary.get("total_territories", 0),
        "total_lines": summary.get("total_lines", 0),
        "territory_ranking": snapshot.get("ranking", snapshot.get("territory_ranking", [])),
        "salesman_ranking": snapshot.get("salesman_ranking", []),
        "customer_ranking": snapshot.get("customer_ranking", []),
        "order_date": summary.get("order_date"),
    }

    if is_canada and cad_rate:
        data["total_amount_usd"] = math.ceil(summary["total_amount"] * cad_rate)
        data["total_released_amount_usd"] = math.ceil(summary.get("total_released_amount", 0) * cad_rate)
        for terr in data["territory_ranking"]:
            terr["total_usd"] = math.ceil(terr["total"] * cad_rate)
            terr["released_usd"] = math.ceil(terr.get("released", 0) * cad_rate)
        for sm in data["salesman_ranking"]:
            sm["total_usd"] = math.ceil(sm["total"] * cad_rate)
            sm["released_usd"] = math.ceil(sm.get("released", 0) * cad_rate)
        for cust in data["customer_ranking"]:
            cust["total_usd"] = math.ceil(cust["total"] * cad_rate)
    else:
        data["total_amount_usd"] = 0
        data["total_released_amount_usd"] = 0

    return data


# ═══════════════════════════════════════════════════════════════
# Sales Home — requires Sales.Base (implied by ANY Sales.*.View)
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/')
@require_role('Sales.Base')
def sales_home():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    return render_template('sales/index.html', user=session["user"])


# ═══════════════════════════════════════════════════════════════
# BOOKINGS — View requires Sales.Bookings.View
#             Export requires Sales.Bookings.Export
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/bookings')
@require_role('Sales.Bookings.View')
def bookings():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    snapshot_us, snapshot_ca, last_updated, cad_rate = get_bookings_from_cache()

    us_data = _build_region_data(snapshot_us, cad_rate, is_canada=False)
    ca_data = _build_region_data(snapshot_ca, cad_rate, is_canada=True)

    error = None
    if snapshot_us is None and snapshot_ca is None:
        error = "Unable to load data. Please try again shortly."

    can_export = user_has_role(session["user"], 'Sales.Bookings.Export')

    return render_template(
        'sales/bookings.html',
        user=session["user"],
        error=error,
        us=us_data,
        ca=ca_data,
        cad_rate=cad_rate,
        last_updated=last_updated,
        can_export=can_export,
    )


@sales_bp.route('/bookings/export')
@require_role('Sales.Bookings.Export')
def bookings_export():
    """Export today's raw bookings data (US + Canada combined) — reads from cache, zero SQL."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us, rows_ca = get_bookings_raw_from_cache()
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
@require_role('Sales.Bookings.Export')
def bookings_export_us():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us, _ = get_bookings_raw_from_cache()
    if not rows_us:
        return redirect(url_for('sales.bookings'))

    wb = build_export_workbook(
        rows=rows_us,
        title_label='Daily Bookings Raw Data — United States',
        columns=BOOKINGS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Bookings_Raw_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/bookings/export/ca')
@require_role('Sales.Bookings.Export')
def bookings_export_ca():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    _, rows_ca = get_bookings_raw_from_cache()
    if not rows_ca:
        return redirect(url_for('sales.bookings'))

    wb = build_export_workbook(
        rows=rows_ca,
        title_label='Daily Bookings Raw Data — Canada',
        columns=BOOKINGS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Bookings_Raw_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# BOOKINGS SUMMARY (MTD / QTD / YTD)
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/bookings-summary')
@require_role('Sales.BookingsSummary.View')
def bookings_summary():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    _, _, _, cad_rate = get_bookings_from_cache()
    summary_data = get_bookings_summary_from_cache(cad_rate)
    can_export = user_has_role(session["user"], 'Sales.BookingsSummary.Export')

    return render_template(
        'sales/bookings_summary.html',
        user=session["user"],
        data=summary_data,
        cad_rate=cad_rate,
        last_updated=summary_data.get('last_updated'),
        can_export=can_export,
    )


@sales_bp.route('/bookings-summary/export/<horizon>')
@require_role('Sales.BookingsSummary.Export')
def bookings_summary_export(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.bookings_summary'))

    _, _, _, cad_rate = get_bookings_from_cache()
    rows_us, rows_ca = fetch_bookings_raw_export_data(horizon, cad_rate)
    if not rows_us and not rows_ca:
        return redirect(url_for('sales.bookings_summary'))

    for row in rows_us:
        row['Region'] = 'US'
    for row in rows_ca:
        row['Region'] = 'CA'

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_us + rows_ca,
        title_label=f'Bookings {label} Raw Data (US + Canada)',
        columns=BOOKINGS_SUMMARY_EXPORT_COLUMNS,
        include_region_col=True,
    )
    return send_workbook(wb, f'Bookings_{label}_US_CA_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/bookings-summary/export/<horizon>/us')
@require_role('Sales.BookingsSummary.Export')
def bookings_summary_export_us(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.bookings_summary'))

    _, _, _, cad_rate = get_bookings_from_cache()
    rows_us, _ = fetch_bookings_raw_export_data(horizon, cad_rate)
    if not rows_us:
        return redirect(url_for('sales.bookings_summary'))

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_us,
        title_label=f'Bookings {label} Raw Data — United States',
        columns=BOOKINGS_SUMMARY_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Bookings_{label}_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/bookings-summary/export/<horizon>/ca')
@require_role('Sales.BookingsSummary.Export')
def bookings_summary_export_ca(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.bookings_summary'))

    _, _, _, cad_rate = get_bookings_from_cache()
    _, rows_ca = fetch_bookings_raw_export_data(horizon, cad_rate)
    if not rows_ca:
        return redirect(url_for('sales.bookings_summary'))

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_ca,
        title_label=f'Bookings {label} Raw Data — Canada',
        columns=BOOKINGS_SUMMARY_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Bookings_{label}_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# DAILY SHIPMENTS — View requires Sales.Shipments.View
#                   Export requires Sales.Shipments.Export
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/shipments')
@require_role('Sales.Shipments.View')
def shipments():
    """Daily Shipments dashboard — today's invoiced/shipped lines from artran."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    snapshot_us, snapshot_ca, last_updated, cad_rate = get_shipments_from_cache()

    us_data = _build_region_data(snapshot_us, cad_rate, is_canada=False)
    ca_data = _build_region_data(snapshot_ca, cad_rate, is_canada=True)

    error = None
    if snapshot_us is None and snapshot_ca is None:
        error = "Unable to load data. Please try again shortly."

    can_export = user_has_role(session["user"], 'Sales.Shipments.Export')

    return render_template(
        'sales/shipments.html',
        user=session["user"],
        error=error,
        us=us_data,
        ca=ca_data,
        cad_rate=cad_rate,
        last_updated=last_updated,
        can_export=can_export,
    )


@sales_bp.route('/shipments/export')
@require_role('Sales.Shipments.Export')
def shipments_export():
    """Export today's raw shipments data (US + Canada combined) — reads from cache, zero SQL."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us, rows_ca = get_shipments_raw_from_cache()
    if not rows_us and not rows_ca:
        return redirect(url_for('sales.shipments'))

    for row in rows_us:
        row['Region'] = 'US'
    for row in rows_ca:
        row['Region'] = 'CA'

    wb = build_export_workbook(
        rows=rows_us + rows_ca,
        title_label='Daily Shipments Raw Data (US + Canada)',
        columns=SHIPMENTS_EXPORT_COLUMNS,
        include_region_col=True,
    )
    return send_workbook(wb, f'Shipments_Raw_US_CA_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/shipments/export/us')
@require_role('Sales.Shipments.Export')
def shipments_export_us():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us, _ = get_shipments_raw_from_cache()
    if not rows_us:
        return redirect(url_for('sales.shipments'))

    wb = build_export_workbook(
        rows=rows_us,
        title_label='Daily Shipments Raw Data — United States',
        columns=SHIPMENTS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Shipments_Raw_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/shipments/export/ca')
@require_role('Sales.Shipments.Export')
def shipments_export_ca():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    _, rows_ca = get_shipments_raw_from_cache()
    if not rows_ca:
        return redirect(url_for('sales.shipments'))

    wb = build_export_workbook(
        rows=rows_ca,
        title_label='Daily Shipments Raw Data — Canada',
        columns=SHIPMENTS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Shipments_Raw_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# SHIPMENTS SUMMARY (MTD / QTD / YTD)
# View requires Sales.ShipmentsSummary.View
# Export requires Sales.ShipmentsSummary.Export
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/shipments-summary')
@require_role('Sales.ShipmentsSummary.View')
def shipments_summary():
    """Shipments Summary — MTD / QTD / YTD with year-over-year comparison."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    _, _, _, cad_rate = get_shipments_from_cache()
    summary_data = get_shipments_summary_from_cache(cad_rate)
    can_export = user_has_role(session["user"], 'Sales.ShipmentsSummary.Export')

    return render_template(
        'sales/shipments_summary.html',
        user=session["user"],
        data=summary_data,
        cad_rate=cad_rate,
        last_updated=summary_data.get('last_updated'),
        can_export=can_export,
    )


@sales_bp.route('/shipments-summary/export/<horizon>')
@require_role('Sales.ShipmentsSummary.Export')
def shipments_summary_export(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.shipments_summary'))

    _, _, _, cad_rate = get_shipments_from_cache()
    rows_us, rows_ca = fetch_shipments_raw_export_data(horizon, cad_rate)
    if not rows_us and not rows_ca:
        return redirect(url_for('sales.shipments_summary'))

    for row in rows_us:
        row['Region'] = 'US'
    for row in rows_ca:
        row['Region'] = 'CA'

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_us + rows_ca,
        title_label=f'Shipments {label} Raw Data (US + Canada)',
        columns=SHIPMENTS_SUMMARY_EXPORT_COLUMNS,
        include_region_col=True,
    )
    return send_workbook(wb, f'Shipments_{label}_US_CA_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/shipments-summary/export/<horizon>/us')
@require_role('Sales.ShipmentsSummary.Export')
def shipments_summary_export_us(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.shipments_summary'))

    _, _, _, cad_rate = get_shipments_from_cache()
    rows_us, _ = fetch_shipments_raw_export_data(horizon, cad_rate)
    if not rows_us:
        return redirect(url_for('sales.shipments_summary'))

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_us,
        title_label=f'Shipments {label} Raw Data — United States',
        columns=SHIPMENTS_SUMMARY_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Shipments_{label}_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/shipments-summary/export/<horizon>/ca')
@require_role('Sales.ShipmentsSummary.Export')
def shipments_summary_export_ca(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.shipments_summary'))

    _, _, _, cad_rate = get_shipments_from_cache()
    _, rows_ca = fetch_shipments_raw_export_data(horizon, cad_rate)
    if not rows_ca:
        return redirect(url_for('sales.shipments_summary'))

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_ca,
        title_label=f'Shipments {label} Raw Data — Canada',
        columns=SHIPMENTS_SUMMARY_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Shipments_{label}_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# OPEN ORDERS — View requires Sales.OpenOrders.View
#               Export requires Sales.OpenOrders.Export
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/open-orders')
@require_role('Sales.OpenOrders.View')
def open_orders():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    snapshot_us, snapshot_ca, last_updated, cad_rate = get_open_orders_from_cache()

    us_data = _build_region_data(snapshot_us, cad_rate, is_canada=False)
    ca_data = _build_region_data(snapshot_ca, cad_rate, is_canada=True)

    error = None
    if snapshot_us is None and snapshot_ca is None:
        error = "Unable to load data. Please try again shortly."

    can_export = user_has_role(session["user"], 'Sales.OpenOrders.Export')

    return render_template(
        'sales/open_orders.html',
        user=session["user"],
        error=error,
        us=us_data,
        ca=ca_data,
        cad_rate=cad_rate,
        last_updated=last_updated,
        can_export=can_export,
    )


@sales_bp.route('/open-orders/export')
@require_role('Sales.OpenOrders.Export')
def open_orders_export():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us, rows_ca = get_open_orders_raw_from_cache()
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
@require_role('Sales.OpenOrders.Export')
def open_orders_export_us():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    rows_us, _ = get_open_orders_raw_from_cache()
    if not rows_us:
        return redirect(url_for('sales.open_orders'))

    wb = build_export_workbook(
        rows=rows_us,
        title_label='Open Sales Orders — United States',
        columns=OPEN_ORDERS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Open_Orders_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/open-orders/export/ca')
@require_role('Sales.OpenOrders.Export')
def open_orders_export_ca():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    _, rows_ca = get_open_orders_raw_from_cache()
    if not rows_ca:
        return redirect(url_for('sales.open_orders'))

    wb = build_export_workbook(
        rows=rows_ca,
        title_label='Open Sales Orders — Canada',
        columns=OPEN_ORDERS_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Open_Orders_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# DASHBOARD — View requires Sales.Dashboard.View
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/dashboard')
@require_role('Sales.Dashboard.View')
def dashboard():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    selected_year = request.args.get('year', type=int, default=date.today().year)
    available_years = get_available_years()
    if selected_year not in available_years:
        selected_year = date.today().year

    _, _, _, cad_rate = get_bookings_from_cache()
    dashboard_data = get_dashboard_data(year=selected_year, cad_rate=cad_rate)

    can_export_historical = False
    if selected_year < date.today().year:
        us_raw = get_historical_raw_rows(selected_year, 'US')
        ca_raw = get_historical_raw_rows(selected_year, 'CA')
        can_export_historical = (us_raw is not None) or (ca_raw is not None)

    return render_template(
        'sales/dashboard.html',
        user=session["user"],
        data=dashboard_data,
        data_json=json.dumps(dashboard_data, default=str),
        selected_year=selected_year,
        available_years=available_years,
        cad_rate=cad_rate,
        last_updated=dashboard_data.get('last_updated'),
        can_export_historical=can_export_historical,
    )


@sales_bp.route('/dashboard/refresh', methods=['POST'])
@require_role('Sales.Dashboard.View')
def dashboard_refresh():
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year', date.today().year)
    invalidate_historical_cache(year=year)

    return jsonify({'status': 'ok', 'redirect': url_for('sales.dashboard', year=year)})


@sales_bp.route('/dashboard/export')
@require_role('Sales.Dashboard.View')
def dashboard_export():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    year = request.args.get('year', type=int)
    if not year or year >= date.today().year:
        return redirect(url_for('sales.dashboard'))

    rows_us = get_historical_raw_rows(year, 'US') or []
    rows_ca = get_historical_raw_rows(year, 'CA') or []

    if not rows_us and not rows_ca:
        return redirect(url_for('sales.dashboard', year=year))

    for row in rows_us:
        row['Region'] = 'US'
    for row in rows_ca:
        row['Region'] = 'CA'

    wb = build_export_workbook(
        rows=rows_us + rows_ca,
        title_label=f'Historical Bookings Raw Data {year} (US + Canada)',
        columns=DASHBOARD_EXPORT_COLUMNS,
        include_region_col=True,
    )
    return send_workbook(wb, f'Dashboard_Raw_US_CA_{year}.xlsx')


@sales_bp.route('/dashboard/export/us')
@require_role('Sales.Dashboard.View')
def dashboard_export_us():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    year = request.args.get('year', type=int)
    if not year or year >= date.today().year:
        return redirect(url_for('sales.dashboard'))

    rows_us = get_historical_raw_rows(year, 'US')
    if not rows_us:
        return redirect(url_for('sales.dashboard', year=year))

    wb = build_export_workbook(
        rows=rows_us,
        title_label=f'Historical Bookings Raw Data {year} — United States',
        columns=DASHBOARD_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Dashboard_Raw_US_{year}.xlsx')


@sales_bp.route('/dashboard/export/ca')
@require_role('Sales.Dashboard.View')
def dashboard_export_ca():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    year = request.args.get('year', type=int)
    if not year or year >= date.today().year:
        return redirect(url_for('sales.dashboard'))

    rows_ca = get_historical_raw_rows(year, 'CA')
    if not rows_ca:
        return redirect(url_for('sales.dashboard', year=year))

    wb = build_export_workbook(
        rows=rows_ca,
        title_label=f'Historical Bookings Raw Data {year} — Canada',
        columns=DASHBOARD_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Dashboard_Raw_CA_{year}.xlsx')