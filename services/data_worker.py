"""
Background Data Worker
Runs on a schedule to refresh cached data from SQL Server.
Pages never query SQL directly — they read from cache for instant loads.
"""

import logging
from datetime import datetime
from extensions import cache
from services.db_service import fetch_bookings_snapshot

logger = logging.getLogger(__name__)

# Cache keys (centralized so routes and worker stay in sync)
CACHE_KEY_BOOKINGS = "bookings_snapshot"
CACHE_KEY_BOOKINGS_UPDATED = "bookings_last_updated"


def refresh_bookings_cache():
    """
    Fetch fresh bookings data from SQL and store in cache.
    Called by the scheduler every 10 minutes.
    """
    logger.info("Worker: Refreshing bookings cache...")

    try:
        result = fetch_bookings_snapshot()

        if result is not None:
            cache.set(CACHE_KEY_BOOKINGS, result, timeout=900)  # 15 min safety
            cache.set(CACHE_KEY_BOOKINGS_UPDATED, datetime.now(), timeout=900)
            logger.info("Worker: Bookings cache updated successfully.")
        else:
            logger.warning("Worker: Query returned None — keeping stale cache.")

    except Exception as e:
        logger.error(f"Worker: Failed to refresh bookings cache: {e}")


def get_bookings_from_cache():
    """
    Read bookings data from cache. If cache is empty (first load after
    app start), do a one-time synchronous fetch so the user isn't left
    with a blank page.

    Returns (snapshot_dict, last_updated_datetime)
    """
    data = cache.get(CACHE_KEY_BOOKINGS)
    updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)

    if data is None:
        logger.info("Cache miss — running one-time synchronous fetch.")
        refresh_bookings_cache()
        data = cache.get(CACHE_KEY_BOOKINGS)
        updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)

    return data, updated