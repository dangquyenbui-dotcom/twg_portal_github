"""
Health Monitor
Tracks the status of all background refresh jobs and dispatches email alerts
when failures occur. Provides a daily summary email.

Usage:
    from services.health_monitor import report_success, report_failure

    # Inside a refresh function:
    report_success('bookings_us')
    report_failure('bookings_us', 'Connection refused')

    # For admin UI:
    from services.health_monitor import get_health_summary

    # For scheduled daily summary:
    from services.health_monitor import send_daily_summary
"""

import logging
import threading
from copy import deepcopy
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Alert throttle: max 1 alert per component per 30 minutes ──
ALERT_THROTTLE_MINUTES = 30

# ── Component registry ──
# Each entry tracks its current status, timestamps, and alert throttle.
_health_status = {}
_health_lock = threading.Lock()

# All known components — initialized lazily on first access
_COMPONENTS = [
    'bookings_us', 'bookings_ca',
    'shipments_us', 'shipments_ca',
    'open_orders_us', 'open_orders_ca',
    'exchange_rate',
    'bookings_summary', 'shipments_summary',
    'goals_refresh',
]

_DISPLAY_NAMES = {
    'bookings_us':       'Daily Bookings (US)',
    'bookings_ca':       'Daily Bookings (CA)',
    'shipments_us':      'Daily Shipments (US)',
    'shipments_ca':      'Daily Shipments (CA)',
    'open_orders_us':    'Open Orders (US)',
    'open_orders_ca':    'Open Orders (CA)',
    'exchange_rate':     'Exchange Rate (CAD→USD)',
    'bookings_summary':  'Bookings Summary (MTD/QTD/YTD)',
    'shipments_summary': 'Shipments Summary (MTD/QTD/YTD)',
    'goals_refresh':     'Goals (SharePoint)',
}


def _ensure_component(component):
    """Initialize a component entry if it doesn't exist yet."""
    if component not in _health_status:
        _health_status[component] = {
            'status': 'ok',
            'last_success': None,
            'last_failure': None,
            'error': None,
            'last_alert_sent': None,
        }


def report_success(component):
    """
    Record a successful refresh for the given component.
    Clears any previous error state.
    """
    with _health_lock:
        _ensure_component(component)
        _health_status[component]['status'] = 'ok'
        _health_status[component]['last_success'] = datetime.now()
        _health_status[component]['error'] = None


def report_failure(component, error_message):
    """
    Record a failed refresh for the given component.
    Sends an alert email if the throttle window has passed.
    """
    now = datetime.now()

    with _health_lock:
        _ensure_component(component)
        entry = _health_status[component]
        entry['status'] = 'error'
        entry['last_failure'] = now
        entry['error'] = str(error_message)[:500]  # truncate very long errors

        # Throttle: only send alert if we haven't sent one recently for this component
        last_alert = entry.get('last_alert_sent')
        should_alert = (
            last_alert is None or
            (now - last_alert) > timedelta(minutes=ALERT_THROTTLE_MINUTES)
        )

        if should_alert:
            entry['last_alert_sent'] = now

    # Send alert email outside the lock (network I/O)
    if should_alert:
        _send_failure_alert(component, error_message)


def _send_failure_alert(component, error_message):
    """Send a failure alert email for a specific component."""
    try:
        from services.graph_mail_service import send_alert

        display_name = _DISPLAY_NAMES.get(component, component)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        body = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 600px;">
            <h2 style="color: #dc2626; margin-bottom: 16px;">
                &#x26A0; Refresh Failure: {display_name}
            </h2>
            <table style="border-collapse: collapse; width: 100%; margin-bottom: 20px;">
                <tr>
                    <td style="padding: 8px 12px; border: 1px solid #e5e7eb; font-weight: 600; background: #f9fafb; width: 140px;">Component</td>
                    <td style="padding: 8px 12px; border: 1px solid #e5e7eb;">{display_name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border: 1px solid #e5e7eb; font-weight: 600; background: #f9fafb;">Time</td>
                    <td style="padding: 8px 12px; border: 1px solid #e5e7eb;">{now_str}</td>
                </tr>
                <tr>
                    <td style="padding: 8px 12px; border: 1px solid #e5e7eb; font-weight: 600; background: #f9fafb;">Error</td>
                    <td style="padding: 8px 12px; border: 1px solid #e5e7eb; color: #dc2626;">
                        <code style="font-size: 12px;">{error_message}</code>
                    </td>
                </tr>
            </table>
            <p style="color: #6b7280; font-size: 12px;">
                Alert throttle: max 1 per {ALERT_THROTTLE_MINUTES} min per component.
                Check Admin &rarr; Dashboard Data &rarr; System Health for live status.
            </p>
        </div>
        """

        send_alert(f"Refresh Failure: {display_name}", body)

    except Exception as e:
        logger.error(f"Health monitor: Failed to send alert email for {component}: {e}")


def get_health_summary():
    """
    Return the current health status of all components.
    Used by the admin UI to render the System Health tab.
    Returns a list of dicts sorted by component name.
    """
    with _health_lock:
        # Ensure all known components exist
        for comp in _COMPONENTS:
            _ensure_component(comp)

        result = []
        for comp in _COMPONENTS:
            entry = deepcopy(_health_status[comp])
            entry['component'] = comp
            entry['display_name'] = _DISPLAY_NAMES.get(comp, comp)
            # Format timestamps for display
            for key in ('last_success', 'last_failure', 'last_alert_sent'):
                if entry.get(key):
                    entry[key + '_fmt'] = entry[key].strftime('%Y-%m-%d %H:%M:%S')
                else:
                    entry[key + '_fmt'] = '—'
            result.append(entry)

        return result


def send_daily_summary():
    """
    Send a daily health summary email showing all component statuses.
    Called by the scheduler at 7:00 AM server time.
    Always sends — even when everything is OK — so the admin knows the system is alive.
    """
    try:
        from services.graph_mail_service import send_alert

        summary = get_health_summary()
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        all_ok = all(s['status'] == 'ok' for s in summary)
        error_count = sum(1 for s in summary if s['status'] == 'error')

        if all_ok:
            status_badge = '<span style="color: #059669; font-weight: 600;">All Systems Healthy &#x2705;</span>'
        else:
            status_badge = f'<span style="color: #dc2626; font-weight: 600;">{error_count} Component(s) Failing &#x26A0;</span>'

        rows_html = ''
        for s in summary:
            if s['status'] == 'ok':
                status_cell = '<td style="padding: 6px 10px; border: 1px solid #e5e7eb; color: #059669; font-weight: 600;">&#x2705; OK</td>'
            else:
                status_cell = '<td style="padding: 6px 10px; border: 1px solid #e5e7eb; color: #dc2626; font-weight: 600;">&#x274C; ERROR</td>'

            error_cell = f'<td style="padding: 6px 10px; border: 1px solid #e5e7eb; color: #dc2626; font-size: 11px;">{s["error"] or ""}</td>'

            rows_html += f"""
            <tr>
                <td style="padding: 6px 10px; border: 1px solid #e5e7eb; font-weight: 500;">{s['display_name']}</td>
                {status_cell}
                <td style="padding: 6px 10px; border: 1px solid #e5e7eb; font-size: 11px; font-family: monospace;">{s['last_success_fmt']}</td>
                <td style="padding: 6px 10px; border: 1px solid #e5e7eb; font-size: 11px; font-family: monospace;">{s['last_failure_fmt']}</td>
                {error_cell}
            </tr>
            """

        body = f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 750px;">
            <h2 style="margin-bottom: 8px;">TWG Portal — Daily Health Summary</h2>
            <p style="color: #6b7280; margin-bottom: 16px;">Generated: {now_str}</p>
            <p style="margin-bottom: 20px;">{status_badge}</p>

            <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
                <thead>
                    <tr style="background: #f9fafb;">
                        <th style="padding: 8px 10px; border: 1px solid #e5e7eb; text-align: left;">Component</th>
                        <th style="padding: 8px 10px; border: 1px solid #e5e7eb; text-align: left; width: 80px;">Status</th>
                        <th style="padding: 8px 10px; border: 1px solid #e5e7eb; text-align: left;">Last Success</th>
                        <th style="padding: 8px 10px; border: 1px solid #e5e7eb; text-align: left;">Last Failure</th>
                        <th style="padding: 8px 10px; border: 1px solid #e5e7eb; text-align: left;">Error</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>

            <p style="color: #9ca3af; font-size: 11px; margin-top: 16px;">
                This email is sent daily at 7:00 AM.
                View live status: Admin &rarr; Dashboard Data &rarr; System Health
            </p>
        </div>
        """

        subject = "Daily Health Summary"
        if not all_ok:
            subject = f"Daily Health Summary — {error_count} FAILING"

        send_alert(subject, body)
        logger.info(f"Health monitor: Daily summary sent. All OK: {all_ok}")

    except Exception as e:
        logger.error(f"Health monitor: Failed to send daily summary: {e}")
