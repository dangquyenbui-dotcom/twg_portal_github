"""
Authentication Decorators

Authorization is based on Entra ID Security Groups (not App Roles).
Users can belong to multiple groups, solving the single-assignment limitation.

Group Object IDs are mapped to internal role names via GROUP_* env vars in .env.
The role hierarchy ensures higher roles automatically grant lower-level access:

  Admin          → Full access to everything (bypasses all checks)
  Sales.Full     → All Sales reports (Bookings, Open Orders, future reports)
  Sales.Viewer   → Sales hub + Daily Bookings only
  Warehouse      → Warehouse hub + all Warehouse reports (future)
  Finance        → Finance hub + all Finance reports (future)
  HR             → HR hub + all HR reports (future)

Hierarchy chain:  Admin > Sales.Full > Sales.Viewer > Sales.Base
                  Admin > Warehouse, Finance, HR

Sales.Base is never assigned directly — it's an internal role that is
automatically implied by Sales.Viewer and Sales.Full so that any Sales
role grants access to the /sales hub page.
"""
from functools import wraps
from flask import session, redirect, url_for, abort

# ── Role hierarchy ──
# Key = role that is needed
# Value = list of higher roles that implicitly grant it
ROLE_HIERARCHY = {
    'Sales.Base':   ['Sales.Viewer', 'Sales.Full'],
    'Sales.Viewer': ['Sales.Full'],
}


def _user_has_role(user_roles, required_role):
    """
    Check if the user has the required role, either directly or via hierarchy.
    Admin always bypasses all checks.
    """
    if not user_roles:
        return False

    # Admin bypasses everything
    if 'Admin' in user_roles:
        return True

    # Direct match
    if required_role in user_roles:
        return True

    # Hierarchy: check if the user has a higher-level role that implies this one
    implied_by = ROLE_HIERARCHY.get(required_role, [])
    for parent_role in implied_by:
        if parent_role in user_roles:
            return True

    return False


def require_role(role_name):
    """
    Decorator to ensure the logged-in user has the required role.
    Roles are derived from Security Group memberships at login time.
    Supports role hierarchy — e.g., Sales.Full implies Sales.Viewer implies Sales.Base.
    Users in the Admin group automatically bypass all checks.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = session.get("user")

            # Not logged in at all
            if not user:
                return redirect(url_for('main.login_page'))

            user_roles = user.get("roles", [])

            # Check role (direct, admin, or via hierarchy)
            if not _user_has_role(user_roles, role_name):
                abort(403, description=f"Access Denied: You do not have permission to view this page.")

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def user_has_role(user, role_name):
    """
    Template-friendly helper to check if a user has a role.
    Use in Jinja2: {% if user_has_role(user, 'Sales.Viewer') %}
    """
    user_roles = (user or {}).get("roles", [])
    return _user_has_role(user_roles, role_name)