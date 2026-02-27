"""
Open Orders Query Service
Fetches all currently open sales order lines (snapshot for dashboard + raw for Excel export).

Open line definition:
  - sotran.qtyord > 0            (still has remaining/open quantity)
  - sotran.sostat NOT IN ('C','V','X') (not closed/voided/cancelled at line level)
  - somast.sostat <> 'C'          (order not fully closed)
  - sotran.sotype NOT IN ('B','R') (no blankets/returns)
  - icitem.plinid <> 'TAX'        (no tax line items)
  - Excluded customers             (same as bookings: W1VAN, W1TOR, W1MON, MISC, TWGMARKET, EMP-US, TEST123)
  - NO date filter                 (all open orders regardless of age)
  - NO currhist filter             (currhist is not relevant for open orders)

Open $ = sotran.qtyord × sotran.price × (1 - sotran.disc / 100)  (qtyord = remaining open qty after shipments)
"""

import logging
import math
from collections import defaultdict

from config import Config
from services.db_connection import get_connection
from services.constants import map_territory, BOOKINGS_EXCLUDED_CUSTOMERS

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Snapshot queries (for dashboard — lean rows, Python aggregation)
# ─────────────────────────────────────────────────────────────

def _build_open_orders_query(database):
    """Build the open orders snapshot query for a given database."""
    return f"""
    SELECT
        tr.sono,
        tr.qtyord                               AS open_qty,
        tr.qtyord * tr.price * (1 - tr.disc / 100.0)
                                                AS open_amount,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE sm.terr
        END                                     AS terr_code,
        tr.salesmn,
        ic.plinid,
        tr.custno
    FROM {database}.dbo.sotran tr WITH (NOLOCK)
    INNER JOIN {database}.dbo.somast sm WITH (NOLOCK)
        ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK)
        ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK)
        ON ic.item = tr.item
    WHERE tr.qtyord > 0
      AND tr.sostat  NOT IN ('C', 'V', 'X')
      AND sm.sostat  <> 'C'
      AND tr.sotype  NOT IN ('B', 'R')
    """


def _aggregate_open_orders(rows, region='US'):
    """
    Aggregate open order rows into summary, territory ranking, and salesman ranking.
    All amounts are rounded up (ceiling) to whole numbers.

    Returns dict with 'summary', 'territory_ranking', and 'salesman_ranking'.
    """
    total_amount = 0.0
    total_units = 0
    total_lines = 0
    distinct_orders = set()
    territory_totals = defaultdict(float)
    salesman_totals = defaultdict(float)

    for sono, open_qty, open_amount, terr_code, salesmn, plinid, custno in rows:
        # Exclude internal/test customers
        custno_clean = (custno or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue

        # Exclude TAX line items
        if (plinid or '').strip().upper() == 'TAX':
            continue

        territory = map_territory(terr_code, region)
        salesman = (salesmn or '').strip() or 'Unassigned'
        amt = float(open_amount or 0)
        qty = int(open_qty or 0)

        total_amount += amt
        total_units += qty
        total_lines += 1
        distinct_orders.add(sono)
        territory_totals[territory] += amt
        salesman_totals[salesman] += amt

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

    summary = {
        "total_amount": total_amount,
        "total_units": total_units,
        "total_lines": total_lines,
        "total_orders": len(distinct_orders),
        "total_territories": len(territory_totals),
    }

    return {
        "summary": summary,
        "territory_ranking": territory_ranking,
        "salesman_ranking": salesman_ranking,
    }


def fetch_open_orders_snapshot(database=None, region='US'):
    """
    Fetch open orders snapshot for a given region.
    Returns dict with 'summary', 'territory_ranking', 'salesman_ranking', or None on failure.
    """
    db = database or Config.DB_ORDERS
    query = _build_open_orders_query(db)

    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        result = _aggregate_open_orders(rows, region=region)

        label = "US" if region == "US" else "CA"
        logger.info(
            f"{label} Open Orders snapshot: ${result['summary']['total_amount']:,} "
            f"across {result['summary']['total_orders']} orders, "
            f"{result['summary']['total_lines']} lines "
            f"({len(rows)} raw rows processed)"
        )
        return result

    except Exception as e:
        label = "US" if region == "US" else "CA"
        logger.error(f"{label} Open Orders query failed: {e}")
        return None


def fetch_open_orders_snapshot_us():
    """Fetch open orders snapshot for US (PRO05)."""
    return fetch_open_orders_snapshot(Config.DB_ORDERS, region='US')


def fetch_open_orders_snapshot_ca():
    """Fetch open orders snapshot for Canada (PRO06)."""
    return fetch_open_orders_snapshot(Config.DB_ORDERS_CA, region='CA')


# ─────────────────────────────────────────────────────────────
# Raw export queries (for Excel — full line-item detail)
# ─────────────────────────────────────────────────────────────

def _build_open_orders_raw_query(database):
    """Build the raw open orders export query for a given database."""
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
        tr.origqtyord        AS OrigQtyOrd,
        tr.qtyord            AS OpenQty,
        tr.qtyshp            AS QtyShipped,
        tr.price             AS UnitPrice,
        tr.disc              AS Discount,
        tr.qtyord * tr.price * (1 - tr.disc / 100.0)
                             AS OpenAmount,
        tr.sostat            AS LineStatus,
        tr.sotype            AS OrderType,
        sm.release           AS Release,
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
    FROM {database}.dbo.sotran tr WITH (NOLOCK)
    INNER JOIN {database}.dbo.somast sm WITH (NOLOCK)
        ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK)
        ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK)
        ON ic.item = tr.item
    WHERE tr.qtyord > 0
      AND tr.sostat  NOT IN ('C', 'V', 'X')
      AND sm.sostat  <> 'C'
      AND tr.sotype  NOT IN ('B', 'R')
    """


def _process_open_orders_raw_rows(cursor, rows, region='US'):
    """Process raw open orders query rows into list of dicts."""
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

        # Map territory
        terr_code = (record.get('TerrCode') or '').strip()
        record['Territory'] = map_territory(terr_code, region)

        # Clean string fields
        for key in ('CustomerNo', 'CustomerName', 'Item', 'Description',
                    'ProductLine', 'Release', 'Salesman', 'Location', 'ShipVia'):
            if record.get(key):
                record[key] = str(record[key]).strip()

        results.append(record)

    return results


def fetch_open_orders_raw(database=None, region='US'):
    """
    Fetch raw open order line items for Excel export.
    Returns list of dicts, or None on failure.
    """
    db = database or Config.DB_ORDERS
    query = _build_open_orders_raw_query(db)

    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        results = _process_open_orders_raw_rows(cursor, rows, region=region)
        cursor.close()
        conn.close()

        label = "US" if region == "US" else "CA"
        logger.info(f"{label} Open Orders raw export: {len(results)} rows")
        return results

    except Exception as e:
        label = "US" if region == "US" else "CA"
        logger.error(f"{label} Open Orders raw query failed: {e}")
        return None


def fetch_open_orders_raw_us():
    """Fetch raw US open orders for export."""
    return fetch_open_orders_raw(Config.DB_ORDERS, region='US')


def fetch_open_orders_raw_ca():
    """Fetch raw Canada open orders for export."""
    return fetch_open_orders_raw(Config.DB_ORDERS_CA, region='CA')