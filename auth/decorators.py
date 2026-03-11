"""
Authentication Decorators

Authorization is based on Entra ID Security Groups mapped to internal roles.
Users can belong to multiple groups = multiple roles (no single-assignment limit).

Role naming convention:
  Admin                          → Full access to everything (bypasses all checks)
  Sales.Bookings.View            → View Daily Bookings dashboard
  Sales.Bookings.Export          → Download Bookings Excel (requires View to be useful)
  Sales.BookingsSummary.View     → View Bookings Summary (MTD/QTD/YTD)
  Sales.BookingsSummary.Export   → Download Bookings Summary Excel
  Sales.Shipments.View           → View Daily Shipments dashboard
  Sales.Shipments.Export         → Download Shipments Excel
  Sales.ShipmentsSummary.View   → View Shipments Summary (MTD/QTD/YTD)
  Sales.ShipmentsSummary.Export → Download Shipments Summary Excel
  Sales.OpenOrders.View          → View Open Orders dashboard
  Sales.OpenOrders.Export        → Download Open Orders Excel (requires View)
  Sales.Dashboard.View           → View Executive Dashboard
  (pattern continues for future reports)

Hierarchy:
  Admin          → bypasses all checks (view + export for everything)
  Sales.*.View   → any Sales view role automatically implies Sales.Base
  Sales.*.Export → does NOT grant view access, only enables download buttons

Sales.Base is never assigned directly — it is an internal role that is
automatically implied by ANY Sales.*.View role so that any Sales user
can access the /sales hub page.
"""
from functools import wraps
from flask import session, redirect, url_for, abort


# ── Role hierarchy ──
# Key = role that is needed
# Value = list of roles that implicitly grant it
#
# Sales.Base is implied by ANY Sales.*.View role.
# This list is maintained here so new reports just need to be added.
ROLE_HIERARCHY = {
    'Sales.Base': [
        'Sales.Bookings.View',
        'Sales.BookingsSummary.View',
        'Sales.Shipments.View',
        'Sales.ShipmentsSummary.View',
        'Sales.OpenOrders.View',
        'Sales.Dashboard.View',
        # Future: add new Sales.*.View roles here
        # 'Sales.TerrPerf.View',
    ],
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
    Supports role hierarchy — e.g., any Sales.*.View implies Sales.Base.
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
    Use in Jinja2: {% if user_has_role(user, 'Sales.Bookings.View') %}
    """
    user_roles = (user or {}).get("roles", [])
    return _user_has_role(user_roles, role_name)