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

# ── Excluded customers ──
EXCLUDED_CUSTOMERS = frozenset({
    'W1VAN', 'W1TOR', 'W1MON', 'MISC', 'TWGMARKET', 'EMP-US', 'TEST123'
})


def get_connection(database=None):
    """Create and return a pyodbc connection with a connection timeout."""
    conn_str = Config.get_connection_string(database)
    return pyodbc.connect(conn_str, timeout=30)


def _map_territory(code):
    """Map a territory code to its display name."""
    return TERRITORY_MAP.get(code, 'Others')


def fetch_bookings_snapshot():
    """
    Fetch today's bookings with a lean SQL query, then aggregate in Python.

    SQL Server only does: filter by date/status, join for territory code + plinid.
    Python does: customer exclusion, TAX filtering, territory mapping,
                 SUM, COUNT DISTINCT, ranking.

    Returns dict with 'summary' and 'ranking', or None on failure.
    """

    query = """
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
    FROM PRO05.dbo.sotran tr WITH (NOLOCK)
    LEFT JOIN PRO05.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN PRO05.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN PRO05.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.ordate = CAST(GETDATE() AS DATE)
      AND tr.currhist <> 'X'
      AND tr.sostat  NOT IN ('V', 'X')
      AND tr.sotype  NOT IN ('B', 'R')
    """

    try:
        conn = get_connection(Config.DB_ORDERS)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

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

            territory = _map_territory((terr_code or '').strip())

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

        logger.info(
            f"Bookings snapshot: ${summary['total_amount']:,.0f} "
            f"across {summary['total_territories']} territories "
            f"({len(rows)} raw rows processed)"
        )
        return {"summary": summary, "ranking": ranking}

    except Exception as e:
        logger.error(f"Bookings query failed: {e}")
        return None


def fetch_bookings_raw():
    """
    Fetch today's raw bookings line items for Excel export.
    Returns all useful columns so users can validate and slice data freely.
    Filtering (customer exclusion, TAX, territory mapping) done in Python.

    Returns list of dicts, or None on failure.
    """

    query = """
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
    FROM PRO05.dbo.sotran tr WITH (NOLOCK)
    LEFT JOIN PRO05.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN PRO05.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN PRO05.dbo.icitem ic WITH (NOLOCK) ON ic.item = tr.item
    WHERE tr.ordate = CAST(GETDATE() AS DATE)
      AND tr.currhist <> 'X'
      AND tr.sostat  NOT IN ('V', 'X')
      AND tr.sotype  NOT IN ('B', 'R')
    """

    try:
        conn = get_connection(Config.DB_ORDERS)
        cursor = conn.cursor()
        cursor.execute(query)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

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
            record['Territory'] = _map_territory(terr_code)

            # Clean up string fields
            for key in ('CustomerNo', 'CustomerName', 'Item', 'Description',
                        'ProductLine', 'Salesman', 'Location', 'ShipVia'):
                if record.get(key):
                    record[key] = str(record[key]).strip()

            results.append(record)

        logger.info(f"Bookings raw export: {len(results)} rows")
        return results

    except Exception as e:
        logger.error(f"Bookings raw query failed: {e}")
        return None