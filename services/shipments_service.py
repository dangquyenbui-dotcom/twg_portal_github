"""
Shipments Query Service
Fetches today's daily shipments (invoiced/shipped) data from AR transaction tables.

Data sources:
  - artran  → current month invoiced line items (live transactional data)
  - arytrn  → historical invoiced line items (completed months, identical schema)

Shipments are invoice-level records — once an order is shipped and invoiced, a line
appears in artran. At month-end the ERP moves artran rows into arytrn (same pattern
as sotran → soytrn for bookings).

Key differences from bookings:
  - Amount uses extprice (ERP pre-calculated shipped amount) instead of origqtyord × price × (1 - disc/100)
  - Quantity uses qtyshp (quantity actually shipped) instead of origqtyord
  - Territory comes directly from artran.terr (no somast join needed)
  - Date field is invdte (invoice date) instead of ordate (order date)
  - Only filter is currhist <> 'X' — no sostat/sotype filters (those are SO-level)
  - Distinct count is invno (invoices) instead of sono (sales orders)
  - Credit memos (artype = 'C') are excluded from daily shipments dashboard
"""

import logging
import math
from collections import defaultdict
from datetime import date

from config import Config
from services.db_connection import get_connection
from services.constants import (
    BOOKINGS_EXCLUDED_CUSTOMERS,
    map_territory,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Snapshot queries (for dashboard — lean rows, Python aggregation)
# ─────────────────────────────────────────────────────────────

def _build_shipments_query(database):
    """Build the daily shipments snapshot query for a given database."""
    return f"""
    SELECT
        tr.invno,
        tr.sono,
        tr.qtyshp                                AS units,
        tr.extprice                               AS amount,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE tr.terr
        END                                       AS terr_code,
        tr.custno,
        ic.plinid,
        tr.salesmn,
        cu.company                                AS cust_name
    FROM {database}.dbo.artran tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.invdte = CAST(GETDATE() AS DATE)
      AND tr.currhist <> 'X'
      AND tr.artype <> 'C'
    """


def _aggregate_shipments(rows, region='US'):
    """
    Aggregate raw rows into summary and three rankings:
      - territory_ranking  (default)
      - salesman_ranking
      - customer_ranking
    All amounts are rounded up (ceiling) to whole numbers.
    """
    total_amount = 0.0
    total_units = 0
    distinct_invoices = set()
    distinct_orders = set()
    territory_totals = defaultdict(float)
    salesman_totals = defaultdict(float)
    customer_totals = defaultdict(lambda: {'name': '', 'amount': 0.0})

    for invno, sono, units, amount, terr_code, custno, plinid, salesmn, cust_name in rows:
        custno_clean = (custno or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue

        if (plinid or '').strip().upper() == 'TAX':
            continue

        territory = map_territory(terr_code, region)
        salesman = (salesmn or '').strip() or 'Unassigned'
        customer_key = custno_clean
        customer_display = (cust_name or '').strip() or custno_clean

        amt = float(amount or 0)
        qty = int(units or 0)

        total_amount += amt
        total_units += qty
        if invno:
            distinct_invoices.add(invno)
        if sono:
            distinct_orders.add(sono)
        territory_totals[territory] += amt
        salesman_totals[salesman] += amt
        customer_totals[customer_key]['amount'] += amt
        customer_totals[customer_key]['name'] = customer_display

    total_amount = math.ceil(total_amount)

    # Territory ranking
    terr_sorted = sorted(territory_totals.items(), key=lambda x: x[1], reverse=True)
    territory_ranking = [
        {"location": loc, "total": math.ceil(total), "rank": rank}
        for rank, (loc, total) in enumerate(terr_sorted, start=1)
    ]

    # Salesman ranking
    sm_sorted = sorted(salesman_totals.items(), key=lambda x: x[1], reverse=True)
    salesman_ranking = [
        {"salesman": sm, "total": math.ceil(total), "rank": rank}
        for rank, (sm, total) in enumerate(sm_sorted, start=1)
    ]

    # Customer ranking
    cust_sorted = sorted(customer_totals.items(), key=lambda x: x[1]['amount'], reverse=True)
    customer_ranking = [
        {"customer": v['name'], "custno": k, "total": math.ceil(v['amount']), "rank": rank}
        for rank, (k, v) in enumerate(cust_sorted, start=1)
    ]

    summary = {
        "order_date": date.today(),
        "total_amount": total_amount,
        "total_units": total_units,
        "total_invoices": len(distinct_invoices),
        "total_orders": len(distinct_orders),
        "total_territories": len(territory_totals),
    }

    return {
        "summary": summary,
        "ranking": territory_ranking,
        "salesman_ranking": salesman_ranking,
        "customer_ranking": customer_ranking,
    }


def fetch_shipments_snapshot(database=None, region='US'):
    """
    Fetch today's shipments for a given region.
    Returns dict with 'summary', 'ranking', 'salesman_ranking', 'customer_ranking', or None on failure.
    """
    db = database or Config.DB_ORDERS
    query = _build_shipments_query(db)

    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        result = _aggregate_shipments(rows, region=region)

        label = "US" if region == "US" else "CA"
        logger.info(
            f"{label} Shipments snapshot: ${result['summary']['total_amount']:,} "
            f"across {result['summary']['total_territories']} territories "
            f"({len(rows)} raw rows processed)"
        )
        return result

    except Exception as e:
        label = "US" if region == "US" else "CA"
        logger.error(f"{label} Shipments query failed: {e}")
        return None


def fetch_shipments_snapshot_us():
    """Fetch today's shipments for US (PRO05)."""
    return fetch_shipments_snapshot(Config.DB_ORDERS, region='US')


def fetch_shipments_snapshot_ca():
    """Fetch today's shipments for Canada (PRO06)."""
    return fetch_shipments_snapshot(Config.DB_ORDERS_CA, region='CA')


# ─────────────────────────────────────────────────────────────
# Raw export queries (for Excel — full line-item detail)
# ─────────────────────────────────────────────────────────────

def _build_shipments_raw_query(database):
    """Build the raw shipments export query for a given database."""
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
    FROM {database}.dbo.artran tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.invdte = CAST(GETDATE() AS DATE)
      AND tr.currhist <> 'X'
      AND tr.artype <> 'C'
    """


def _process_shipments_raw_rows(cursor, rows, region='US'):
    """Process raw shipments query rows into list of dicts with territory mapping."""
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
                    'ProductLine', 'Salesman', 'Location', 'PONumber',
                    'InvoiceNo', 'SalesOrder', 'Batch', 'Currency',
                    'InvoiceStatus', 'InvoiceType'):
            if record.get(key):
                record[key] = str(record[key]).strip()

        results.append(record)

    return results


def fetch_shipments_raw(database=None, region='US'):
    """
    Fetch today's raw shipment line items for Excel export.
    Returns list of dicts, or None on failure.
    """
    db = database or Config.DB_ORDERS
    query = _build_shipments_raw_query(db)

    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        results = _process_shipments_raw_rows(cursor, rows, region=region)
        cursor.close()
        conn.close()

        label = "US" if region == "US" else "CA"
        logger.info(f"{label} Shipments raw export: {len(results)} rows")
        return results

    except Exception as e:
        label = "US" if region == "US" else "CA"
        logger.error(f"{label} Shipments raw query failed: {e}")
        return None


def fetch_shipments_raw_us():
    """Fetch today's raw US shipments for export."""
    return fetch_shipments_raw(Config.DB_ORDERS, region='US')


def fetch_shipments_raw_ca():
    """Fetch today's raw Canada shipments for export."""
    return fetch_shipments_raw(Config.DB_ORDERS_CA, region='CA')