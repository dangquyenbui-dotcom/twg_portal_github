"""
Session Tracker Service
Tracks active user sessions — who is signed in, when, from where.

Sessions are stored in active_sessions.json and cached via Flask-Caching.
Keyed by Entra ID Object ID (oid) so each user has one entry.

Usage:
    from services.session_tracker import record_login, update_activity, record_logout
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

from extensions import cache

logger = logging.getLogger(__name__)

# ── Config ──
SESSIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'active_sessions.json')
CACHE_KEY_SESSIONS = 'active_sessions'
SESSIONS_CACHE_TTL = 30              # 30 seconds (freshness matters for admin view)
ACTIVE_THRESHOLD_MINUTES = 30        # "active" = last activity within 30 min
STALE_CLEANUP_HOURS = 24             # auto-remove entries older than 24 hours
ACTIVITY_FLUSH_INTERVAL = 60         # only write to disk every 60s per user

_lock = threading.Lock()
_last_flush = {}        # oid → time.time() of last disk write
_activity_cache = {}    # oid → last_activity ISO string (in-memory, flushed periodically)


# ═══════════════════════════════════════════════════════════════
# File I/O
# ═══════════════════════════════════════════════════════════════

def _load_sessions_file():
    """Read active_sessions.json from disk."""
    if not os.path.exists(SESSIONS_FILE):
        return {'sessions': {}}
    try:
        with open(SESSIONS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"SessionTracker: Failed to read {SESSIONS_FILE}: {e}")
        return {'sessions': {}}


def _save_sessions_file(data):
    """Write active_sessions.json to disk and invalidate cache."""
    data['updated_at'] = datetime.now().isoformat()
    try:
        with open(SESSIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        cache.delete(CACHE_KEY_SESSIONS)
    except Exception as e:
        logger.error(f"SessionTracker: Failed to write {SESSIONS_FILE}: {e}")


def _get_sessions():
    """Cached read of sessions data."""
    cached = cache.get(CACHE_KEY_SESSIONS)
    if cached is not None:
        return cached
    data = _load_sessions_file()
    cache.set(CACHE_KEY_SESSIONS, data, timeout=SESSIONS_CACHE_TTL)
    return data


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def record_login(oid, name, email, roles, ip_address, user_agent):
    """Record or update a user's session on login. Returns session_token for single-session enforcement."""
    if not oid:
        return None
    now = datetime.now().isoformat()
    token = str(uuid.uuid4())
    with _lock:
        data = _load_sessions_file()
        data['sessions'][oid] = {
            'oid': oid,
            'name': name or 'Unknown',
            'email': email or 'Unknown',
            'roles': roles or [],
            'ip_address': ip_address or 'Unknown',
            'user_agent': user_agent or 'Unknown',
            'login_time': now,
            'last_activity': now,
            'session_token': token,
        }
        _save_sessions_file(data)
        _last_flush[oid] = time.time()
        _activity_cache[oid] = now
    logger.info(f"SessionTracker: Login recorded for {email} from {ip_address}")
    return token


def has_existing_session(oid):
    """Check if a user already has an active session. Returns session info dict or None.
    Only returns existing session if it has a session_token (enforced session)
    and was active within ACTIVE_THRESHOLD_MINUTES (truly active, not just <24h)."""
    if not oid:
        return None
    data = _get_sessions()
    session_data = data.get('sessions', {}).get(oid)
    if not session_data:
        return None
    # Skip sessions without a token (legacy/auto-registered — can't be kicked)
    if not session_data.get('session_token'):
        return None
    # Only flag if session was active recently (not stale re-login from same device)
    last_dt = _parse_dt(session_data.get('last_activity', ''))
    if datetime.now() - last_dt > timedelta(minutes=ACTIVE_THRESHOLD_MINUTES):
        return None
    return {
        'user_agent_short': _parse_user_agent(session_data.get('user_agent', '')),
        'ip_address': session_data.get('ip_address', 'Unknown'),
        'last_activity_fmt': _format_relative(last_dt, datetime.now()),
    }


def check_session_token(oid, token):
    """Check if a session token matches. Returns True if valid, False if kicked out."""
    if not oid or not token:
        return True  # No token to check (legacy session)
    data = _get_sessions()
    session_data = data.get('sessions', {}).get(oid)
    if not session_data:
        return True  # No tracked session, allow
    stored_token = session_data.get('session_token')
    if not stored_token:
        return True  # Old session without token, allow
    return stored_token == token


def update_activity(oid, name=None, email=None, roles=None, ip_address=None, user_agent=None):
    """Update last_activity for a user. Auto-registers if not yet tracked. Throttled to reduce disk I/O."""
    if not oid:
        return
    now_ts = time.time()
    now_iso = datetime.now().isoformat()

    # Always update in-memory
    _activity_cache[oid] = now_iso

    # Only flush to disk if enough time has passed
    last = _last_flush.get(oid, 0)
    if now_ts - last < ACTIVITY_FLUSH_INTERVAL:
        # But force flush if this is an unknown session (auto-register)
        data = _get_sessions()
        if oid in data.get('sessions', {}):
            return

    with _lock:
        data = _load_sessions_file()
        if oid in data['sessions']:
            data['sessions'][oid]['last_activity'] = now_iso
            # Update IP and user agent in case they changed
            if ip_address:
                data['sessions'][oid]['ip_address'] = ip_address
            if user_agent:
                data['sessions'][oid]['user_agent'] = user_agent
        else:
            # Auto-register: user was logged in before tracking started
            data['sessions'][oid] = {
                'oid': oid,
                'name': name or 'Unknown',
                'email': email or 'Unknown',
                'roles': roles or [],
                'ip_address': ip_address or 'Unknown',
                'user_agent': user_agent or 'Unknown',
                'login_time': now_iso,
                'last_activity': now_iso,
            }
            logger.info(f"SessionTracker: Auto-registered session for {email}")
        _save_sessions_file(data)
        _last_flush[oid] = now_ts


def record_logout(oid):
    """Remove a user's session on logout."""
    if not oid:
        return
    with _lock:
        data = _load_sessions_file()
        if oid in data['sessions']:
            email = data['sessions'][oid].get('email', 'unknown')
            del data['sessions'][oid]
            _save_sessions_file(data)
            logger.info(f"SessionTracker: Logout recorded for {email}")
    _last_flush.pop(oid, None)
    _activity_cache.pop(oid, None)


def cleanup_stale_sessions():
    """Remove sessions older than STALE_CLEANUP_HOURS."""
    cutoff = datetime.now() - timedelta(hours=STALE_CLEANUP_HOURS)
    with _lock:
        data = _load_sessions_file()
        sessions = data.get('sessions', {})
        stale = [
            oid for oid, s in sessions.items()
            if _parse_dt(s.get('last_activity')) < cutoff
        ]
        if stale:
            for oid in stale:
                del sessions[oid]
                _last_flush.pop(oid, None)
                _activity_cache.pop(oid, None)
            _save_sessions_file(data)
            logger.info(f"SessionTracker: Cleaned up {len(stale)} stale session(s)")


def get_active_sessions_for_display():
    """
    Return list of session dicts for the admin UI, sorted by last_activity desc.
    Each dict is enriched with: is_active, login_time_fmt, last_activity_fmt, user_agent_short.
    Also auto-cleans stale entries.
    """
    cleanup_stale_sessions()
    data = _load_sessions_file()  # Fresh read after cleanup
    sessions = data.get('sessions', {})
    now = datetime.now()
    threshold = now - timedelta(minutes=ACTIVE_THRESHOLD_MINUTES)

    result = []
    for oid, s in sessions.items():
        # Use in-memory activity time if fresher
        last_activity_str = _activity_cache.get(oid, s.get('last_activity', ''))
        last_dt = _parse_dt(last_activity_str)
        login_dt = _parse_dt(s.get('login_time', ''))

        result.append({
            'name': s.get('name', 'Unknown'),
            'email': s.get('email', 'Unknown'),
            'roles': s.get('roles', []),
            'ip_address': s.get('ip_address', 'Unknown'),
            'user_agent': s.get('user_agent', 'Unknown'),
            'user_agent_short': _parse_user_agent(s.get('user_agent', '')),
            'login_time_fmt': _format_datetime(login_dt),
            'last_activity_fmt': _format_relative(last_dt, now),
            'is_active': last_dt >= threshold,
        })

    result.sort(key=lambda x: x['last_activity_fmt'], reverse=False)
    # Sort active first, then by name
    result.sort(key=lambda x: (not x['is_active'], x['name']))
    return result


# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _parse_dt(iso_str):
    """Parse ISO datetime string, return datetime or epoch."""
    if not iso_str:
        return datetime.min
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return datetime.min


def _format_datetime(dt):
    """Format datetime as 'Mar 18, 2:15 PM'."""
    if dt == datetime.min:
        return '—'
    return dt.strftime('%b %d, %I:%M %p').replace(' 0', ' ')


def _format_relative(dt, now):
    """Format as relative time: '2 min ago', '1 hr ago', or absolute if > 12h."""
    if dt == datetime.min:
        return '—'
    diff = now - dt
    minutes = int(diff.total_seconds() / 60)
    if minutes < 1:
        return 'Just now'
    if minutes < 60:
        return f'{minutes} min ago'
    hours = minutes // 60
    if hours < 12:
        return f'{hours} hr{"s" if hours > 1 else ""} ago'
    return _format_datetime(dt)


def _parse_user_agent(ua):
    """Parse User-Agent string to a short label like 'Chrome / Windows'."""
    if not ua or ua == 'Unknown':
        return 'Unknown'

    # Browser detection
    browser = 'Other'
    if 'Edg/' in ua or 'Edge/' in ua:
        browser = 'Edge'
    elif 'Chrome/' in ua and 'Safari/' in ua:
        browser = 'Chrome'
    elif 'Firefox/' in ua:
        browser = 'Firefox'
    elif 'Safari/' in ua and 'Chrome/' not in ua:
        browser = 'Safari'

    # OS detection
    os_name = 'Other'
    if 'iPhone' in ua or 'iPad' in ua:
        os_name = 'iPhone' if 'iPhone' in ua else 'iPad'
    elif 'Android' in ua:
        os_name = 'Android'
    elif 'Windows' in ua:
        os_name = 'Windows'
    elif 'Mac OS' in ua or 'Macintosh' in ua:
        os_name = 'Mac'
    elif 'Linux' in ua:
        os_name = 'Linux'

    return f'{browser} / {os_name}'
