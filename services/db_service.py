"""
Database Service
Handles all SQL Server connections and queries.
All queries are optimized to minimize SQL Server resource usage.
"""

import logging
import pyodbc
from config import Config

logger = logging.getLogger(__name__)


def get_connection(database=None):
    """Create and return a pyodbc connection with a connection timeout."""
    conn_str = Config.get_connection_string(database)
    return pyodbc.connect(conn_str, timeout=30)


def fetch_bookings_snapshot():
    """
    Fetch today's bookings pre-aggregated into:
      1. summary   – one row with totals (amount, units, distinct orders, territories)
      2. ranking   – one row per territory with total + rank

    This runs as a SINGLE round-trip with two result sets so we only
    hit SQL Server once and let the database do all the heavy lifting.
    """

    query = """
    -- ============================================================
    -- CTE: filter once, reuse everywhere
    -- ============================================================
    ;WITH bookings AS (
        SELECT
            tr.sono,
            tr.origqtyord                       AS units,
            tr.origqtyord * tr.price            AS amount,
            CASE LTRIM(RTRIM(
                    CASE WHEN cu.terr COLLATE Latin1_General_CI_AS = '900'
                         THEN cu.terr
                         ELSE sm.terr
                    END)) COLLATE Latin1_General_CI_AS
                WHEN '000' THEN 'LA'
                WHEN '001' THEN 'LA'
                WHEN '010' THEN 'China'
                WHEN '114' THEN 'Seattle'
                WHEN '126' THEN 'Denver'
                WHEN '204' THEN 'Columbus'
                WHEN '206' THEN 'Jacksonville'
                WHEN '210' THEN 'Houston'
                WHEN '211' THEN 'Dallas'
                WHEN '218' THEN 'San Antonio'
                WHEN '221' THEN 'Kansas City'
                WHEN '302' THEN 'Nashville'
                WHEN '305' THEN 'Levittown,PA'
                WHEN '307' THEN 'Charlotte'
                WHEN '312' THEN 'Atlanta'
                WHEN '324' THEN 'Indianapolis'
                WHEN '900' THEN 'Central Billing'
                ELSE 'Others'
            END AS territory
        FROM PRO05.dbo.sotran tr WITH (NOLOCK)
        LEFT JOIN PRO05.dbo.somast  sm  WITH (NOLOCK) ON sm.sono = tr.sono
        LEFT JOIN PRO05.dbo.arcust  cu  WITH (NOLOCK) ON cu.custno = tr.custno
        LEFT JOIN PRO05.dbo.icitem  ic  WITH (NOLOCK) ON LTRIM(RTRIM(ic.item)) COLLATE Latin1_General_CI_AS
                                                        = LTRIM(RTRIM(tr.item)) COLLATE Latin1_General_CI_AS
        WHERE tr.ordate = CAST(GETDATE() AS DATE)
          AND tr.currhist <> 'X'
          AND LTRIM(RTRIM(tr.custno)) COLLATE Latin1_General_CI_AS
              NOT IN ('TWGMARKET','PROMO_TS','W1MGMT','W1MON','W1TOR','W1VAN',
                      'CASHMN','WHETERRY','TWG','WHEEL1')
          AND COALESCE(ic.plinid COLLATE Latin1_General_CI_AS, '') <> 'TAX'
    )

    -- Result Set 1: Summary row
    SELECT
        CAST(GETDATE() AS DATE)         AS order_date,
        ISNULL(SUM(amount), 0)          AS total_amount,
        ISNULL(SUM(units), 0)           AS total_units,
        COUNT(DISTINCT sono)            AS total_orders,
        COUNT(DISTINCT territory)       AS total_territories
    FROM bookings;

    -- Result Set 2: Territory ranking
    ;WITH bookings AS (
        SELECT
            tr.sono,
            tr.origqtyord                       AS units,
            tr.origqtyord * tr.price            AS amount,
            CASE LTRIM(RTRIM(
                    CASE WHEN cu.terr COLLATE Latin1_General_CI_AS = '900'
                         THEN cu.terr
                         ELSE sm.terr
                    END)) COLLATE Latin1_General_CI_AS
                WHEN '000' THEN 'LA'
                WHEN '001' THEN 'LA'
                WHEN '010' THEN 'China'
                WHEN '114' THEN 'Seattle'
                WHEN '126' THEN 'Denver'
                WHEN '204' THEN 'Columbus'
                WHEN '206' THEN 'Jacksonville'
                WHEN '210' THEN 'Houston'
                WHEN '211' THEN 'Dallas'
                WHEN '218' THEN 'San Antonio'
                WHEN '221' THEN 'Kansas City'
                WHEN '302' THEN 'Nashville'
                WHEN '305' THEN 'Levittown,PA'
                WHEN '307' THEN 'Charlotte'
                WHEN '312' THEN 'Atlanta'
                WHEN '324' THEN 'Indianapolis'
                WHEN '900' THEN 'Central Billing'
                ELSE 'Others'
            END AS territory
        FROM PRO05.dbo.sotran tr WITH (NOLOCK)
        LEFT JOIN PRO05.dbo.somast  sm  WITH (NOLOCK) ON sm.sono = tr.sono
        LEFT JOIN PRO05.dbo.arcust  cu  WITH (NOLOCK) ON cu.custno = tr.custno
        LEFT JOIN PRO05.dbo.icitem  ic  WITH (NOLOCK) ON LTRIM(RTRIM(ic.item)) COLLATE Latin1_General_CI_AS
                                                        = LTRIM(RTRIM(tr.item)) COLLATE Latin1_General_CI_AS
        WHERE tr.ordate = CAST(GETDATE() AS DATE)
          AND tr.currhist <> 'X'
          AND LTRIM(RTRIM(tr.custno)) COLLATE Latin1_General_CI_AS
              NOT IN ('TWGMARKET','PROMO_TS','W1MGMT','W1MON','W1TOR','W1VAN',
                      'CASHMN','WHETERRY','TWG','WHEEL1')
          AND COALESCE(ic.plinid COLLATE Latin1_General_CI_AS, '') <> 'TAX'
    )
    SELECT
        territory                               AS location,
        SUM(amount)                             AS total,
        ROW_NUMBER() OVER (ORDER BY SUM(amount) DESC) AS rank
    FROM bookings
    GROUP BY territory
    ORDER BY rank;
    """

    try:
        conn = get_connection(Config.DB_ORDERS)
        cursor = conn.cursor()
        cursor.execute(query)

        # --- Result Set 1: Summary ---
        summary_row = cursor.fetchone()
        summary = {
            "order_date": summary_row[0],
            "total_amount": float(summary_row[1] or 0),
            "total_units": int(summary_row[2] or 0),
            "total_orders": int(summary_row[3] or 0),
            "total_territories": int(summary_row[4] or 0),
        }

        # --- Result Set 2: Territory Ranking ---
        cursor.nextset()
        columns = [col[0] for col in cursor.description]
        ranking = [dict(zip(columns, row)) for row in cursor.fetchall()]

        cursor.close()
        conn.close()

        logger.info(f"Bookings snapshot: ${summary['total_amount']:,.0f} across {summary['total_territories']} territories")
        return {"summary": summary, "ranking": ranking}

    except Exception as e:
        logger.error(f"Bookings query failed: {e}")
        return None