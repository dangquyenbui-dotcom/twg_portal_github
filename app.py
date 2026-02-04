"""
Production Portal - Main Application
Cloud-ready configuration with Microsoft Entra ID SSO
"""

import logging
from flask import Flask, session, redirect, url_for, request, render_template
from flask_session import Session
from config import Config
import auth.entra_auth as auth_utils
from routes.main import main_bp

# Logging configuration
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize Server-side Sessions
    Session(app)

    # Validate Config
    Config.validate()

    # Register Blueprints
    app.register_blueprint(main_bp)

    # --- SSO ROUTES ---
    
    @app.route("/login")
    def login():
        try:
            # 1. Initiate the auth code flow and store in session
            # This generates the PKCE 'code_verifier' which is saved in the session
            session["flow"] = auth_utils._build_msal_app().initiate_auth_code_flow(
                Config.SCOPE,
                redirect_uri='http://localhost:5000' + Config.REDIRECT_PATH
            )
            logger.debug(f"Auth flow initiated. Redirecting to Microsoft...")
            return redirect(session["flow"]["auth_uri"])
        except Exception as e:
            logger.error(f"Login initiation failed: {e}")
            return f"Error: {str(e)}"

    @app.route("/auth/redirect")
    def authorized():
        # 2. Retrieve the flow from the session
        flow = session.get("flow")
        if not flow:
            logger.warning("No flow found in session. Session may have expired or cookies are missing.")
            return redirect(url_for("main.login_page"))

        try:
            # 3. Exchange code for token using the flow
            # We convert request.args to a dict to ensure compatibility
            result = auth_utils.get_token_from_code(
                auth_response=request.args.to_dict(),
                auth_code_flow=flow
            )
            
            if "error" in result:
                logger.error(f"Authentication Error: {result.get('error_description')}")
                return render_template("login.html", error=result.get("error_description"))

            # 4. Success: Set user session
            user_claims = result.get("id_token_claims")
            session["user"] = {
                "name": user_claims.get("name"),
                "email": user_claims.get("preferred_username"),
                "oid": user_claims.get("oid"),
                "tid": user_claims.get("tid")
            }
            
            logger.info(f"User {session['user']['email']} logged in successfully.")
            
            # Clear the flow to keep session clean
            session.pop("flow", None)
            return redirect(url_for("main.index"))
            
        except Exception as e:
            logger.exception("Auth route crashed")
            return f"Authentication Error: {str(e)}"

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