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

    # Database
    DB_SERVER = os.getenv('DB_SERVER')
    DB_NAME = os.getenv('DB_NAME', 'TwgPortalDB')

    @classmethod
    def validate(cls):
        """Validate that all required config values are present and not None."""
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