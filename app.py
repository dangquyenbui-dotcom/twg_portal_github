"""
TWG Portal - Main Application
"""

import logging
import atexit
from flask import Flask, session, redirect, url_for, request, render_template
from config import Config
from extensions import cache, scheduler
import auth.entra_auth as auth_utils
from routes.main import main_bp
from routes.sales import sales_bp
from services.data_worker import refresh_bookings_and_rate, refresh_open_orders_scheduled, refresh_all_on_startup

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
    # If an explicit override is set in .env, use it as-is
    if Config.REDIRECT_URI_OVERRIDE:
        return Config.REDIRECT_URI_OVERRIDE

    # Build from request
    base = request.url_root.rstrip('/')

    # Force https for anything that isn't localhost/127.0.0.1
    host = request.host.split(':')[0]
    if host not in ('localhost', '127.0.0.1') and base.startswith('http://'):
        base = 'https://' + base[len('http://'):]

    redirect_uri = base + Config.REDIRECT_PATH
    logger.info(f"Login: Built redirect_uri: {redirect_uri}")
    return redirect_uri


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    Config.validate()

    # --- Init Cache ---
    cache.init_app(app)

    # --- Init Scheduler ---
    if not scheduler.running:
        scheduler.init_app(app)
        scheduler.start()
        logger.info("Scheduler started.")

    # Schedule bookings + exchange rate refresh every 10 minutes
    if not scheduler.get_job('bookings_refresh'):
        scheduler.add_job(
            id='bookings_refresh',
            func=refresh_bookings_and_rate,
            trigger='interval',
            seconds=Config.DATA_REFRESH_INTERVAL,
            misfire_grace_time=60
        )
        logger.info(f"Scheduled 'bookings_refresh' every {Config.DATA_REFRESH_INTERVAL}s")

    # Schedule open orders refresh every 60 minutes (separate, lighter on SQL Server)
    if not scheduler.get_job('open_orders_refresh'):
        scheduler.add_job(
            id='open_orders_refresh',
            func=refresh_open_orders_scheduled,
            trigger='interval',
            seconds=Config.OPEN_ORDERS_REFRESH_INTERVAL,
            misfire_grace_time=120
        )
        logger.info(f"Scheduled 'open_orders_refresh' every {Config.OPEN_ORDERS_REFRESH_INTERVAL}s")

    # --- Immediate refresh on startup so cache is never empty ---
    with app.app_context():
        logger.info("Running initial data refresh (all sources)...")
        refresh_all_on_startup()

    # Shut down scheduler on exit
    atexit.register(lambda: scheduler.shutdown() if scheduler.running else None)

    # --- Register Blueprints ---
    app.register_blueprint(main_bp)
    app.register_blueprint(sales_bp)

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
            session["user"] = {
                "name": user_claims.get("name"),
                "email": user_claims.get("preferred_username"),
                "oid": user_claims.get("oid"),
                "tid": user_claims.get("tid"),
                "roles": user_claims.get("roles", [])
            }
            session.pop("flow", None)
            logger.info(f"User authenticated: {session['user'].get('email')} with roles {session['user'].get('roles')}")
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