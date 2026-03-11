"""
Background Data Worker
Runs on a schedule to refresh cached data from SQL Server.
Pages and exports never query SQL directly — they read from cache for instant loads.

Refreshes:
  - US bookings snapshot + raw (PRO05)     — every 10 min
  - CA bookings snapshot + raw (PRO06)     — every 10 min
  - US open orders snapshot + raw (PRO05)  — every 60 min
  - CA open orders snapshot + raw (PRO06)  — every 60 min
  - CAD → USD exchange rate                — every 10 min
  - Bookings Summary MTD/QTD/YTD           — every 30 min (+ on startup)

On startup:
  1. Exchange rate
  2. Daily bookings (snapshot + raw, today only)
  3. Open orders (snapshot + raw, all open lines)
  4. Bookings Summary: auto-freeze completed months → read from disk → query only
     current month from sotran → assemble MTD/QTD/YTD → populate dashboard cache
  Total startup: ~5-8 seconds (frozen files load in <1ms each, only current month hits SQL)
"""

import logging
import math
from datetime import datetime
from extensions import cache
from services.bookings_service import (
    fetch_bookings_snapshot_us, fetch_bookings_snapshot_ca,
    fetch_bookings_raw_us, fetch_bookings_raw_ca,
)
from services.open_orders_service import (
    fetch_open_orders_snapshot_us, fetch_open_orders_snapshot_ca,
    fetch_open_orders_raw_us, fetch_open_orders_raw_ca,
)

logger = logging.getLogger(__name__)

# ── Cache keys (centralized so routes and worker stay in sync) ──

# Bookings — dashboard snapshots
CACHE_KEY_BOOKINGS_US = "bookings_snapshot_us"
CACHE_KEY_BOOKINGS_CA = "bookings_snapshot_ca"
CACHE_KEY_BOOKINGS_UPDATED = "bookings_last_updated"

# Bookings — raw export data
CACHE_KEY_BOOKINGS_RAW_US = "bookings_raw_us"
CACHE_KEY_BOOKINGS_RAW_CA = "bookings_raw_ca"

# Open Orders — dashboard snapshots
CACHE_KEY_OPEN_ORDERS_US = "open_orders_snapshot_us"
CACHE_KEY_OPEN_ORDERS_CA = "open_orders_snapshot_ca"
CACHE_KEY_OPEN_ORDERS_UPDATED = "open_orders_last_updated"

# Open Orders — raw export data
CACHE_KEY_OPEN_ORDERS_RAW_US = "open_orders_raw_us"
CACHE_KEY_OPEN_ORDERS_RAW_CA = "open_orders_raw_ca"

# Exchange Rate
CACHE_KEY_CAD_RATE = "cad_to_usd_rate"

# Fallback rate in case all APIs are unreachable
DEFAULT_CAD_TO_USD = 0.72

# Cache timeouts
BOOKINGS_CACHE_TIMEOUT = 900     # 15 min (refreshes every 10 min)
OO_CACHE_TIMEOUT = 3900          # 65 min (refreshes every 60 min)


# ─────────────────────────────────────────────────────────────
# Exchange Rate
# ─────────────────────────────────────────────────────────────

def _fetch_cad_to_usd_rate():
    """
    Fetch real-time CAD → USD exchange rate from a free public API.
    Returns the rate as a float, or the default fallback on failure.
    """
    import urllib.request
    import json

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
                if 0.5 < rate < 1.0:
                    logger.info(f"Exchange rate fetched: 1 CAD = {rate:.4f} USD (from {url})")
                    return rate
                else:
                    logger.warning(f"Exchange rate out of range ({rate}) from {url}")
        except Exception as e:
            logger.warning(f"Exchange rate API failed ({url}): {e}")

    logger.warning(f"All exchange rate APIs failed. Using default: {DEFAULT_CAD_TO_USD}")
    return DEFAULT_CAD_TO_USD


# ─────────────────────────────────────────────────────────────
# Bookings Cache (snapshot + raw)
# ─────────────────────────────────────────────────────────────

def refresh_bookings_cache():
    """
    Fetch fresh bookings data from SQL for both US and Canada.
    Caches both the dashboard snapshot AND raw export data in one pass,
    so Excel exports never hit SQL Server directly.
    """
    logger.info("Worker: Refreshing bookings cache (US + CA)...")

    try:
        # ── US snapshot ──
        result_us = fetch_bookings_snapshot_us()
        if result_us is not None:
            cache.set(CACHE_KEY_BOOKINGS_US, result_us, timeout=BOOKINGS_CACHE_TIMEOUT)
            logger.info("Worker: US bookings snapshot cache updated.")
        else:
            logger.warning("Worker: US bookings snapshot returned None — keeping stale cache.")

        # ── CA snapshot ──
        result_ca = fetch_bookings_snapshot_ca()
        if result_ca is not None:
            cache.set(CACHE_KEY_BOOKINGS_CA, result_ca, timeout=BOOKINGS_CACHE_TIMEOUT)
            logger.info("Worker: CA bookings snapshot cache updated.")
        else:
            logger.warning("Worker: CA bookings snapshot returned None — keeping stale cache.")

        # ── US raw export data ──
        raw_us = fetch_bookings_raw_us()
        if raw_us is not None:
            cache.set(CACHE_KEY_BOOKINGS_RAW_US, raw_us, timeout=BOOKINGS_CACHE_TIMEOUT)
            logger.info(f"Worker: US bookings raw cache updated ({len(raw_us)} rows).")
        else:
            logger.warning("Worker: US bookings raw query returned None — keeping stale cache.")

        # ── CA raw export data ──
        raw_ca = fetch_bookings_raw_ca()
        if raw_ca is not None:
            cache.set(CACHE_KEY_BOOKINGS_RAW_CA, raw_ca, timeout=BOOKINGS_CACHE_TIMEOUT)
            logger.info(f"Worker: CA bookings raw cache updated ({len(raw_ca)} rows).")
        else:
            logger.warning("Worker: CA bookings raw query returned None — keeping stale cache.")

        # ── Timestamp ──
        if any(x is not None for x in [result_us, result_ca, raw_us, raw_ca]):
            cache.set(CACHE_KEY_BOOKINGS_UPDATED, datetime.now(), timeout=BOOKINGS_CACHE_TIMEOUT)

    except Exception as e:
        logger.error(f"Worker: Failed to refresh bookings cache: {e}")


def get_bookings_from_cache():
    """
    Read bookings dashboard data from cache. If empty, do a one-time synchronous fetch.
    Returns (us_snapshot, ca_snapshot, last_updated, cad_to_usd_rate)
    """
    data_us = cache.get(CACHE_KEY_BOOKINGS_US)
    data_ca = cache.get(CACHE_KEY_BOOKINGS_CA)
    updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)
    cad_rate = cache.get(CACHE_KEY_CAD_RATE)

    if data_us is None and data_ca is None:
        logger.info("Bookings cache miss — running synchronous fetch.")
        refresh_bookings_cache()
        data_us = cache.get(CACHE_KEY_BOOKINGS_US)
        data_ca = cache.get(CACHE_KEY_BOOKINGS_CA)
        updated = cache.get(CACHE_KEY_BOOKINGS_UPDATED)

    if cad_rate is None:
        cad_rate = DEFAULT_CAD_TO_USD

    return data_us, data_ca, updated, cad_rate


def get_bookings_raw_from_cache():
    """
    Read bookings raw export data from cache. If empty, do a one-time synchronous fetch.
    Returns (us_rows, ca_rows) — each is a list of dicts or empty list.
    """
    raw_us = cache.get(CACHE_KEY_BOOKINGS_RAW_US)
    raw_ca = cache.get(CACHE_KEY_BOOKINGS_RAW_CA)

    if raw_us is None and raw_ca is None:
        logger.info("Bookings raw cache miss — running synchronous fetch.")
        refresh_bookings_cache()
        raw_us = cache.get(CACHE_KEY_BOOKINGS_RAW_US)
        raw_ca = cache.get(CACHE_KEY_BOOKINGS_RAW_CA)

    return raw_us or [], raw_ca or []


# ─────────────────────────────────────────────────────────────
# Open Orders Cache (snapshot + raw)
# ─────────────────────────────────────────────────────────────

def refresh_open_orders_cache():
    """
    Fetch fresh open orders data from SQL for both US and Canada.
    Caches both the dashboard snapshot AND raw export data in one pass,
    so Excel exports never hit SQL Server directly.
    Uses a longer cache timeout (65 min) since open orders refresh hourly.
    """
    logger.info("Worker: Refreshing open orders cache (US + CA)...")

    try:
        # ── US snapshot ──
        result_us = fetch_open_orders_snapshot_us()
        if result_us is not None:
            cache.set(CACHE_KEY_OPEN_ORDERS_US, result_us, timeout=OO_CACHE_TIMEOUT)
            logger.info("Worker: US open orders snapshot cache updated.")
        else:
            logger.warning("Worker: US open orders snapshot returned None — keeping stale cache.")

        # ── CA snapshot ──
        result_ca = fetch_open_orders_snapshot_ca()
        if result_ca is not None:
            cache.set(CACHE_KEY_OPEN_ORDERS_CA, result_ca, timeout=OO_CACHE_TIMEOUT)
            logger.info("Worker: CA open orders snapshot cache updated.")
        else:
            logger.warning("Worker: CA open orders snapshot returned None — keeping stale cache.")

        # ── US raw export data ──
        raw_us = fetch_open_orders_raw_us()
        if raw_us is not None:
            cache.set(CACHE_KEY_OPEN_ORDERS_RAW_US, raw_us, timeout=OO_CACHE_TIMEOUT)
            logger.info(f"Worker: US open orders raw cache updated ({len(raw_us)} rows).")
        else:
            logger.warning("Worker: US open orders raw query returned None — keeping stale cache.")

        # ── CA raw export data ──
        raw_ca = fetch_open_orders_raw_ca()
        if raw_ca is not None:
            cache.set(CACHE_KEY_OPEN_ORDERS_RAW_CA, raw_ca, timeout=OO_CACHE_TIMEOUT)
            logger.info(f"Worker: CA open orders raw cache updated ({len(raw_ca)} rows).")
        else:
            logger.warning("Worker: CA open orders raw query returned None — keeping stale cache.")

        # ── Timestamp ──
        if any(x is not None for x in [result_us, result_ca, raw_us, raw_ca]):
            cache.set(CACHE_KEY_OPEN_ORDERS_UPDATED, datetime.now(), timeout=OO_CACHE_TIMEOUT)

    except Exception as e:
        logger.error(f"Worker: Failed to refresh open orders cache: {e}")


def get_open_orders_from_cache():
    """
    Read open orders dashboard data from cache. If empty, do a one-time synchronous fetch.
    Returns (us_snapshot, ca_snapshot, last_updated, cad_to_usd_rate)
    """
    data_us = cache.get(CACHE_KEY_OPEN_ORDERS_US)
    data_ca = cache.get(CACHE_KEY_OPEN_ORDERS_CA)
    updated = cache.get(CACHE_KEY_OPEN_ORDERS_UPDATED)
    cad_rate = cache.get(CACHE_KEY_CAD_RATE)

    if data_us is None and data_ca is None:
        logger.info("Open orders cache miss — running synchronous fetch.")
        refresh_open_orders_cache()
        data_us = cache.get(CACHE_KEY_OPEN_ORDERS_US)
        data_ca = cache.get(CACHE_KEY_OPEN_ORDERS_CA)
        updated = cache.get(CACHE_KEY_OPEN_ORDERS_UPDATED)

    if cad_rate is None:
        cad_rate = DEFAULT_CAD_TO_USD

    return data_us, data_ca, updated, cad_rate


def get_open_orders_raw_from_cache():
    """
    Read open orders raw export data from cache. If empty, do a one-time synchronous fetch.
    Returns (us_rows, ca_rows) — each is a list of dicts or empty list.
    """
    raw_us = cache.get(CACHE_KEY_OPEN_ORDERS_RAW_US)
    raw_ca = cache.get(CACHE_KEY_OPEN_ORDERS_RAW_CA)

    if raw_us is None and raw_ca is None:
        logger.info("Open orders raw cache miss — running synchronous fetch.")
        refresh_open_orders_cache()
        raw_us = cache.get(CACHE_KEY_OPEN_ORDERS_RAW_US)
        raw_ca = cache.get(CACHE_KEY_OPEN_ORDERS_RAW_CA)

    return raw_us or [], raw_ca or []


# ─────────────────────────────────────────────────────────────
# Scheduled Refresh Functions
# ─────────────────────────────────────────────────────────────

def refresh_bookings_and_rate():
    """
    Called by scheduler every 10 minutes.
    Refreshes bookings snapshot + raw (US + CA) and the exchange rate.
    """
    logger.info("Worker: ═══ Bookings refresh (every 10 min) ═══")

    rate = _fetch_cad_to_usd_rate()
    cache.set(CACHE_KEY_CAD_RATE, rate, timeout=OO_CACHE_TIMEOUT)

    refresh_bookings_cache()

    logger.info("Worker: ═══ Bookings refresh complete ═══")


def refresh_open_orders_scheduled():
    """
    Called by scheduler every 60 minutes.
    Refreshes open orders snapshot + raw (US + CA).
    """
    logger.info("Worker: ═══ Open orders refresh (every 60 min) ═══")
    refresh_open_orders_cache()
    logger.info("Worker: ═══ Open orders refresh complete ═══")


def refresh_all_on_startup():
    """
    Called once on app startup to populate all caches immediately.

    Startup sequence:
      1. Exchange rate (needed by everything)
      2. Daily bookings (snapshot + raw, today only)
      3. Open orders (snapshot + raw, all open lines)
      4. Bookings Summary MTD/QTD/YTD:
         - Auto-freezes any completed months missing from disk (one-time SQL per month)
         - Reads frozen files from disk (<1ms each)
         - Queries only current month from sotran (small, fast)
         - Assembles MTD/QTD/YTD with YoY from prior year frozen files
         - Populates Executive Dashboard cache for current year (side effect)

    First startup with no frozen files: ~15-20 sec (freezes all prior months from SQL)
    Subsequent startups: ~3-5 sec (reads from disk, only current month from SQL)
    """
    logger.info("Worker: ═══ Initial startup refresh ═══")

    # 1. Exchange rate
    rate = _fetch_cad_to_usd_rate()
    cache.set(CACHE_KEY_CAD_RATE, rate, timeout=OO_CACHE_TIMEOUT)

    # 2. Daily bookings
    refresh_bookings_cache()

    # 3. Open orders
    refresh_open_orders_cache()

    # 4. Bookings Summary (frozen files + current month) + Dashboard cache
    from services.bookings_summary_service import refresh_bookings_summary
    logger.info("Worker: Refreshing Bookings Summary (frozen months + live current month)...")
    refresh_bookings_summary(cad_rate=rate)

    logger.info("Worker: ═══ Startup complete — all caches warm ═══")