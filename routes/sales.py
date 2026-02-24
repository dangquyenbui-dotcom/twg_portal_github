from flask import Blueprint, render_template, session, redirect, url_for
from services.data_worker import get_bookings_from_cache

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

    # Read from cache — instant, no SQL query
    snapshot, last_updated = get_bookings_from_cache()

    if snapshot is None:
        return render_template(
            'sales/bookings.html',
            user=session["user"],
            error="Unable to load data. Please try again shortly.",
            total_amount=0, total_units=0, total_orders=0,
            total_territories=0, territory_ranking=[],
            order_date=None, last_updated=None
        )

    summary = snapshot["summary"]

    return render_template(
        'sales/bookings.html',
        user=session["user"],
        error=None,
        total_amount=summary["total_amount"],
        total_units=summary["total_units"],
        total_orders=summary["total_orders"],
        total_territories=summary["total_territories"],
        territory_ranking=snapshot["ranking"],
        order_date=summary.get("order_date"),
        last_updated=last_updated
    )