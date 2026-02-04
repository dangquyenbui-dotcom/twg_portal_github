"""
Configuration settings for Production Portal
Reads sensitive information from environment variables
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    """Application configuration"""
    
    # Flask settings
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    SESSION_TYPE = os.getenv('SESSION_TYPE', 'filesystem')
    
    # Microsoft Entra ID settings
    CLIENT_ID = os.getenv('CLIENT_ID')
    CLIENT_SECRET = os.getenv('CLIENT_SECRET')
    AUTHORITY = os.getenv('AUTHORITY')
    REDIRECT_PATH = os.getenv('REDIRECT_PATH', '/auth/redirect')
    SCOPE = [os.getenv('SCOPE', 'User.Read')]
    
    # Database (Keep existing settings for future use)
    DB_SERVER = os.getenv('DB_SERVER')
    DB_NAME = os.getenv('DB_NAME', 'ProductionDB')
    
    @classmethod
    def validate(cls):
        """Validate required configuration"""
        if not cls.CLIENT_ID or not cls.CLIENT_SECRET:
            print("❌ Authentication Config Missing: Check .env for CLIENT_ID and CLIENT_SECRET")
            return False
        return True