"""
TWG Portal - Main Application
With Background Caching & Scheduler
"""

import logging
import atexit
from flask import Flask, session, redirect, url_for, request, render_template
from flask_session import Session
from config import Config
from extensions import cache, scheduler
import auth.entra_auth as auth_utils
from routes.main import main_bp
from services.data_worker import refresh_sales_cache # Import the worker task

# Logging configuration
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # 1. Init Standard Extensions
    Session(app)
    Config.validate()
    
    # 2. Init Cache & Scheduler
    cache.init_app(app)
    scheduler.init_app(app)
    scheduler.start()

    # 3. Schedule the Job (Run every 60 seconds)
    # We add a job directly here
    if not scheduler.get_job('sales_refresh_job'):
        scheduler.add_job(
            id='sales_refresh_job',
            func=refresh_sales_cache,
            trigger='interval',
            seconds=60 # Updates cache every 1 minute
        )
        logger.info("🕒 Scheduled 'sales_refresh_job' to run every 60s")

    # 4. Register Routes
    app.register_blueprint(main_bp)

    # Shut down scheduler when app closes
    atexit.register(lambda: scheduler.shutdown())

    # --- SSO ROUTES ---
    @app.route("/login")
    def login():
        try:
            session["flow"] = auth_utils._build_msal_app().initiate_auth_code_flow(
                Config.SCOPE,
                redirect_uri='http://localhost:5000' + Config.REDIRECT_PATH
            )
            return redirect(session["flow"]["auth_uri"])
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return f"Error: {str(e)}"

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
                return render_template("login.html", error=result.get("error_description"))

            user_claims = result.get("id_token_claims")
            session["user"] = {
                "name": user_claims.get("name"),
                "email": user_claims.get("preferred_username"),
                "oid": user_claims.get("oid"),
                "tid": user_claims.get("tid")
            }
            session.pop("flow", None)
            return redirect(url_for("main.index"))
            
        except Exception as e:
            logger.exception("Auth route crashed")
            return f"Auth Error: {str(e)}"

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(
            f"{Config.AUTHORITY}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri=http://localhost:5000/login_page"
        )

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)