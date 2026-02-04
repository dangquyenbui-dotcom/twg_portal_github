"""
Production Portal - Main Application
Cloud-ready configuration with Microsoft Entra ID SSO
"""

from flask import Flask, session, redirect, url_for, request, render_template
from flask_session import Session
from config import Config
import auth.entra_auth as auth_utils
from routes.main import main_bp

# Import i18n (Optional: keeping your existing structure if you add it later)
# from i18n_config import I18nConfig 

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize Server-side Sessions (Required for MSAL security)
    Session(app)

    # Validate Config
    Config.validate()

    # Register Blueprints
    app.register_blueprint(main_bp)

    # --- SSO ROUTES ---
    
    @app.route("/login")
    def login():
        # 1. Create the detailed auth flow request
        session["flow"] = auth_utils._build_msal_app().initiate_auth_code_flow(
            Config.SCOPE,
            redirect_uri='http://localhost:5000' + Config.REDIRECT_PATH
        )
        # 2. Redirect user to Microsoft
        return redirect(session["flow"]["auth_uri"])

    @app.route("/auth/redirect")  # This must match Azure Portal Redirect URI
    def authorized():
        try:
            # 3. User is back. Exchange code for token.
            result = auth_utils.get_token_from_code(
                request.args['code'],
                auth_code_flow=session.get("flow", {})
            )
            
            if "error" in result:
                return render_template("login.html", error=result.get("error_description"))

            # 4. Login Successful - Extract User Info
            user_claims = result.get("id_token_claims")
            
            session["user"] = {
                "name": user_claims.get("name"),
                "email": user_claims.get("preferred_username"),
                "oid": user_claims.get("oid"), # Unique Object ID
                "tid": user_claims.get("tid")  # Tenant ID
            }
            
            return redirect(url_for("main.index"))
            
        except ValueError:
             return redirect(url_for("main.login_page"))
        except Exception as e:
            print(f"Auth Error: {e}")
            return f"Authentication Error: {str(e)}"

    @app.route("/logout")
    def logout():
        session.clear()
        # Redirect to Microsoft to clear their cookies too
        return redirect(
            f"{Config.AUTHORITY}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri=http://localhost:5000/login_page"
        )

    return app

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🚀 SERVER STARTING (HTTP) - Microsoft Entra ID SSO Mode")
    print(f"   URL: http://localhost:5000")
    print("="*60 + "\n")
    
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)