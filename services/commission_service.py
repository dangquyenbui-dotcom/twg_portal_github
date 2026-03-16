"""
Commission Service
Manages per-salesman commission payout rates and calculates estimated commissions.

Rates are stored in commission_rates.json and cached via Flask-Caching.
Commission = individual margin × payout rate, plus optional 25% Mayhem Multiplier
bonus if the territory meets its stretch goal.

Usage:
    from services.commission_service import calculate_commission, get_commission_rate

    rate = get_commission_rate('MARY')        # 0.008 (0.80%)
    result = calculate_commission(margin, 'MARY', territory_invoiced, territory_goal)
"""

import json
import logging
import os
from calendar import monthrange
from datetime import date, datetime

from extensions import cache

logger = logging.getLogger(__name__)

# ── Defaults ──
DEFAULT_COMMISSION_RATE = 0.025   # 2.50%
MAYHEM_MULTIPLIER = 0.25         # 25% bonus
RATES_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'commission_rates.json')
CACHE_KEY_RATES = 'commission_rates'
RATES_CACHE_TTL = 3600  # 1 hour


# ═══════════════════════════════════════════════════════════════
# File I/O
# ═══════════════════════════════════════════════════════════════

def _load_rates_file():
    """Read commission_rates.json from disk. Returns dict or empty default."""
    if not os.path.exists(RATES_FILE):
        return {'rates': {}, 'updated_at': None}
    try:
        with open(RATES_FILE, 'r') as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.error(f"Commission: Failed to read {RATES_FILE}: {e}")
        return {'rates': {}, 'updated_at': None}


def _save_rates_file(data):
    """Write commission_rates.json to disk and invalidate cache."""
    data['updated_at'] = datetime.now().isoformat()
    try:
        with open(RATES_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        cache.delete(CACHE_KEY_RATES)
        logger.info(f"Commission: Saved {len(data.get('rates', {}))} rates to {RATES_FILE}")
    except Exception as e:
        logger.error(f"Commission: Failed to write {RATES_FILE}: {e}")
        raise


# ═══════════════════════════════════════════════════════════════
# Public API — Rate Management
# ═══════════════════════════════════════════════════════════════

def get_all_commission_rates():
    """Return the full rates dict (for admin UI)."""
    cached = cache.get(CACHE_KEY_RATES)
    if cached is not None:
        return cached
    data = _load_rates_file()
    cache.set(CACHE_KEY_RATES, data, timeout=RATES_CACHE_TTL)
    return data


def get_commission_rate(salesman_code):
    """
    Get the commission payout rate for a salesman.
    Returns float (e.g. 0.025 for 2.5%). Falls back to DEFAULT_COMMISSION_RATE.
    """
    data = get_all_commission_rates()
    rates = data.get('rates', {})
    code = (salesman_code or '').strip().upper()
    return rates.get(code, DEFAULT_COMMISSION_RATE)


def save_commission_rate(salesman_code, rate_pct):
    """
    Save a commission rate for a salesman.

    Args:
        salesman_code: e.g. 'MARY'
        rate_pct: percentage as float (e.g. 2.5 for 2.5%), will be stored as decimal (0.025)
    """
    code = (salesman_code or '').strip().upper()
    if not code:
        raise ValueError("Salesman code is required")
    if rate_pct < 0 or rate_pct > 100:
        raise ValueError("Rate must be between 0 and 100")

    data = _load_rates_file()
    data['rates'][code] = round(rate_pct / 100, 6)  # Store as decimal
    _save_rates_file(data)
    logger.info(f"Commission: Set {code} = {rate_pct}%")


def delete_commission_rate(salesman_code):
    """Remove a salesman's commission rate (they'll use the default)."""
    code = (salesman_code or '').strip().upper()
    data = _load_rates_file()
    if code in data.get('rates', {}):
        del data['rates'][code]
        _save_rates_file(data)
        logger.info(f"Commission: Deleted rate for {code}")
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# Public API — Commission Calculation
# ═══════════════════════════════════════════════════════════════

def calculate_commission(total_margin, salesman_code, territory_invoiced=None,
                         territory_goal=None, year=None, month=None):
    """
    Calculate estimated commission for a salesman.

    Args:
        total_margin: Individual salesman's margin for the month
        salesman_code: Salesman code for rate lookup
        territory_invoiced: Total territory-wide invoiced amount (for Mayhem)
        territory_goal: Territory stretch goal amount (for Mayhem)
        year, month: Period (for pacing projection, defaults to current month)

    Returns:
        dict with: base_commission, mayhem_bonus, total_commission,
                   mayhem_eligible, mayhem_met, mayhem_projected, payout_rate
    """
    payout_rate = get_commission_rate(salesman_code)
    base = total_margin * payout_rate

    # Mayhem Multiplier — 25% bonus if territory meets its goal
    mayhem_eligible = (
        territory_invoiced is not None
        and territory_goal is not None
        and territory_goal > 0
    )
    mayhem_met = mayhem_eligible and territory_invoiced >= territory_goal
    mayhem_projected = False
    mayhem_bonus = 0

    if mayhem_met:
        mayhem_bonus = base * MAYHEM_MULTIPLIER
        mayhem_projected = True
    elif mayhem_eligible:
        # Check if territory is on pace to meet goal (current month only)
        today = date.today()
        y = year or today.year
        m = month or today.month
        is_current = (y == today.year and m == today.month)

        if is_current and today.day > 1:
            _, last_day = monthrange(y, m)
            pace = territory_invoiced / today.day * last_day
            mayhem_projected = pace >= territory_goal

    return {
        'base_commission': round(base, 2),
        'mayhem_bonus': round(mayhem_bonus, 2),
        'total_commission': round(base + mayhem_bonus, 2),
        'mayhem_eligible': mayhem_eligible,
        'mayhem_met': mayhem_met,
        'mayhem_projected': mayhem_projected,
        'payout_rate': payout_rate,
    }
