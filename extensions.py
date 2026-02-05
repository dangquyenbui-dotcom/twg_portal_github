"""
Extensions Module
Initializes shared extensions like Cache and Scheduler to avoid circular imports.
"""
from flask_caching import Cache
from flask_apscheduler import APScheduler

# Initialize Cache (Simple memory cache for now, scalable to Redis later)
cache = Cache()

# Initialize Scheduler (Runs background tasks)
scheduler = APScheduler()