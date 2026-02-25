"""
Background Data Worker
Runs on a schedule to refresh cached data from SQL Server.
Pages never query SQL directly — they read from cache for instant loads.
"""

import logging
from datetime import datetime
from extensions import cache
from services.db_service import fetch_bookings_snapshot, fetch_bookings_snapshot_ca

logger = logging.getLogger(__name__)

# Cache keys (centralized so routes and worker stay in sync)
CACHE_KEY_BOOKINGS_US = "bookings_snapshot_us"
CACHE_KEY_BOOKINGS_CA = "bookings_snapshot_ca"
CACHE_KEY_BOOKINGS_UPDATED = "bookings_last_updated"


def refresh_bookings_cache():
    """
    Fetch fresh bookings data from SQL for both US and Canada, store in cache.
    Called by the scheduler every 10 minutes.
    """
    logger.info("Worker: Refreshing bookings cache (US + CA)...")

    try:
        # Fetch US bookings (PRO05)
        result_us = fetch_bookings_snapshot()
        if result_us is not None:
            cache.set(CACHE_KEY_BOOKINGS_US, result_us, timeout=900)
            logger.info("Worker: US bookings cache updated successfully.")
        else:
            logger.warning("Worker: US query returned None — keeping stale cache.")

        # Fetch Canada bookings (PRO06)
        result_ca = fetch_bookings_snapshot_ca()
        if result_ca is not None:
            cache.set(CACHE_KEY_BOOKINGS_CA, result_ca, timeout=900)
            logger.info("Worker: CA bookings cache updated successfully.")
        else:
            logger.warning("Worker: CA query returned None — keeping stale cache.")

        # Update timestamp if at least one succeeded
        if result_us is not None or result_ca is not None:
            cache.set(CACHE_KEY_BOOKINGS_UPDATED, datetime.now(), timeout=900)

    except Exception as e:
        logger.error(f"Worker: Failed to refresh bookings cache: {e}")


def get_bookings_from_cache():
    """
    Read bookings data from cache for both US and Canada.
    If cache is empty (first load after app start), do a one-time synchronous fetch.

    Returns (us_snapshot, ca_snapshot, last_updated_datetime)
    """
    data_us = cache.get(CACHE_KEY_BOOKINGS_US)
    data_ca = cache.get(CACHE_KEY_BOOKINGS_CA)
    updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)

    if data_us is None and data_ca is None:
        logger.info("Cache miss — running one-time synchronous fetch.")
        refresh_bookings_cache()
        data_us = cache.get(CACHE_KEY_BOOKINGS_US)
        data_ca = cache.get(CACHE_KEY_BOOKINGS_CA)
        updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)

    return data_us, data_ca, updated