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

    # SQL Server Settings
    DB_DRIVER = os.getenv('DB_DRIVER', '{ODBC Driver 18 for SQL Server}')
    DB_SERVER = os.getenv('DB_SERVER')
    DB_UID = os.getenv('DB_UID')
    DB_PWD = os.getenv('DB_PWD')
    DB_TRUST_CERT = os.getenv('DB_TRUST_CERT', 'yes')

    # Database Names
    DB_AUTH = os.getenv('DB_AUTH', 'PRO12')
    DB_ORDERS = os.getenv('DB_ORDERS', 'PRO05')

    # Cache (filesystem so it survives brief restarts)
    CACHE_TYPE = 'FileSystemCache'
    CACHE_DIR = 'cache-data'
    CACHE_DEFAULT_TIMEOUT = 900  # 15 min safety net

    # Scheduler
    SCHEDULER_API_ENABLED = False  # No need to expose the REST API

    # Refresh interval in seconds (10 minutes)
    DATA_REFRESH_INTERVAL = 600

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

        logger.info(f"Config validated. CLIENT_ID={cls.CLIENT_ID[:8]}...")
        return True