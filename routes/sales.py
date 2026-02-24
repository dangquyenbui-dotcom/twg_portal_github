from flask import Blueprint, render_template, session, redirect, url_for
from services.db_service import fetch_daily_bookings

sales_bp = Blueprint('sales', __name__, url_prefix='/sales')


@sales_bp.route('/')
def sales_home():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    return render_template('sales/index.html', user=session["user"])


@sales_bp.route('/bookings')
def bookings():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    data = fetch_daily_bookings()
    error = None

    if data is None:
        error = "Unable to connect to the database. Please try again later."
        data = []

    # Build summary stats
    total_amount = sum(row.get('Booking Amount', 0) or 0 for row in data)
    total_units = sum(row.get('Units Ordered', 0) or 0 for row in data)
    total_orders = len(set(row.get('Sales Order Number', '') for row in data))

    # Territory ranking: aggregate totals and rank
    territory_totals = {}
    for row in data:
        terr = row.get('Territory', 'Unknown')
        territory_totals[terr] = territory_totals.get(terr, 0) + (row.get('Booking Amount', 0) or 0)

    territory_ranking = [
        {"rank": i + 1, "location": name, "total": amount}
        for i, (name, amount) in enumerate(
            sorted(territory_totals.items(), key=lambda x: x[1], reverse=True)
        )
    ]

    return render_template(
        'sales/bookings.html',
        user=session["user"],
        error=error,
        total_amount=total_amount,
        total_units=total_units,
        total_orders=total_orders,
        total_territories=len(territory_ranking),
        territory_ranking=territory_ranking,
        order_date=data[0].get('Order Date') if data else None
    )