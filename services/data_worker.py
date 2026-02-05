"""
Background Worker
Responsible for fetching heavy data (SQL) and updating the Cache.
"""
import time
import random
import logging
from extensions import cache

logger = logging.getLogger(__name__)

def refresh_sales_cache():
    """
    Simulates a heavy SQL query and updates the cache.
    Replace the dummy logic below with pyodbc code later.
    """
    logger.info("⏳ Worker: Starting heavy 'SQL' query for Sales Data...")
    
    # Simulate SQL Latency (wait 2 seconds)
    time.sleep(2)
    
    # --- MOCK DATA GENERATOR (Represents your SQL Result) ---
    mock_data = [
        {"terr": "312", "location": "Atlanta", "total": random.uniform(30000, 40000), "rank": 1},
        {"terr": "302", "location": "Nashville", "total": random.uniform(20000, 29000), "rank": 2},
        {"terr": "307", "location": "Charlotte", "total": random.uniform(15000, 24000), "rank": 3},
        {"terr": "001", "location": "LA2", "total": random.uniform(15000, 21000), "rank": 4},
        {"terr": "900", "location": "Central Billing", "total": 20469.32, "rank": 5},
    ]
    # ---------------------------------------------------------

    # Store in Cache for 1 hour (or until next refresh overwrites it)
    cache.set("sales_dashboard_data", mock_data, timeout=3600)
    
    logger.info("✅ Worker: Cache successfully updated with fresh data.")