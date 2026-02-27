"""
Database Connection Helper
Handles pyodbc connection creation for SQL Server.
"""

import pyodbc
from config import Config


def get_connection(database=None):
    """Create and return a pyodbc connection with a connection timeout."""
    conn_str = Config.get_connection_string(database)
    return pyodbc.connect(conn_str, timeout=30)