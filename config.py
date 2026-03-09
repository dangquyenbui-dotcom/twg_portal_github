"""
Configuration settings for TWG Portal
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

# --- Explicitly load .env from the project root directory ---
env_path = Path(__file__).resolve().parent / '.env'
loaded = load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)

if not loaded:
    logger.warning(f"Could not load .env from: {env_path}")
    env_path_alt = Path(__file__).resolve().parent / '_env'
    loaded = load_dotenv(dotenv_path=env_path_alt)
    if loaded:
        logger.info(f"Loaded environment from fallback: {env_path_alt}")


class Config:
    """Application configuration"""
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')

    # Auth Settings
    CLIENT_ID = os.getenv('CLIENT_ID')
    CLIENT_SECRET = os.getenv('CLIENT_SECRET')
    TENANT_ID = os.getenv('TENANT_ID')
    AUTHORITY = os.getenv('AUTHORITY', f'https://login.microsoftonline.com/{os.getenv("TENANT_ID", "")}')
    REDIRECT_PATH = os.getenv('REDIRECT_PATH', '/auth/redirect')
    SCOPE = [os.getenv('SCOPE', 'User.Read')]

    # Optional: Hardcode the full redirect URI for environments where
    # request.url_root doesn't match Azure's registered URI (e.g. behind
    # a reverse proxy, custom domain, or dev server).
    REDIRECT_URI_OVERRIDE = os.getenv('REDIRECT_URI_OVERRIDE', '').strip() or None

    # ── Security Group → Role Mapping ──
    #
    # Maps Entra ID Security Group Object IDs to internal role names.
    # One user can belong to multiple groups = multiple roles.
    #
    # Naming convention:
    #   GROUP_ADMIN                         → Admin (full bypass)
    #   GROUP_SALES_<REPORT>_VIEW           → Sales.<Report>.View (dashboard access)
    #   GROUP_SALES_<REPORT>_EXPORT         → Sales.<Report>.Export (Excel download)
    #   GROUP_WAREHOUSE_<REPORT>_VIEW       → Warehouse.<Report>.View (future)
    #   GROUP_WAREHOUSE_<REPORT>_EXPORT     → Warehouse.<Report>.Export (future)
    #
    # Export roles do NOT grant view access — they only enable download buttons
    # on reports the user can already see via the corresponding View role.
    # Admin bypasses all checks (view + export).
    #
    GROUP_ROLE_MAP = {}

    @classmethod
    def _build_group_role_map(cls):
        """
        Build the group-to-role mapping from environment variables.
        Each GROUP_* env var maps a Security Group Object ID to an internal role name.

        Pattern:
          GROUP_ADMIN                        → Admin
          GROUP_SALES_BOOKINGS_VIEW          → Sales.Bookings.View
          GROUP_SALES_BOOKINGS_EXPORT        → Sales.Bookings.Export
          GROUP_SALES_OPENORDERS_VIEW        → Sales.OpenOrders.View
          GROUP_SALES_OPENORDERS_EXPORT      → Sales.OpenOrders.Export
          (add more reports by following the same pattern)
        """
        mapping = {}
        group_vars = {
            # ── Admin (full access to everything) ──
            'GROUP_ADMIN':                      'Admin',

            # ── Sales: Daily Bookings ──
            'GROUP_SALES_BOOKINGS_VIEW':        'Sales.Bookings.View',
            'GROUP_SALES_BOOKINGS_EXPORT':      'Sales.Bookings.Export',

            # ── Sales: Open Orders ──
            'GROUP_SALES_OPENORDERS_VIEW':      'Sales.OpenOrders.View',
            'GROUP_SALES_OPENORDERS_EXPORT':    'Sales.OpenOrders.Export',

            # ── Sales: Dashboard (Executive) ──
            'GROUP_SALES_DASHBOARD_VIEW':       'Sales.Dashboard.View',

            # ── Sales: Shipments (future — uncomment when ready) ──
            # 'GROUP_SALES_SHIPMENTS_VIEW':     'Sales.Shipments.View',
            # 'GROUP_SALES_SHIPMENTS_EXPORT':   'Sales.Shipments.Export',

            # ── Sales: Territory Performance (future) ──
            # 'GROUP_SALES_TERRPERF_VIEW':      'Sales.TerrPerf.View',
            # 'GROUP_SALES_TERRPERF_EXPORT':    'Sales.TerrPerf.Export',

            # ── Warehouse (future) ──
            # 'GROUP_WAREHOUSE':                'Warehouse',

            # ── Finance (future) ──
            # 'GROUP_FINANCE':                  'Finance',

            # ── HR (future) ──
            # 'GROUP_HR':                       'HR',
        }
        for env_key, role_name in group_vars.items():
            group_id = os.getenv(env_key, '').strip()
            if group_id:
                mapping[group_id] = role_name
                logger.info(f"Group mapping: {env_key} ({group_id[:8]}...) → {role_name}")
            else:
                logger.debug(f"Group mapping: {env_key} not set — skipping")
        cls.GROUP_ROLE_MAP = mapping

    # SQL Server Settings
    DB_DRIVER = os.getenv('DB_DRIVER', '{ODBC Driver 18 for SQL Server}')
    DB_SERVER = os.getenv('DB_SERVER')
    DB_UID = os.getenv('DB_UID')
    DB_PWD = os.getenv('DB_PWD')
    DB_TRUST_CERT = os.getenv('DB_TRUST_CERT', 'yes')

    # Database Names
    DB_AUTH = os.getenv('DB_AUTH', 'PRO12')
    DB_ORDERS = os.getenv('DB_ORDERS', 'PRO05')        # US orders
    DB_ORDERS_CA = os.getenv('DB_ORDERS_CA', 'PRO06')  # Canada orders

    # Cache (filesystem so it survives brief restarts)
    CACHE_TYPE = 'FileSystemCache'
    CACHE_DIR = 'cache-data'
    CACHE_DEFAULT_TIMEOUT = 900  # 15 min safety net

    # Scheduler
    SCHEDULER_API_ENABLED = False  # No need to expose the REST API

    # Refresh interval in seconds (10 minutes)
    DATA_REFRESH_INTERVAL = 600

    # Open Orders refresh interval in seconds (60 minutes — less frequent to reduce SQL load)
    OPEN_ORDERS_REFRESH_INTERVAL = 3600

    # Dashboard current month refresh interval in seconds (60 minutes)
    # Historical data is cached on demand and never auto-refreshed.
    DASHBOARD_REFRESH_INTERVAL = 3600

    @classmethod
    def get_connection_string(cls, database=None):
        """Build a pyodbc connection string for the given database."""
        db = database or cls.DB_ORDERS
        return (
            f"DRIVER={cls.DB_DRIVER};"
            f"SERVER={cls.DB_SERVER};"
            f"DATABASE={db};"
            f"UID={cls.DB_UID};"
            f"PWD={cls.DB_PWD};"
            f"TrustServerCertificate={cls.DB_TRUST_CERT};"
        )

    @classmethod
    def validate(cls):
        """Validate that all required config values are present."""
        required = {
            "CLIENT_ID": cls.CLIENT_ID,
            "CLIENT_SECRET": cls.CLIENT_SECRET,
            "AUTHORITY": cls.AUTHORITY,
        }
        missing = [name for name, value in required.items() if not value]

        if missing:
            logger.error("=" * 60)
            logger.error("FATAL: Missing required configuration values!")
            logger.error(f"   Missing: {', '.join(missing)}")
            logger.error(f"   Expected .env location: {Path(__file__).resolve().parent / '.env'}")
            logger.error("=" * 60)
            raise SystemExit("Cannot start: missing authentication configuration.")

        # Build group → role mapping from env vars
        cls._build_group_role_map()

        if not cls.GROUP_ROLE_MAP:
            logger.warning("WARNING: No GROUP_* environment variables set. No users will have any roles.")
        else:
            logger.info(f"Config validated. {len(cls.GROUP_ROLE_MAP)} security group(s) mapped.")

        logger.info(f"Config validated. CLIENT_ID={cls.CLIENT_ID[:8]}...")
        if cls.REDIRECT_URI_OVERRIDE:
            logger.info(f"Config: REDIRECT_URI_OVERRIDE is set: {cls.REDIRECT_URI_OVERRIDE}")
        else:
            logger.info("Config: No REDIRECT_URI_OVERRIDE — will build redirect_uri dynamically from request.")
        return True