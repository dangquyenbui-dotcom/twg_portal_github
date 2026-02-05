from flask import Blueprint, render_template, session, redirect, url_for
from extensions import cache
from services.data_worker import refresh_sales_cache

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    return render_template('index.html', user=session["user"])

@main_bp.route('/dashboard')
def dashboard():
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
        
    # 1. Try to get data from Cache
    sales_data = cache.get("sales_dashboard_data")
    
    # 2. Fail-safe: If cache is empty (first run), fetch it now
    if sales_data is None:
        print("⚠️ Cache miss! Fetching data synchronously...")
        refresh_sales_cache() # Run the worker logic manually once
        sales_data = cache.get("sales_dashboard_data")
        
    return render_template('dashboard.html', user=session["user"], sales=sales_data)

@main_bp.route('/login_page')
def login_page():
    if session.get("user"):
        return redirect(url_for('main.index'))
    return render_template('login.html')