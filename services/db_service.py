"""
Database Service
Handles all SQL Server connections and queries.

Strategy: Pull minimal filtered rows from SQL Server with a lean query,
then do all aggregation, territory mapping, and ranking in Python.
This keeps SQL Server load low and leverages the app server's CPU instead.
"""

import logging
import pyodbc
from collections import defaultdict
from datetime import date
from config import Config

logger = logging.getLogger(__name__)

# ── Territory mapping (code → display name) ──
TERRITORY_MAP = {
    '000': 'LA',
    '001': 'LA',
    '010': 'China',
    '114': 'Seattle',
    '126': 'Denver',
    '204': 'Columbus',
    '206': 'Jacksonville',
    '210': 'Houston',
    '211': 'Dallas',
    '218': 'San Antonio',
    '221': 'Kansas City',
    '302': 'Nashville',
    '305': 'Levittown,PA',
    '307': 'Charlotte',
    '312': 'Atlanta',
    '324': 'Indianapolis',
    '900': 'Central Billing',
}

# ── Canada territory mapping (code → display name) ──
TERRITORY_MAP_CA = {
    '000': 'Toronto',
    '001': 'Toronto',
    '100': 'Vancouver',
    '200': 'Montreal',
    '300': 'Calgary',
    '900': 'Central Billing',
}

# ── Excluded customers ──
EXCLUDED_CUSTOMERS = frozenset({
    'W1VAN', 'W1TOR', 'W1MON', 'MISC', 'TWGMARKET', 'EMP-US', 'TEST123'
})


def get_connection(database=None):
    """Create and return a pyodbc connection with a connection timeout."""
    conn_str = Config.get_connection_string(database)
    return pyodbc.connect(conn_str, timeout=30)


def _map_territory(code, region='US'):
    """Map a territory code to its display name."""
    if region == 'CA':
        return TERRITORY_MAP_CA.get(code, 'Others')
    return TERRITORY_MAP.get(code, 'Others')


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
    Returns dict with 'summary' and 'ranking', or None if no data.
    """
    total_amount = 0.0
    total_units = 0
    distinct_orders = set()
    territory_totals = defaultdict(float)

    for sono, units, amount, terr_code, custno, plinid in rows:
        custno_clean = (custno or '').strip().upper()
        if custno_clean in EXCLUDED_CUSTOMERS:
            continue

        if (plinid or '').strip().upper() == 'TAX':
            continue

        territory = _map_territory((terr_code or '').strip(), region)

        amt = float(amount or 0)
        qty = int(units or 0)

        total_amount += amt
        total_units += qty
        distinct_orders.add(sono)
        territory_totals[territory] += amt

    ranking_sorted = sorted(territory_totals.items(), key=lambda x: x[1], reverse=True)
    ranking = [
        {"location": loc, "total": total, "rank": rank}
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


def fetch_bookings_snapshot():
    """
    Fetch today's bookings for US (PRO05).
    Returns dict with 'summary' and 'ranking', or None on failure.
    """
    query = _build_bookings_query(Config.DB_ORDERS)

    try:
        conn = get_connection(Config.DB_ORDERS)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        result = _aggregate_bookings(rows, region='US')

        logger.info(
            f"US Bookings snapshot: ${result['summary']['total_amount']:,.0f} "
            f"across {result['summary']['total_territories']} territories "
            f"({len(rows)} raw rows processed)"
        )
        return result

    except Exception as e:
        logger.error(f"US Bookings query failed: {e}")
        return None


def fetch_bookings_snapshot_ca():
    """
    Fetch today's bookings for Canada (PRO06).
    Returns dict with 'summary' and 'ranking', or None on failure.
    """
    query = _build_bookings_query(Config.DB_ORDERS_CA)

    try:
        conn = get_connection(Config.DB_ORDERS_CA)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        result = _aggregate_bookings(rows, region='CA')

        logger.info(
            f"CA Bookings snapshot: ${result['summary']['total_amount']:,.0f} "
            f"across {result['summary']['total_territories']} territories "
            f"({len(rows)} raw rows processed)"
        )
        return result

    except Exception as e:
        logger.error(f"CA Bookings query failed: {e}")
        return None


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


def _process_raw_rows(cursor, rows, region='US'):
    """Process raw query rows into list of dicts with territory mapping."""
    columns = [col[0] for col in cursor.description]
    results = []

    for row in rows:
        record = dict(zip(columns, row))

        # Exclude customers
        custno_clean = (record.get('CustomerNo') or '').strip().upper()
        if custno_clean in EXCLUDED_CUSTOMERS:
            continue

        # Exclude TAX line items
        if (record.get('ProductLine') or '').strip().upper() == 'TAX':
            continue

        # Map territory
        terr_code = (record.get('TerrCode') or '').strip()
        record['Territory'] = _map_territory(terr_code, region)

        # Clean up string fields
        for key in ('CustomerNo', 'CustomerName', 'Item', 'Description',
                    'ProductLine', 'Salesman', 'Location', 'ShipVia'):
            if record.get(key):
                record[key] = str(record[key]).strip()

        results.append(record)

    return results


def fetch_bookings_raw():
    """
    Fetch today's raw US bookings line items for Excel export.
    Returns list of dicts, or None on failure.
    """
    query = _build_bookings_raw_query(Config.DB_ORDERS)

    try:
        conn = get_connection(Config.DB_ORDERS)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        results = _process_raw_rows(cursor, rows, region='US')
        cursor.close()
        conn.close()

        logger.info(f"US Bookings raw export: {len(results)} rows")
        return results

    except Exception as e:
        logger.error(f"US Bookings raw query failed: {e}")
        return None


def fetch_bookings_raw_ca():
    """
    Fetch today's raw Canada bookings line items for Excel export.
    Returns list of dicts, or None on failure.
    """
    query = _build_bookings_raw_query(Config.DB_ORDERS_CA)

    try:
        conn = get_connection(Config.DB_ORDERS_CA)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        results = _process_raw_rows(cursor, rows, region='CA')
        cursor.close()
        conn.close()

        logger.info(f"CA Bookings raw export: {len(results)} rows")
        return results

    except Exception as e:
        logger.error(f"CA Bookings raw query failed: {e}")
        return None