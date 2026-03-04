"""
Authentication Decorators
"""
from functools import wraps
from flask import session, redirect, url_for, abort

def require_role(role_name):
    """
    Decorator to ensure the logged-in user has a specific Entra ID App Role.
    Users with the 'Admin' role automatically bypass this check.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = session.get("user")
            
            # Not logged in at all
            if not user:
                return redirect(url_for('main.login_page'))
            
            user_roles = user.get("roles", [])
            
            # Admin bypasses all role checks
            if "Admin" in user_roles:
                return f(*args, **kwargs)
                
            # Check for the specific required role
            if role_name not in user_roles:
                abort(403, description=f"Access Denied: You need the '{role_name}' role to view this page.")
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator