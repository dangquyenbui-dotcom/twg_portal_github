from flask import Blueprint, render_template, session, redirect, url_for

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    # Gatekeeper: If no user in session, force login
    if not session.get("user"):
        return redirect(url_for('main.login_page'))
    
    return render_template('index.html', user=session["user"])

@main_bp.route('/login_page')
def login_page():
    # If already logged in, go to index
    if session.get("user"):
        return redirect(url_for('main.index'))
    return render_template('login.html')