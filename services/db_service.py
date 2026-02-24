"""
Database Service
Handles all SQL Server connections and queries.
"""

import logging
import pyodbc
from config import Config

logger = logging.getLogger(__name__)


def get_connection(database=None):
    """Create and return a pyodbc connection."""
    conn_str = Config.get_connection_string(database)
    return pyodbc.connect(conn_str, timeout=30)


def fetch_daily_bookings():
    """
    Fetch today's bookings from PRO05 (SOTRAN + SOMAST).
    Returns a list of dicts.
    """
    query = """
    SELECT
        tr.sono AS [Sales Order Number],
        tr.ordate AS [Order Date],
        tr.item AS [Item],
        CASE ic.plinid COLLATE Latin1_General_CI_AS
            WHEN 'BACCAR' THEN 'OTHER WHEEL'
            WHEN 'DETROI' THEN 'OTHER WHEEL'
            WHEN 'DIP' THEN 'OTHER WHEEL'
            WHEN 'ION' THEN 'ION'
            WHEN 'LUGNUT' THEN 'LUGNUT'
            WHEN 'MAZZI' THEN 'MAZZI'
            WHEN 'SACCHI' THEN 'OTHER WHEEL'
            WHEN 'VELOCH' THEN 'OTHER WHEEL'
            WHEN 'TIRE' THEN 'TIRE'
            WHEN 'AKITA' THEN 'OTHER WHEEL'
            WHEN 'STEEL' THEN 'OTHER WHEEL'
            WHEN 'CRAGAR' THEN 'OTHER WHEEL'
            WHEN 'SPINNE' THEN 'OTHER ACCE'
            WHEN 'SF' THEN 'OTHER WHEEL'
            WHEN 'AIRSPD' THEN 'OTHER ACCE'
            WHEN 'MPW' THEN 'OTHER WHEEL'
            WHEN 'IONF' THEN 'OTHER WHEEL'
            WHEN 'TPMS' THEN 'OTHER TPMS'
            WHEN 'IONB' THEN 'IONB'
            WHEN 'TOUREN' THEN 'TOUREN'
            WHEN 'RIDLER' THEN 'RIDLER'
            WHEN 'MAYHEM' THEN 'MAYHEM'
            WHEN 'TIRC' THEN 'OTHER TIRE'
            WHEN 'IONT' THEN 'IONT'
            WHEN 'XMC' THEN 'XMC'
            WHEN 'FRT' THEN 'FRT'
            WHEN 'DEFECT' THEN 'DEFECT'
            WHEN 'OE' THEN 'OE'
            WHEN 'WCAP' THEN 'WCAP'
            WHEN 'MASINI' THEN 'OTHER WHEEL'
            WHEN 'ITM' THEN 'OTHER TPMS'
            WHEN 'METAL' THEN 'METAL'
            WHEN 'AMP' THEN 'AMP'
            WHEN 'CALI' THEN 'CALI'
            WHEN 'LAND' THEN 'OTHER TIRE'
            WHEN 'DL' THEN 'DL'
            WHEN 'MAX' THEN 'MAX'
            WHEN 'BODAMR' THEN 'BODAMR'
            WHEN 'BODLFT' THEN 'BODLFT'
            WHEN 'RHI' THEN 'RHI'
            WHEN 'POWER' THEN 'OTHER ACCE'
            WHEN 'AT' THEN 'AT'
            WHEN 'KRAZE' THEN 'KRAZE'
            WHEN 'TS' THEN 'TS'
            WHEN 'DURUN' THEN 'OTHER TIRE'
            ELSE ic.plinid COLLATE Latin1_General_CI_AS
        END AS [Brand],
        cu.company AS [Customer Name],
        tr.custno AS [Customer Number],
        CASE ic.plinid COLLATE Latin1_General_CI_AS
            WHEN 'BACCAR' THEN 'WHEEL'
            WHEN 'DETROI' THEN 'WHEEL'
            WHEN 'DIP' THEN 'WHEEL'
            WHEN 'ION' THEN 'WHEEL'
            WHEN 'LUGNUT' THEN 'ACCE'
            WHEN 'MAZZI' THEN 'WHEEL'
            WHEN 'SACCHI' THEN 'WHEEL'
            WHEN 'VELOCH' THEN 'WHEEL'
            WHEN 'TIRE' THEN 'TIRE'
            WHEN 'AKITA' THEN 'WHEEL'
            WHEN 'STEEL' THEN 'WHEEL'
            WHEN 'CRAGAR' THEN 'WHEEL'
            WHEN 'SPINNE' THEN 'ACCE'
            WHEN 'SF' THEN 'WHEEL'
            WHEN 'AIRSPD' THEN 'ACCE'
            WHEN 'MPW' THEN 'WHEEL'
            WHEN 'IONF' THEN 'WHEEL'
            WHEN 'TPMS' THEN 'TPMS'
            WHEN 'IONB' THEN 'WHEEL'
            WHEN 'TOUREN' THEN 'WHEEL'
            WHEN 'RIDLER' THEN 'WHEEL'
            WHEN 'MAYHEM' THEN 'WHEEL'
            WHEN 'TIRC' THEN 'TIRE'
            WHEN 'IONT' THEN 'WHEEL'
            WHEN 'XMC' THEN 'MISCELLANEOUS'
            WHEN 'FRT' THEN 'MISCELLANEOUS'
            WHEN 'DEFECT' THEN 'MISCELLANEOUS'
            WHEN 'OE' THEN 'WHEEL'
            WHEN 'WCAP' THEN 'WHEEL'
            WHEN 'MASINI' THEN 'WHEEL'
            WHEN 'ITM' THEN 'TPMS'
            WHEN 'METAL' THEN 'ACCE'
            WHEN 'AMP' THEN 'TIRE'
            WHEN 'CALI' THEN 'WHEEL'
            WHEN 'LAND' THEN 'TIRE'
            WHEN 'DL' THEN 'WHEEL'
            WHEN 'MAX' THEN 'TPMS'
            WHEN 'BODAMR' THEN 'BA4X4'
            WHEN 'BODLFT' THEN 'BA4X4'
            WHEN 'RHI' THEN 'ACCE'
            WHEN 'POWER' THEN 'ACCE'
            WHEN 'AT' THEN 'WHEEL'
            WHEN 'KRAZE' THEN 'WHEEL'
            WHEN 'TS' THEN 'TS'
            WHEN 'DURUN' THEN 'TIRE'
            ELSE 'Other'
        END AS [Category],
        CASE LTRIM(RTRIM(CASE WHEN cu.terr COLLATE Latin1_General_CI_AS = '900' THEN cu.terr ELSE sm.terr END)) COLLATE Latin1_General_CI_AS
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
        END AS [Territory],
        adr.salesmn AS [Salesperson - ShipTo],
        cu.salesmn AS [Salesperson - BillTo],
        tr.origqtyord AS [Units Ordered],
        tr.origqtyord * tr.price AS [Booking Amount],
        tr.price AS [Order Price],
        tr.sysprice AS [System Price],
        tr.jobberpric AS [Jobber Price]
    FROM PRO05.dbo.sotran tr WITH (NOLOCK)
    LEFT JOIN PRO05.dbo.somast sm WITH (NOLOCK) ON sm.sono = tr.sono
    LEFT JOIN PRO05.dbo.arcust cu WITH (NOLOCK) ON cu.custno = tr.custno
    LEFT JOIN PRO05.dbo.icitem ic WITH (NOLOCK) ON LTRIM(RTRIM(ic.item)) COLLATE Latin1_General_CI_AS = LTRIM(RTRIM(tr.item)) COLLATE Latin1_General_CI_AS
    LEFT JOIN PRO05.dbo.arcadr adr WITH (NOLOCK) ON adr.custno = sm.custno AND adr.cshipno = sm.cshipno
    WHERE tr.ordate = CAST(GETDATE() AS DATE)
        AND tr.currhist <> 'X'
        AND LTRIM(RTRIM(tr.custno)) COLLATE Latin1_General_CI_AS NOT IN ('TWGMARKET','PROMO_TS','W1MGMT','W1MON','W1TOR','W1VAN','CASHMN','WHETERRY','TWG','WHEEL1')
        AND COALESCE(ic.plinid COLLATE Latin1_General_CI_AS, '') <> 'TAX'
    ORDER BY [Territory], [Sales Order Number]
    """

    try:
        conn = get_connection(Config.DB_ORDERS)
        cursor = conn.cursor()
        cursor.execute(query)

        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        cursor.close()
        conn.close()

        logger.info(f"Bookings query returned {len(rows)} rows")
        return rows

    except Exception as e:
        logger.error(f"Bookings query failed: {e}")
        return None