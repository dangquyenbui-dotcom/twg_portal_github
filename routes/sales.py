"""
Sales Blueprint
Routes for all sales reports: bookings, bookings summary, shipments (consolidated),
my sales tracker, open orders, executive dashboard, and Excel exports.

Per-report role mapping (using Security Groups):
  /sales                                  → Sales.Base  (auto-implied by ANY Sales.*.View)

  -- BOOKINGS (orders placed) --
  /sales/bookings                         → Sales.Bookings.View
  /sales/bookings/export/*                → Sales.Bookings.Export
  /sales/bookings-summary                 → Sales.BookingsSummary.View
  /sales/bookings-summary/export/*        → Sales.BookingsSummary.Export

  -- SHIPMENTS (consolidated: daily + MTD/QTD/YTD) --
  /sales/shipments                        → Sales.Shipments.View
  /sales/shipments/export                 → Sales.Shipments.Export  (today's data)
  /sales/shipments/export/<horizon>       → Sales.Shipments.Export  (MTD/QTD/YTD)
  /sales/shipments-summary               → redirects to /sales/shipments

  -- MY SALES TRACKER (per-salesman monthly) --
  /sales/my-tracker                       → Sales.MST.View
  /sales/my-tracker/export                → Sales.MST.Export

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
    get_mtd_by_region,
    fetch_raw_export_data as fetch_bookings_raw_export_data,
)
from services.shipments_summary_service import (
    get_shipments_summary_from_cache,
    fetch_raw_export_data as fetch_shipments_raw_export_data,
)
from services.bookings_dashboard_data_service import (
    get_dashboard_data, get_available_years, invalidate_historical_cache,
    get_historical_raw_rows,
)
from services.my_tracker_service import (
    get_salesmen_list, get_tracker_data, fetch_raw_tracker_export, get_available_months,
    get_leaderboard_data, get_winback_customers,
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

# My Sales Tracker export — same as shipments but WITHOUT Unit Cost (sensitive)
TRACKER_EXPORT_COLUMNS = [col for col in SHIPMENTS_EXPORT_COLUMNS if col[1] != 'UnitCost']


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


def _inject_territory_goals(territory_ranking, year, month):
    """
    Enrich a territory_ranking list with goal data from the cached
    SharePoint stretch-goal spreadsheet.

    Adds to each entry:
        goal         – monthly goal amount (int or None)
        pct_to_goal  – total / goal * 100 (float or None)
    """
    try:
        from services.goals_service import get_territory_goal

        for terr in territory_ranking:
            goal_info = get_territory_goal(terr['location'], year, month)
            terr['goal'] = goal_info.get('goal') if goal_info else None
            if terr.get('goal') and terr['goal'] > 0:
                terr['pct_to_goal'] = round(terr['total'] / terr['goal'] * 100, 1)
            else:
                terr['pct_to_goal'] = None
    except Exception:
        # Goals are non-critical — never break the page
        for terr in territory_ranking:
            terr.setdefault('goal', None)
            terr.setdefault('pct_to_goal', None)


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

    # Inject monthly stretch goals into territory rankings
    today = date.today()
    _inject_territory_goals(us_data["territory_ranking"], today.year, today.month)
    _inject_territory_goals(ca_data["territory_ranking"], today.year, today.month)

    # ── MTD data (per-region) for all ranking tabs ──
    mtd_regions = get_mtd_by_region(cad_rate)
    us_mtd_data = mtd_regions.get('us') or {}
    ca_mtd_data = mtd_regions.get('ca') or {}

    # Inject goals into MTD territory rankings so template can compute % = MTD / Goal
    us_mtd_terr_list = us_mtd_data.get('territory_ranking') or []
    ca_mtd_terr_list = ca_mtd_data.get('territory_ranking') or []
    _inject_territory_goals(us_mtd_terr_list, today.year, today.month)
    _inject_territory_goals(ca_mtd_terr_list, today.year, today.month)

    # Build lookup dicts keyed by name for template merging
    us_mtd_terr = {r['location']: r for r in us_mtd_terr_list}
    us_mtd_sm   = {r['salesman']: r for r in (us_mtd_data.get('salesman_ranking') or [])}
    us_mtd_cust = {r['custno']: r   for r in (us_mtd_data.get('customer_ranking') or [])}
    ca_mtd_terr = {r['location']: r for r in ca_mtd_terr_list}
    ca_mtd_sm   = {r['salesman']: r for r in (ca_mtd_data.get('salesman_ranking') or [])}
    ca_mtd_cust = {r['custno']: r   for r in (ca_mtd_data.get('customer_ranking') or [])}

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
        us_mtd_terr=us_mtd_terr,
        us_mtd_sm=us_mtd_sm,
        us_mtd_cust=us_mtd_cust,
        ca_mtd_terr=ca_mtd_terr,
        ca_mtd_sm=ca_mtd_sm,
        ca_mtd_cust=ca_mtd_cust,
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

    # Inject monthly stretch goals into MTD territory ranking
    today = date.today()
    if summary_data and summary_data.get('mtd'):
        _inject_territory_goals(
            summary_data['mtd'].get('territory_ranking', []),
            today.year, today.month,
        )

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
# SHIPMENTS (consolidated: daily pulse + MTD/QTD/YTD summary)
# View requires Sales.Shipments.View
# Export requires Sales.Shipments.Export
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/shipments')
@require_role('Sales.Shipments.View')
def shipments():
    """Consolidated Shipments — today's pulse + MTD/QTD/YTD summary with YoY."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    # --- Today's data (daily snapshot) ---
    snapshot_us, snapshot_ca, last_updated_daily, cad_rate = get_shipments_from_cache()

    us_data = _build_region_data(snapshot_us, cad_rate, is_canada=False)
    ca_data = _build_region_data(snapshot_ca, cad_rate, is_canada=True)

    # Build combined US + CA totals for "Today's Pulse" section
    today_combined = {
        "total_amount": us_data["total_amount"] + ca_data.get("total_amount_usd", 0),
        "total_units": us_data["total_units"] + ca_data["total_units"],
        "total_invoices": us_data["total_invoices"] + ca_data["total_invoices"],
        "total_orders": us_data.get("total_orders", 0) + ca_data.get("total_orders", 0),
        "us_amount": us_data["total_amount"],
        "ca_amount": ca_data["total_amount"],
        "ca_amount_usd": ca_data.get("total_amount_usd", 0),
    }

    # Today's date label
    order_date = us_data.get("order_date") or ca_data.get("order_date")
    today_label = order_date.strftime('%A, %B %d, %Y') if order_date else 'Today'

    # --- Summary data (MTD / QTD / YTD) ---
    summary_data = get_shipments_summary_from_cache(cad_rate)

    # Inject monthly stretch goals into territory rankings (MTD horizon)
    today = date.today()
    if summary_data and summary_data.get('mtd'):
        _inject_territory_goals(
            summary_data['mtd'].get('territory_ranking', []),
            today.year, today.month,
        )

    error = None
    if snapshot_us is None and snapshot_ca is None:
        error = "Unable to load data. Please try again shortly."

    can_export = user_has_role(session["user"], 'Sales.Shipments.Export')

    return render_template(
        'sales/shipments.html',
        user=session["user"],
        error=error,
        today_combined=today_combined,
        today_label=today_label,
        summary_data=summary_data,
        cad_rate=cad_rate,
        last_updated_daily=last_updated_daily,
        last_updated_summary=summary_data.get('last_updated'),
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
# SHIPMENTS SUMMARY EXPORTS (MTD / QTD / YTD)
# Now served under /shipments/export/<horizon>
# Old /shipments-summary URL redirects to /shipments
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/shipments-summary')
def shipments_summary_redirect():
    """Redirect old /shipments-summary URL to consolidated /shipments page."""
    return redirect(url_for('sales.shipments'), code=302)


@sales_bp.route('/shipments/export/<horizon>')
@require_role('Sales.Shipments.Export')
def shipments_summary_export(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.shipments'))

    _, _, _, cad_rate = get_shipments_from_cache()
    rows_us, rows_ca = fetch_shipments_raw_export_data(horizon, cad_rate)
    if not rows_us and not rows_ca:
        return redirect(url_for('sales.shipments'))

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


@sales_bp.route('/shipments/export/<horizon>/us')
@require_role('Sales.Shipments.Export')
def shipments_summary_export_us(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.shipments'))

    _, _, _, cad_rate = get_shipments_from_cache()
    rows_us, _ = fetch_shipments_raw_export_data(horizon, cad_rate)
    if not rows_us:
        return redirect(url_for('sales.shipments'))

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_us,
        title_label=f'Shipments {label} Raw Data — United States',
        columns=SHIPMENTS_SUMMARY_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Shipments_{label}_US_{date.today().strftime("%Y%m%d")}.xlsx')


@sales_bp.route('/shipments/export/<horizon>/ca')
@require_role('Sales.Shipments.Export')
def shipments_summary_export_ca(horizon):
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    if horizon not in ('mtd', 'qtd', 'ytd'):
        return redirect(url_for('sales.shipments'))

    _, _, _, cad_rate = get_shipments_from_cache()
    _, rows_ca = fetch_shipments_raw_export_data(horizon, cad_rate)
    if not rows_ca:
        return redirect(url_for('sales.shipments'))

    label = horizon.upper()
    wb = build_export_workbook(
        rows=rows_ca,
        title_label=f'Shipments {label} Raw Data — Canada',
        columns=SHIPMENTS_SUMMARY_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'Shipments_{label}_CA_{date.today().strftime("%Y%m%d")}.xlsx')


# ═══════════════════════════════════════════════════════════════
# MY SALES TRACKER — Per-salesman monthly report
#   View requires Sales.MST.View (dedicated security group)
#   Export requires Sales.MST.Export
#   Non-admin users are always locked to their own EmployeeId code
# ═══════════════════════════════════════════════════════════════

@sales_bp.route('/my-tracker')
@require_role('Sales.MST.View')
def my_tracker():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    user = session["user"]
    is_admin = 'Admin' in user.get('roles', [])
    salesman_code = user.get('salesman_code', '').strip()

    # Parse query params
    selected_year = request.args.get('year', type=int, default=date.today().year)
    selected_month = request.args.get('month', type=int, default=date.today().month)
    selected_region = request.args.get('region', 'US').upper()
    if selected_region not in ('US', 'CA'):
        selected_region = 'US'

    # Get available months for the selector
    available_months = get_available_months()

    # Get salesman list for the selected month + region
    salesmen = get_salesmen_list(selected_year, selected_month, region=selected_region)

    # Determine which salesman to display
    if is_admin:
        # Admin can pick any salesman
        selected_salesman = request.args.get('salesman', '')
        if not selected_salesman and salesmen:
            selected_salesman = salesmen[0]
        not_configured = False
    elif salesman_code:
        # Regular user locked to their code
        selected_salesman = salesman_code
        not_configured = False
    else:
        # User has no salesman code and is not admin
        selected_salesman = ''
        not_configured = True

    # Fetch data if we have a salesman
    data = None
    ly_by_day = []
    if selected_salesman and not not_configured:
        data = get_tracker_data(selected_salesman, selected_year, selected_month, region=selected_region)
        # Last year same month for cumulative comparison
        ly_year = selected_year - 1
        ly_data = get_tracker_data(selected_salesman, ly_year, selected_month, region=selected_region)
        ly_by_day = ly_data.get('by_day', []) if ly_data else []

    can_export = user_has_role(user, 'Sales.MST.Export')

    # Leaderboard — all salesmen ranked by total invoiced (shared cache)
    leaderboard = []
    if not not_configured:
        leaderboard = get_leaderboard_data(selected_year, selected_month, region=selected_region)

    # Win-back opportunities — lapsed customers from same month last year
    winback_customers = []
    if selected_salesman and not not_configured and data:
        winback_customers = get_winback_customers(
            selected_salesman, selected_year, selected_month, region=selected_region
        )

    # Currency label for display
    currency_label = 'CAD' if selected_region == 'CA' else 'USD'

    # Build month label for display
    month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                   'July', 'August', 'September', 'October', 'November', 'December']
    month_label = f"{month_names[selected_month]} {selected_year}" if 1 <= selected_month <= 12 else ''
    ly_month_label = f"{month_names[selected_month]} {selected_year - 1}" if 1 <= selected_month <= 12 else ''

    # Is this the current month? (data is live from ERP)
    is_current_month = (selected_year == date.today().year and selected_month == date.today().month)
    # Use the actual SQL fetch timestamp from cached data, not page-load time
    last_updated = data.get('fetched_at') if data else None

    # Territory goal for this salesman's primary territory
    territory_goal = None
    territory_name = None
    territory_invoiced = None
    region_goal = None
    region_name = None
    region_invoiced = None
    if data and data.get('primary_territory'):
        territory_name = data['primary_territory']
        try:
            from services.goals_service import get_territory_goal
            goal_info = get_territory_goal(territory_name, selected_year, selected_month)
            territory_goal = goal_info.get('goal') if goal_info else None
        except Exception:
            pass  # Goals are non-critical
        if territory_goal:
            try:
                from services.my_tracker_service import get_territory_invoiced
                territory_invoiced = get_territory_invoiced(
                    territory_name, selected_year, selected_month, region=selected_region
                )
            except Exception:
                pass  # Territory total is non-critical

        # Region goal — look up which region this territory belongs to
        try:
            from services.constants import TERRITORY_TO_REGION
            from services.goals_service import get_region_goal
            from services.my_tracker_service import get_region_invoiced
            region_key = TERRITORY_TO_REGION.get(territory_name)
            if region_key:
                region_name = region_key  # e.g. 'WEST', 'SOUTHEAST'
                region_info = get_region_goal(region_key, selected_year, selected_month)
                region_goal = region_info.get('goal') if region_info else None
                if region_goal:
                    region_invoiced = get_region_invoiced(
                        region_key, selected_year, selected_month, region=selected_region
                    )
        except Exception:
            pass  # Region goals are non-critical

    # Territory & region daily invoiced — for cumulative chart
    territory_daily = None
    region_daily = None
    if territory_name:
        try:
            from services.my_tracker_service import get_territory_daily_invoiced
            territory_daily = get_territory_daily_invoiced(
                territory_name, selected_year, selected_month, region=selected_region
            )
        except Exception:
            pass
    if region_name:
        try:
            from services.my_tracker_service import get_region_daily_invoiced
            region_daily = get_region_daily_invoiced(
                region_name, selected_year, selected_month, region=selected_region
            )
        except Exception:
            pass

    # Estimated commission
    commission_data = None
    if data and data.get('total_margin') is not None and selected_salesman:
        try:
            from services.commission_service import calculate_commission
            commission_data = calculate_commission(
                total_margin=data['total_margin'],
                salesman_code=selected_salesman,
                territory_invoiced=territory_invoiced,
                territory_goal=territory_goal,
                year=selected_year,
                month=selected_month,
            )
        except Exception:
            pass  # Commission is non-critical

    return render_template(
        'sales/my_tracker.html',
        user=user,
        is_admin=is_admin,
        salesmen=salesmen,
        selected_salesman=selected_salesman,
        selected_year=selected_year,
        selected_month=selected_month,
        selected_region=selected_region,
        available_months=available_months,
        month_label=month_label,
        ly_month_label=ly_month_label,
        data=data,
        ly_by_day=ly_by_day,
        not_configured=not_configured,
        is_current_month=is_current_month,
        last_updated=last_updated,
        can_export=can_export,
        currency_label=currency_label,
        leaderboard=leaderboard,
        winback_customers=winback_customers,
        territory_goal=territory_goal,
        territory_name=territory_name,
        territory_invoiced=territory_invoiced,
        region_goal=region_goal,
        region_name=region_name,
        region_invoiced=region_invoiced,
        territory_daily=territory_daily,
        region_daily=region_daily,
        commission_data=commission_data,
    )


@sales_bp.route('/my-tracker/export')
@require_role('Sales.MST.Export')
def my_tracker_export():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    user = session["user"]
    is_admin = 'Admin' in user.get('roles', [])
    salesman_code = user.get('salesman_code', '').strip()

    salesman = request.args.get('salesman', '')
    year = request.args.get('year', type=int, default=date.today().year)
    month = request.args.get('month', type=int, default=date.today().month)
    region = request.args.get('region', 'US').upper()
    if region not in ('US', 'CA'):
        region = 'US'

    # Enforce: non-admin can only export their own data
    if not is_admin and salesman_code:
        salesman = salesman_code
    elif not is_admin:
        return redirect(url_for('sales.my_tracker'))

    if not salesman:
        return redirect(url_for('sales.my_tracker'))

    rows = fetch_raw_tracker_export(salesman, year, month, region=region)
    if not rows:
        return redirect(url_for('sales.my_tracker', salesman=salesman, year=year, month=month, region=region))

    region_label = 'CA' if region == 'CA' else 'US'
    wb = build_export_workbook(
        rows=rows,
        title_label=f'My Sales Tracker — {salesman} ({region_label}) — {year}-{month:02d}',
        columns=TRACKER_EXPORT_COLUMNS,
    )
    return send_workbook(wb, f'MyTracker_{salesman}_{region_label}_{year}{month:02d}_{date.today().strftime("%Y%m%d")}.xlsx')


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