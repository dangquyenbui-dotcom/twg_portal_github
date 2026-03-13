"""
TWG Portal - Main Application
"""

import logging
import atexit
import requests
from flask import Flask, session, redirect, url_for, request, render_template, send_from_directory
from config import Config
from extensions import cache, scheduler
import auth.entra_auth as auth_utils
from auth.decorators import user_has_role
from routes.main import main_bp
from routes.sales import sales_bp
from routes.admin import admin_bp
from services.data_worker import refresh_bookings_and_rate, refresh_open_orders_scheduled, refresh_all_on_startup
from services.bookings_dashboard_data_service import refresh_dashboard_current_month
from services.bookings_summary_service import refresh_bookings_summary_scheduled
from services.shipments_summary_service import refresh_shipments_summary_scheduled
from services.health_monitor import send_daily_summary

# --- Logging: INFO level only ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('msal').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)
logging.getLogger('apscheduler').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def _build_redirect_uri():
    """
    Build the OAuth redirect URI, forcing HTTPS for non-localhost environments.
    Behind a reverse proxy (IIS/nginx with SSL termination), Flask sees http://
    from request.url_root even though users access via https://. Azure Entra ID
    requires https:// for all redirect URIs except localhost, so we force it.
    """
    if Config.REDIRECT_URI_OVERRIDE:
        return Config.REDIRECT_URI_OVERRIDE

    base = request.url_root.rstrip('/')

    host = request.host.split(':')[0]
    if host not in ('localhost', '127.0.0.1') and base.startswith('http://'):
        base = 'https://' + base[len('http://'):]

    redirect_uri = base + Config.REDIRECT_PATH
    logger.info(f"Login: Built redirect_uri: {redirect_uri}")
    return redirect_uri


def _fetch_employee_id(access_token):
    """
    Call Microsoft Graph /me endpoint to read the user's employeeId.
    This is the ERP salesman code set on the user's Job Information in Entra ID.
    Returns the employeeId string, or '' if not set / on error.
    """
    try:
        resp = requests.get(
            'https://graph.microsoft.com/v1.0/me?$select=employeeId',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10,
        )
        if resp.status_code == 200:
            eid = (resp.json().get('employeeId') or '').strip()
            if eid:
                logger.info(f"Graph: employeeId = {eid}")
            return eid
        else:
            logger.warning(f"Graph /me failed: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"Graph /me error: {e}")
    return ''


def _resolve_roles_from_groups(group_ids):
    """
    Convert a list of Entra ID Security Group Object IDs into internal role names
    using the GROUP_ROLE_MAP from config. Returns a list of role name strings.
    """
    if not group_ids:
        return []

    roles = []
    for gid in group_ids:
        role = Config.GROUP_ROLE_MAP.get(gid)
        if role:
            roles.append(role)

    return roles


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    Config.validate()

    # --- Register Jinja2 global so templates can check roles with hierarchy ---
    app.jinja_env.globals['user_has_role'] = user_has_role

    # --- Init Cache ---
    cache.init_app(app)

    # --- Init Scheduler ---
    if not scheduler.running:
        scheduler.init_app(app)
        scheduler.start()
        logger.info("Scheduler started.")

    # ── Bookings + Shipments refresh (every 10 min) ──
    if not scheduler.get_job('bookings_refresh'):
        scheduler.add_job(
            id='bookings_refresh',
            func=refresh_bookings_and_rate,
            trigger='interval',
            seconds=Config.DATA_REFRESH_INTERVAL,
            misfire_grace_time=60
        )
        logger.info(f"Scheduled 'bookings_refresh' every {Config.DATA_REFRESH_INTERVAL}s")

    # ── Open orders refresh (every 60 min) ──
    if not scheduler.get_job('open_orders_refresh'):
        scheduler.add_job(
            id='open_orders_refresh',
            func=refresh_open_orders_scheduled,
            trigger='interval',
            seconds=Config.OPEN_ORDERS_REFRESH_INTERVAL,
            misfire_grace_time=120
        )
        logger.info(f"Scheduled 'open_orders_refresh' every {Config.OPEN_ORDERS_REFRESH_INTERVAL}s")

    # ── Dashboard current month refresh (every 60 min) ──
    if not scheduler.get_job('dashboard_current_refresh'):
        scheduler.add_job(
            id='dashboard_current_refresh',
            func=refresh_dashboard_current_month,
            trigger='interval',
            seconds=Config.DASHBOARD_REFRESH_INTERVAL,
            misfire_grace_time=120
        )
        logger.info(f"Scheduled 'dashboard_current_refresh' every {Config.DASHBOARD_REFRESH_INTERVAL}s")

    # ── Bookings Summary MTD/QTD/YTD refresh (every 30 min) ──
    if not scheduler.get_job('bookings_summary_refresh'):
        scheduler.add_job(
            id='bookings_summary_refresh',
            func=refresh_bookings_summary_scheduled,
            trigger='interval',
            seconds=Config.BOOKINGS_SUMMARY_REFRESH_INTERVAL,
            misfire_grace_time=120
        )
        logger.info(f"Scheduled 'bookings_summary_refresh' every {Config.BOOKINGS_SUMMARY_REFRESH_INTERVAL}s")

    # ── Shipments Summary MTD/QTD/YTD refresh (every 30 min) ──
    if not scheduler.get_job('shipments_summary_refresh'):
        scheduler.add_job(
            id='shipments_summary_refresh',
            func=refresh_shipments_summary_scheduled,
            trigger='interval',
            seconds=Config.SHIPMENTS_SUMMARY_REFRESH_INTERVAL,
            misfire_grace_time=120
        )
        logger.info(f"Scheduled 'shipments_summary_refresh' every {Config.SHIPMENTS_SUMMARY_REFRESH_INTERVAL}s")

    # ── Daily health summary email (7:00 AM server time) ──
    if not scheduler.get_job('daily_health_summary'):
        scheduler.add_job(
            id='daily_health_summary',
            func=send_daily_summary,
            trigger='cron',
            hour=7, minute=0,
            misfire_grace_time=300,
        )
        logger.info("Scheduled 'daily_health_summary' at 7:00 AM daily")

    # ── Goals refresh from SharePoint (daily at 2:00 AM) ──
    from services.goals_service import refresh_goals_cache
    if not scheduler.get_job('goals_refresh'):
        scheduler.add_job(
            id='goals_refresh',
            func=refresh_goals_cache,
            trigger='cron',
            hour=2, minute=0,
            misfire_grace_time=600,
        )
        logger.info("Scheduled 'goals_refresh' at 2:00 AM daily")

    with app.app_context():
        logger.info("Running initial startup refresh (all data + dashboard current year)...")
        refresh_all_on_startup()
        # refresh_all_on_startup now handles:
        #   1. Exchange rate
        #   2. Daily bookings (snapshot + raw)
        #   3. Daily shipments (snapshot + raw)
        #   4. Open orders (snapshot + raw)
        #   5. Bookings Summary MTD/QTD/YTD + prior year comparisons
        #   6. Shipments Summary MTD/QTD/YTD + prior year comparisons
        #   7. Dashboard current year cache (populated as side effect of YTD)
        # Past years on the dashboard still use frozen files — no change.
        logger.info("All caches warm. Every page loads instantly from first request.")

    atexit.register(lambda: scheduler.shutdown() if scheduler.running else None)

    # --- Register Blueprints ---
    app.register_blueprint(main_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(admin_bp)

    # --- PWA: Safari probes these root paths for the home screen icon ---
    @app.route('/apple-touch-icon.png')
    @app.route('/apple-touch-icon-precomposed.png')
    @app.route('/apple-touch-icon-120x120.png')
    @app.route('/apple-touch-icon-120x120-precomposed.png')
    @app.route('/apple-touch-icon-152x152.png')
    @app.route('/apple-touch-icon-152x152-precomposed.png')
    @app.route('/apple-touch-icon-180x180.png')
    @app.route('/apple-touch-icon-180x180-precomposed.png')
    def apple_touch_icon():
        return send_from_directory(
            app.static_folder, 'logo/apple-touch-icon.png',
            mimetype='image/png',
            max_age=86400
        )

    # --- SSO ROUTES ---
    @app.route("/login")
    def login():
        try:
            redirect_uri = _build_redirect_uri()

            flow = auth_utils._build_msal_app().initiate_auth_code_flow(
                Config.SCOPE,
                redirect_uri=redirect_uri
            )

            if "error" in flow:
                return render_template("login.html", error=flow.get("error_description", flow.get("error")))

            session["flow"] = flow
            return redirect(flow["auth_uri"])
        except Exception as e:
            logger.exception(f"Login failed: {e}")
            return render_template("login.html", error=f"Login initialization failed: {str(e)}")

    @app.route("/auth/redirect")
    def authorized():
        flow = session.get("flow")
        if not flow:
            return redirect(url_for("main.login_page"))

        try:
            result = auth_utils.get_token_from_code(
                auth_response=request.args.to_dict(),
                auth_code_flow=flow
            )

            if "error" in result:
                error_msg = result.get("error_description", result.get("error"))
                return render_template("login.html", error=error_msg)

            user_claims = result.get("id_token_claims")

            # ── Resolve Security Group IDs → internal role names ──
            group_ids = user_claims.get("groups", [])
            roles = _resolve_roles_from_groups(group_ids)

            # ── Fetch salesman code from Microsoft Graph (employeeId) ──
            salesman_code = ''
            access_token = result.get("access_token")
            if access_token:
                salesman_code = _fetch_employee_id(access_token)

            session["user"] = {
                "name": user_claims.get("name"),
                "email": user_claims.get("preferred_username"),
                "oid": user_claims.get("oid"),
                "tid": user_claims.get("tid"),
                "groups": group_ids,
                "roles": roles,
                "salesman_code": salesman_code,
            }
            session.pop("flow", None)
            logger.info(
                f"User authenticated: {session['user'].get('email')} "
                f"| groups: {len(group_ids)} "
                f"| roles: {roles}"
            )
            return redirect(url_for("main.index"))

        except Exception as e:
            logger.exception("Auth route crashed")
            return render_template("login.html", error=f"Authentication failed: {str(e)}")

    @app.route("/logout")
    def logout():
        post_logout_uri = request.url_root.rstrip('/') + '/login_page'
        session.clear()
        return redirect(
            f"{Config.AUTHORITY}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={post_logout_uri}"
        )

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=False)