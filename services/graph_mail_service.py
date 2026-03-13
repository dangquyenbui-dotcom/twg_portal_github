"""
Graph Mail Service
Sends emails via Microsoft Graph API using client credentials flow.

Uses the same CLIENT_ID / CLIENT_SECRET / TENANT_ID already configured
for SSO authentication. Requires 'Mail.Send' application permission
granted via Azure Portal → App Registrations → API Permissions.

Usage:
    from services.graph_mail_service import send_email, send_alert

    send_email('Subject', '<p>Body HTML</p>')        # to default ALERT_EMAIL_TO
    send_alert('Alert Title', '<p>Details</p>')      # prepends [TWG Portal Alert]
"""

import logging
import requests
from msal import ConfidentialClientApplication
from config import Config

logger = logging.getLogger(__name__)

# ── Module-level MSAL app (reused for token caching) ──
_msal_app = None


def _get_msal_app():
    """Lazy-init MSAL ConfidentialClientApplication for client credentials."""
    global _msal_app
    if _msal_app is None:
        _msal_app = ConfidentialClientApplication(
            client_id=Config.CLIENT_ID,
            client_credential=Config.CLIENT_SECRET,
            authority=Config.AUTHORITY,
        )
    return _msal_app


def _get_graph_token():
    """
    Acquire an access token for Microsoft Graph using client credentials flow.
    Returns the access token string, or None on failure.
    """
    try:
        app = _get_msal_app()
        result = app.acquire_token_for_client(
            scopes=['https://graph.microsoft.com/.default']
        )

        if 'access_token' in result:
            return result['access_token']

        error = result.get('error', 'unknown')
        error_desc = result.get('error_description', '')
        logger.error(f"Graph token acquisition failed: {error} — {error_desc}")
        return None

    except Exception as e:
        logger.error(f"Graph token acquisition error: {e}")
        return None


def send_email(subject, body_html, to_email=None):
    """
    Send an email via Microsoft Graph API.

    Args:
        subject:   Email subject line.
        body_html: Email body as HTML string.
        to_email:  Recipient email (defaults to Config.ALERT_EMAIL_TO).

    Returns:
        True on success (HTTP 202), False on failure.
    """
    to_email = to_email or Config.ALERT_EMAIL_TO
    from_email = Config.ALERT_EMAIL_FROM

    if not from_email or not to_email:
        logger.warning("Graph mail: ALERT_EMAIL_FROM or ALERT_EMAIL_TO not configured.")
        return False

    token = _get_graph_token()
    if not token:
        logger.error("Graph mail: Could not acquire access token — email not sent.")
        return False

    url = f"{Config.GRAPH_API_BASE}/users/{from_email}/sendMail"
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body_html,
            },
            "toRecipients": [
                {"emailAddress": {"address": to_email}}
            ],
        },
        "saveToSentItems": "false",
    }

    try:
        resp = requests.post(
            url,
            json=payload,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )

        if resp.status_code == 202:
            logger.info(f"Graph mail: Sent '{subject}' to {to_email}")
            return True

        logger.error(
            f"Graph mail: Failed to send — HTTP {resp.status_code}: "
            f"{resp.text[:500]}"
        )
        return False

    except Exception as e:
        logger.error(f"Graph mail: Request error — {e}")
        return False


def send_alert(subject, body_html):
    """
    Convenience wrapper: sends an alert email with [TWG Portal Alert] prefix.
    """
    return send_email(f"[TWG Portal Alert] {subject}", body_html)
