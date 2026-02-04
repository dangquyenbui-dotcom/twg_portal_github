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
    
    CLIENT_ID = os.getenv('CLIENT_ID')
    CLIENT_SECRET = os.getenv('CLIENT_SECRET')
    AUTHORITY = os.getenv('AUTHORITY')
    REDIRECT_PATH = os.getenv('REDIRECT_PATH', '/auth/redirect')
    SCOPE = [os.getenv('SCOPE', 'User.Read')]
    
    # Database default updated to remove 'Production' reference
    DB_SERVER = os.getenv('DB_SERVER')
    DB_NAME = os.getenv('DB_NAME', 'TwgPortalDB')
    
    @classmethod
    def validate(cls):
        required = ["CLIENT_ID", "CLIENT_SECRET", "AUTHORITY"]
        missing = [f for f in required if not getattr(cls, f)]
        if missing:
            logger.error(f"Missing config: {', '.join(missing)}")
            return False
        return True