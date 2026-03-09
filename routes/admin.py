"""
Admin Blueprint
Routes for administrative functions — dashboard data management, cache control.

All routes require the Admin role.
"""

import logging
from flask import Blueprint, render_template, session, redirect, url_for, request, jsonify

from auth.decorators import require_role
from services.dashboard_data_service import (
    get_frozen_status, download_year_data, delete_frozen_data,
    invalidate_historical_cache, get_available_years,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


@admin_bp.route('/dashboard-data')
@require_role('Admin')
def dashboard_data():
    """Admin page: view and manage frozen dashboard data files."""
    if not session.get("user"):
        return redirect(url_for('main.login_page'))

    statuses = get_frozen_status()
    available_years = get_available_years()

    # Group statuses by year for easier template rendering
    years_data = {}
    for s in statuses:
        yr = s['year']
        if yr not in years_data:
            years_data[yr] = {'year': yr, 'is_current_year': s['is_current_year'], 'regions': {}}
        years_data[yr]['regions'][s['region']] = s

    years_list = sorted(years_data.values(), key=lambda x: x['year'], reverse=True)

    return render_template(
        'admin/dashboard_data.html',
        user=session["user"],
        years=years_list,
    )


@admin_bp.route('/dashboard-data/download', methods=['POST'])
@require_role('Admin')
def dashboard_data_download():
    """
    AJAX: Download (fetch + aggregate + save) data for a specific year + region.
    Expects JSON: {"year": 2025, "region": "US"}
    """
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year')
    region = data.get('region', '').upper()

    if not year or region not in ('US', 'CA'):
        return jsonify({'error': 'Invalid year or region'}), 400

    try:
        result = download_year_data(year=int(year), region=region)
        logger.info(
            f"Admin: Downloaded {region} {year} — "
            f"${result['total_amount']:,}, {result['months']} months, "
            f"{result['file_size']:,} bytes"
        )
        return jsonify({
            'status': 'ok',
            'result': result,
            'message': f"{region} {year} downloaded successfully. "
                       f"${result['total_amount']:,} across {result['months']} months. "
                       f"File size: {result['file_size']:,} bytes.",
        })
    except Exception as e:
        logger.error(f"Admin: Download failed for {region} {year}: {e}")
        return jsonify({'error': str(e)}), 500


@admin_bp.route('/dashboard-data/download-both', methods=['POST'])
@require_role('Admin')
def dashboard_data_download_both():
    """
    AJAX: Download both US and CA for a specific year.
    Expects JSON: {"year": 2025}
    """
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year')

    if not year:
        return jsonify({'error': 'Invalid year'}), 400

    results = []
    errors = []

    for region in ('US', 'CA'):
        try:
            result = download_year_data(year=int(year), region=region)
            results.append(result)
        except Exception as e:
            errors.append(f"{region}: {str(e)}")

    if errors:
        return jsonify({
            'status': 'partial' if results else 'error',
            'results': results,
            'errors': errors,
            'message': f"Completed with errors: {'; '.join(errors)}",
        }), 207 if results else 500

    return jsonify({
        'status': 'ok',
        'results': results,
        'message': f"US + CA {year} downloaded successfully.",
    })


@admin_bp.route('/dashboard-data/delete', methods=['POST'])
@require_role('Admin')
def dashboard_data_delete():
    """
    AJAX: Delete frozen data file for a specific year + region.
    Expects JSON: {"year": 2025, "region": "US"}
    """
    if not session.get("user"):
        return jsonify({'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    year = data.get('year')
    region = data.get('region', '').upper()

    if not year or region not in ('US', 'CA'):
        return jsonify({'error': 'Invalid year or region'}), 400

    deleted = delete_frozen_data(region, int(year))
    invalidate_historical_cache(year=int(year), region=region)

    if deleted:
        return jsonify({'status': 'ok', 'message': f"{region} {year} deleted."})
    else:
        return jsonify({'status': 'ok', 'message': f"{region} {year} was not downloaded."})