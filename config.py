"""
Configuration settings for TWG Portal
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class Config:
    """Application configuration"""
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    SESSION_TYPE = os.getenv('SESSION_TYPE', 'filesystem')
    
    # Auth Settings
    CLIENT_ID = os.getenv('CLIENT_ID')
    CLIENT_SECRET = os.getenv('CLIENT_SECRET')
    AUTHORITY = os.getenv('AUTHORITY')
    REDIRECT_PATH = os.getenv('REDIRECT_PATH', '/auth/redirect')
    SCOPE = [os.getenv('SCOPE', 'User.Read')]
    
    # Database
    DB_SERVER = os.getenv('DB_SERVER')
    DB_NAME = os.getenv('DB_NAME', 'TwgPortalDB')

    # --- NEW: CACHE SETTINGS ---
    # using 'filesystem' ensures cache survives if app restarts briefly
    CACHE_TYPE = 'FileSystemCache'  
    CACHE_DIR = 'cache-data'
    CACHE_DEFAULT_TIMEOUT = 300  # 5 minutes default

    # --- NEW: SCHEDULER SETTINGS ---
    SCHEDULER_API_ENABLED = True
    
    @classmethod
    def validate(cls):
        required = ["CLIENT_ID", "CLIENT_SECRET", "AUTHORITY"]
        missing = [f for f in required if not getattr(cls, f)]
        if missing:
            logger.error(f"Missing config: {', '.join(missing)}")
            return False
        return True