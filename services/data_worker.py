"""
Background Data Worker
Runs on a schedule to refresh cached data from SQL Server.
Pages never query SQL directly — they read from cache for instant loads.

Also fetches the real-time CAD → USD exchange rate from a public API.
"""

import logging
import math
from datetime import datetime
from extensions import cache
from services.db_service import fetch_bookings_snapshot, fetch_bookings_snapshot_ca

logger = logging.getLogger(__name__)

# Cache keys (centralized so routes and worker stay in sync)
CACHE_KEY_BOOKINGS_US = "bookings_snapshot_us"
CACHE_KEY_BOOKINGS_CA = "bookings_snapshot_ca"
CACHE_KEY_BOOKINGS_UPDATED = "bookings_last_updated"
CACHE_KEY_CAD_RATE = "cad_to_usd_rate"

# Fallback rate in case the API is unreachable
DEFAULT_CAD_TO_USD = 0.72


def _fetch_cad_to_usd_rate():
    """
    Fetch real-time CAD → USD exchange rate from a free public API.
    Returns the rate as a float, or the default fallback on failure.
    """
    import urllib.request
    import json

    # Primary: frankfurter.app (free, no API key, ECB data)
    apis = [
        ("https://api.frankfurter.app/latest?from=CAD&to=USD", lambda d: d["rates"]["USD"]),
        ("https://open.er-api.com/v6/latest/CAD", lambda d: d["rates"]["USD"]),
    ]

    for url, extractor in apis:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "TWGPortal/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                rate = float(extractor(data))
                if 0.5 < rate < 1.0:  # Sanity check for CAD→USD
                    logger.info(f"Exchange rate fetched: 1 CAD = {rate:.4f} USD (from {url})")
                    return rate
                else:
                    logger.warning(f"Exchange rate out of range ({rate}) from {url}")
        except Exception as e:
            logger.warning(f"Exchange rate API failed ({url}): {e}")

    logger.warning(f"All exchange rate APIs failed. Using default: {DEFAULT_CAD_TO_USD}")
    return DEFAULT_CAD_TO_USD


def refresh_bookings_cache():
    """
    Fetch fresh bookings data from SQL for both US and Canada, store in cache.
    Also refreshes the CAD → USD exchange rate.
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

        # Fetch exchange rate
        rate = _fetch_cad_to_usd_rate()
        cache.set(CACHE_KEY_CAD_RATE, rate, timeout=900)

        # Update timestamp if at least one succeeded
        if result_us is not None or result_ca is not None:
            cache.set(CACHE_KEY_BOOKINGS_UPDATED, datetime.now(), timeout=900)

    except Exception as e:
        logger.error(f"Worker: Failed to refresh bookings cache: {e}")


def get_bookings_from_cache():
    """
    Read bookings data from cache for both US and Canada.
    If cache is empty (first load after app start), do a one-time synchronous fetch.

    Returns (us_snapshot, ca_snapshot, last_updated_datetime, cad_to_usd_rate)
    """
    data_us = cache.get(CACHE_KEY_BOOKINGS_US)
    data_ca = cache.get(CACHE_KEY_BOOKINGS_CA)
    updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)
    cad_rate = cache.get(CACHE_KEY_CAD_RATE)

    if data_us is None and data_ca is None:
        logger.info("Cache miss — running one-time synchronous fetch.")
        refresh_bookings_cache()
        data_us = cache.get(CACHE_KEY_BOOKINGS_US)
        data_ca = cache.get(CACHE_KEY_BOOKINGS_CA)
        updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)
        cad_rate = cache.get(CACHE_KEY_CAD_RATE)

    if cad_rate is None:
        cad_rate = DEFAULT_CAD_TO_USD

    return data_us, data_ca, updated, cad_rate