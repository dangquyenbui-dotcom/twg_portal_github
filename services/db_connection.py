"""
Database Connection Helper
Handles pyodbc connection creation for SQL Server.
"""

import pyodbc
from config import Config


def get_connection(database=None):
    """Create and return a pyodbc connection with a connection timeout.
    Sets READ UNCOMMITTED isolation level (equivalent to NOLOCK on all tables)
    to ensure we never hold locks on the production SQL Server."""
    conn_str = Config.get_connection_string(database)
    conn = pyodbc.connect(conn_str, timeout=30)
    cursor = conn.cursor()
    cursor.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED")
    cursor.close()
    return conn