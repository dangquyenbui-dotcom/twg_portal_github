"""
TWG Portal - Main Application
"""

import logging
from flask import Flask, session, redirect, url_for, request, render_template
from config import Config
import auth.entra_auth as auth_utils
from routes.main import main_bp

# --- Logging: INFO level only ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')

logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('msal').setLevel(logging.WARNING)
logging.getLogger('werkzeug').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    Config.validate()

    app.register_blueprint(main_bp)

    # --- SSO ROUTES ---
    @app.route("/login")
    def login():
        try:
            redirect_uri = request.url_root.rstrip('/') + Config.REDIRECT_PATH

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
                "tid": user_claims.get("tid")
            }
            session.pop("flow", None)
            logger.info(f"User authenticated: {session['user'].get('email')}")
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