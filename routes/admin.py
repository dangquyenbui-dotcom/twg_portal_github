"""
Admin Blueprint
Routes for administrative functions — dashboard data management, cache control.

Supports two data types:
  - Bookings:  sotran/soytrn → bookings_dashboard_data/
  - Shipments: artran/arytrn → shipments_dashboard_data/

Downloads run in background threads. The frontend polls /task-status/<id>
for progress so long-running SQL queries (2-5 min) don't cause HTTP timeouts.

All routes require the Admin role.
"""

import logging
import threading
import uuid
from datetime import datetime

from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify

from auth.decorators import require_role
from services.bookings_dashboard_data_service import (
    get_frozen_status as get_bookings_frozen_status,
    download_year_data as download_bookings_year_data,
    delete_frozen_data as delete_bookings_frozen_data,
    invalidate_historical_cache as invalidate_bookings_cache,
    get_available_years,
)
from services.shipments_dashboard_data_service import (
    get_frozen_status as get_shipments_frozen_status,
    download_year_data as download_shipments_year_data,
    delete_frozen_data as delete_shipments_frozen_data,
    invalidate_historical_cache as invalidate_shipments_cache,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# ── Background task tracking ──
# In-memory dict: task_id → {status, message, started_at, result, error}
# Tasks are cleaned up after 10 minutes.
_tasks = {}
_tasks_lock = threading.Lock()
_TASK_TTL_SECONDS = 600


def _cleanup_old_tasks():
    """Remove tasks older than TTL."""
    now = datetime.now()
    with _tasks_lock:
        expired = [
            tid for tid, t in _tasks.items()
            if (now - t['started_at']).total_seconds() > _TASK_TTL_SECONDS
        ]
        for tid in expired:
            del _tasks[tid]


def _create_task(description):
    """Create a new task entry and return its ID."""
    _cleanup_old_tasks()
    task_id = uuid.uuid4().hex[:12]
    with _tasks_lock:
        _tasks[task_id] = {
            'status': 'running',
            'message': description,
            'started_at': datetime.now(),
            'result': None,
            'error': None,
        }
    return task_id


def _update_task(task_id, **kwargs):
    """Update task fields (message, status, result, error)."""
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


def _run_download_single(task_id, download_fn, year, region, label):
    """Background thread: download a single region."""
    try:
        _update_task(task_id, message=f"Querying SQL Server for {label}...")
        result = download_fn(year=year, region=region)
        msg = (
            f"{label} downloaded successfully. "
            f"${result['total_amount']:,} across {result['months']} months. "
            f"{result['row_count']:,} raw rows. "
            f"{result['file_size']:,} bytes."
        )
        _update_task(task_id, status='completed', message=msg, result=result)
        logger.info(f"Admin task {task_id}: {msg}")
    except Exception as e:
        _update_task(task_id, status='error', message=str(e), error=str(e))
        logger.error(f"Admin task {task_id}: Download failed for {label}: {e}")


def _run_download_both(task_id, download_fn, year, data_type):
    """Background thread: download US then CA for a year."""
    label = f"{data_type.title()} {year}"
    results = []
    errors = []

    for region in ('US', 'CA'):
        region_label = f"{data_type.title()} {region} {year}"
        _update_task(task_id, message=f"Querying SQL Server for {region_label}...")
        try:
            result = download_fn(year=year, region=region)
            results.append(result)
            _update_task(task_id, message=f"{region_label} done. "
                         f"{result['row_count']:,} rows, ${result['total_amount']:,}.")
        except Exception as e:
            errors.append(f"{region}: {str(e)}")

    if errors:
        msg = f"{label} completed with errors: {'; '.join(errors)}"
        _update_task(task_id, status='error' if not results else 'completed',
                     message=msg, error='; '.join(errors), result=results)
    else:
        total_rows = sum(r['row_count'] for r in results)
        total_size = sum(r['file_size'] for r in results)
        msg = (
            f"{label} US + CA downloaded successfully. "
            f"{total_rows:,} total raw rows. "
            f"Total file size: {total_size:,} bytes."
        )
        _update_task(task_id, status='completed', message=msg, result=results)

    logger.info(f"Admin task {task_id}: {msg}")


def _group_statuses_by_year(statuses):
    """Group flat status list into year-keyed dicts for template rendering."""
    years_data = {}
    for s in statuses:
        yr = s['year']
        if yr not in years_data:
            years_data[yr] = {'year': yr, 'is_current_year': s['is_current_year'], 'regions': {}}
        years_data[yr]['regions'][s['region']] = s
    return sorted(years_data.values(), key=lambda x: x['year'], reverse=True)


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

@admin_bp.route('/dashboard-data')
@require_role('Admin')
def dashboard_data():
    """Admin page: view and manage frozen dashboard data files (bookings + shipments)."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    bookings_years = _group_statuses_by_year(get_bookings_frozen_status())
    shipments_years = _group_statuses_by_year(get_shipments_frozen_status())

    return render_template(
        'admin/dashboard_data.html',
        user=session["user"],
        bookings_years=bookings_years,
        shipments_years=shipments_years,
    )


@admin_bp.route('/dashboard-data/download', methods=['POST'])
@require_role('Admin')
def dashboard_data_download():
    """
    AJAX: Start background download for a specific year + region + data_type.
    Returns immediately with a task_id for polling.
    Expects JSON: {"year": 2025, "region": "US", "data_type": "bookings"|"shipments"}
    """
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year')
    region = data.get('region', '').upper()
    data_type = data.get('data_type', 'bookings')

    if not year or region not in ('US', 'CA'):
        return jsonify({'error': 'Invalid year or region'}), 400
    if data_type not in ('bookings', 'shipments'):
        return jsonify({'error': 'Invalid data_type'}), 400

    download_fn = download_bookings_year_data if data_type == 'bookings' else download_shipments_year_data
    label = f"{data_type.title()} {region} {year}"

    task_id = _create_task(f"Starting download for {label}...")
    thread = threading.Thread(
        target=_run_download_single,
        args=(task_id, download_fn, int(year), region, label),
        daemon=True,
    )
    thread.start()

    return jsonify({'status': 'started', 'task_id': task_id})


@admin_bp.route('/dashboard-data/download-both', methods=['POST'])
@require_role('Admin')
def dashboard_data_download_both():
    """
    AJAX: Start background download for both US and CA for a year + data_type.
    Returns immediately with a task_id for polling.
    Expects JSON: {"year": 2025, "data_type": "bookings"|"shipments"}
    """
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year')
    data_type = data.get('data_type', 'bookings')

    if not year:
        return jsonify({'error': 'Invalid year'}), 400
    if data_type not in ('bookings', 'shipments'):
        return jsonify({'error': 'Invalid data_type'}), 400

    download_fn = download_bookings_year_data if data_type == 'bookings' else download_shipments_year_data
    label = f"{data_type.title()} US + CA {year}"

    task_id = _create_task(f"Starting download for {label}...")
    thread = threading.Thread(
        target=_run_download_both,
        args=(task_id, download_fn, int(year), data_type),
        daemon=True,
    )
    thread.start()

    return jsonify({'status': 'started', 'task_id': task_id})


@admin_bp.route('/dashboard-data/task-status/<task_id>')
@require_role('Admin')
def dashboard_data_task_status(task_id):
    """AJAX: Poll for background task progress."""
    with _tasks_lock:
        task = _tasks.get(task_id)

    if not task:
        return jsonify({'status': 'not_found', 'message': 'Task not found'}), 404

    elapsed = (datetime.now() - task['started_at']).total_seconds()
    return jsonify({
        'status': task['status'],
        'message': task['message'],
        'elapsed': round(elapsed),
    })


@admin_bp.route('/dashboard-data/delete', methods=['POST'])
@require_role('Admin')
def dashboard_data_delete():
    """
    AJAX: Delete frozen data file for a specific year + region + data_type.
    Expects JSON: {"year": 2025, "region": "US", "data_type": "bookings"|"shipments"}
    """
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year')
    region = data.get('region', '').upper()
    data_type = data.get('data_type', 'bookings')

    if not year or region not in ('US', 'CA'):
        return jsonify({'error': 'Invalid year or region'}), 400
    if data_type not in ('bookings', 'shipments'):
        return jsonify({'error': 'Invalid data_type'}), 400

    if data_type == 'bookings':
        deleted = delete_bookings_frozen_data(region, int(year))
        invalidate_bookings_cache(year=int(year), region=region)
    else:
        deleted = delete_shipments_frozen_data(region, int(year))
        invalidate_shipments_cache(year=int(year), region=region)

    label = f"{data_type.title()} {region} {year}"
    if deleted:
        return jsonify({'status': 'ok', 'message': f"{label} deleted."})
    else:
        return jsonify({'status': 'ok', 'message': f"{label} was not downloaded."})
