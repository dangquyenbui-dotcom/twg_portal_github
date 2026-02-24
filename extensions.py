"""
Extensions Module
Initializes shared extensions to avoid circular imports.
"""
from flask_caching import Cache
from flask_apscheduler import APScheduler

cache = Cache()
scheduler = APScheduler()