"""
Bookings Query Service
Fetches today's daily bookings data (snapshot for dashboard + raw for Excel export).
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

def _build_bookings_query(database):
    """Build the bookings snapshot query for a given database."""
    return f"""
    SELECT
        tr.sono,
        tr.origqtyord                           AS units,
        tr.origqtyord * tr.price                AS amount,
        CASE WHEN cu.terr = '900'
             THEN cu.terr
             ELSE sm.terr
        END                                     AS terr_code,
        tr.custno,
        ic.plinid
    FROM {database}.dbo.sotran tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.ordate = CAST(GETDATE() AS DATE)
      AND tr.currhist <> 'X'
      AND tr.sostat  NOT IN ('V', 'X')
      AND tr.sotype  NOT IN ('B', 'R')
    """


def _aggregate_bookings(rows, region='US'):
    """
    Aggregate raw rows into summary and ranking.
    All amounts are rounded up (ceiling) to whole numbers.
    """
    total_amount = 0.0
    total_units = 0
    distinct_orders = set()
    territory_totals = defaultdict(float)

    for sono, units, amount, terr_code, custno, plinid in rows:
        custno_clean = (custno or '').strip().upper()
        if custno_clean in BOOKINGS_EXCLUDED_CUSTOMERS:
            continue

        if (plinid or '').strip().upper() == 'TAX':
            continue

        territory = map_territory(terr_code, region)
        amt = float(amount or 0)
        qty = int(units or 0)

        total_amount += amt
        total_units += qty
        distinct_orders.add(sono)
        territory_totals[territory] += amt

    total_amount = math.ceil(total_amount)

    ranking_sorted = sorted(territory_totals.items(), key=lambda x: x[1], reverse=True)
    ranking = [
        {"location": loc, "total": math.ceil(total), "rank": rank}
        for rank, (loc, total) in enumerate(ranking_sorted, start=1)
    ]

    summary = {
        "order_date": date.today(),
        "total_amount": total_amount,
        "total_units": total_units,
        "total_orders": len(distinct_orders),
        "total_territories": len(territory_totals),
    }

    return {"summary": summary, "ranking": ranking}


def fetch_bookings_snapshot(database=None, region='US'):
    """
    Fetch today's bookings for a given region.
    Returns dict with 'summary' and 'ranking', or None on failure.
    """
    db = database or Config.DB_ORDERS
    query = _build_bookings_query(db)

    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        result = _aggregate_bookings(rows, region=region)

        label = "US" if region == "US" else "CA"
        logger.info(
            f"{label} Bookings snapshot: ${result['summary']['total_amount']:,} "
            f"across {result['summary']['total_territories']} territories "
            f"({len(rows)} raw rows processed)"
        )
        return result

    except Exception as e:
        label = "US" if region == "US" else "CA"
        logger.error(f"{label} Bookings query failed: {e}")
        return None


def fetch_bookings_snapshot_us():
    """Fetch today's bookings for US (PRO05)."""
    return fetch_bookings_snapshot(Config.DB_ORDERS, region='US')


def fetch_bookings_snapshot_ca():
    """Fetch today's bookings for Canada (PRO06)."""
    return fetch_bookings_snapshot(Config.DB_ORDERS_CA, region='CA')


# ─────────────────────────────────────────────────────────────
# Raw export queries (for Excel — full line-item detail)
# ─────────────────────────────────────────────────────────────

def _build_bookings_raw_query(database):
    """Build the raw bookings export query for a given database."""
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
        tr.origqtyord * tr.price AS ExtAmount,
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
    FROM {database}.dbo.sotran tr WITH (NOLOCK)
    LEFT JOIN {database}.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN {database}.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN {database}.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.ordate = CAST(GETDATE() AS DATE)
      AND tr.currhist <> 'X'
      AND tr.sostat  NOT IN ('V', 'X')
      AND tr.sotype  NOT IN ('B', 'R')
    """


def _process_bookings_raw_rows(cursor, rows, region='US'):
    """Process raw bookings query rows into list of dicts with territory mapping."""
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
                    'ProductLine', 'Salesman', 'Location', 'ShipVia'):
            if record.get(key):
                record[key] = str(record[key]).strip()

        results.append(record)

    return results


def fetch_bookings_raw(database=None, region='US'):
    """
    Fetch today's raw bookings line items for Excel export.
    Returns list of dicts, or None on failure.
    """
    db = database or Config.DB_ORDERS
    query = _build_bookings_raw_query(db)

    try:
        conn = get_connection(db)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        results = _process_bookings_raw_rows(cursor, rows, region=region)
        cursor.close()
        conn.close()

        label = "US" if region == "US" else "CA"
        logger.info(f"{label} Bookings raw export: {len(results)} rows")
        return results

    except Exception as e:
        label = "US" if region == "US" else "CA"
        logger.error(f"{label} Bookings raw query failed: {e}")
        return None


def fetch_bookings_raw_us():
    """Fetch today's raw US bookings for export."""
    return fetch_bookings_raw(Config.DB_ORDERS, region='US')


def fetch_bookings_raw_ca():
    """Fetch today's raw Canada bookings for export."""
    return fetch_bookings_raw(Config.DB_ORDERS_CA, region='CA')