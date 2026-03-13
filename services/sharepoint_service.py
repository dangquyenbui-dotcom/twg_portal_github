"""
SharePoint Service
Reads files from SharePoint sites via Microsoft Graph API.

Uses the same CLIENT_ID / CLIENT_SECRET / TENANT_ID already configured
for SSO authentication. Requires 'Sites.Read.All' application permission
granted via Azure Portal → App Registrations → API Permissions.

Usage:
    from services.sharepoint_service import test_sharepoint_access

    result = test_sharepoint_access()
    # {'success': True, 'site_name': '...', 'file_found': True, 'file_name': '...', ...}
"""

import logging
from io import BytesIO

import requests
from config import Config

logger = logging.getLogger(__name__)

# ── Module-level cache for site ID (doesn't change) ──
_cached_site_id = None
_cached_drive_id = None


def _get_token():
    """Get a Graph API access token via the graph_mail_service module."""
    from services.graph_mail_service import _get_graph_token
    return _get_graph_token()


def get_sharepoint_site_id():
    """
    Look up the SharePoint site ID for the configured site name.
    Caches the result in a module-level variable (site ID never changes).

    Returns:
        Site ID string, or None on failure.
    """
    global _cached_site_id

    if _cached_site_id:
        return _cached_site_id

    site_name = Config.SHAREPOINT_SITE_NAME
    if not site_name:
        logger.warning("SharePoint: SHAREPOINT_SITE_NAME not configured.")
        return None

    token = _get_token()
    if not token:
        logger.error("SharePoint: Could not acquire access token.")
        return None

    # Try the site path lookup (direct path)
    url = f"{Config.GRAPH_API_BASE}/sites/thewheelgroup.sharepoint.com:/sites/{site_name}"

    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )

        if resp.status_code == 200:
            data = resp.json()
            _cached_site_id = data.get('id')
            logger.info(f"SharePoint: Found site '{site_name}' → ID: {_cached_site_id}")
            return _cached_site_id

        logger.warning(f"SharePoint: Direct path lookup failed (HTTP {resp.status_code}). Trying search...")

    except Exception as e:
        logger.warning(f"SharePoint: Direct path lookup error ({e}). Trying search...")

    # Fallback: Search across all sites by display name
    try:
        search_url = f"{Config.GRAPH_API_BASE}/sites?search={site_name}"
        resp = requests.get(
            search_url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )

        if resp.status_code == 200:
            sites = resp.json().get('value', [])
            if sites:
                # Log all found sites so the admin can see the correct name
                for s in sites:
                    logger.info(
                        f"SharePoint: Search found site: "
                        f"name='{s.get('displayName')}' "
                        f"webUrl='{s.get('webUrl')}' "
                        f"id='{s.get('id')}'"
                    )
                # Use the first match
                _cached_site_id = sites[0].get('id')
                logger.info(f"SharePoint: Using first search result → ID: {_cached_site_id}")
                return _cached_site_id
            else:
                logger.error(f"SharePoint: Search returned 0 results for '{site_name}'.")
        else:
            logger.error(f"SharePoint: Search failed — HTTP {resp.status_code}: {resp.text[:300]}")

    except Exception as e:
        logger.error(f"SharePoint: Search error — {e}")

    return None


def _get_drive_id():
    """Get the default document library (drive) ID for the SharePoint site."""
    global _cached_drive_id

    if _cached_drive_id:
        return _cached_drive_id

    site_id = get_sharepoint_site_id()
    if not site_id:
        return None

    token = _get_token()
    if not token:
        return None

    url = f"{Config.GRAPH_API_BASE}/sites/{site_id}/drive"

    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )

        if resp.status_code == 200:
            _cached_drive_id = resp.json().get('id')
            logger.info(f"SharePoint: Drive ID: {_cached_drive_id}")
            return _cached_drive_id

        logger.error(f"SharePoint: Drive lookup failed — HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    except Exception as e:
        logger.error(f"SharePoint: Drive lookup error — {e}")
        return None


def list_drive_files(folder_path=''):
    """
    List files in the SharePoint site's default document library.

    Args:
        folder_path: Subfolder path (empty string = root).

    Returns:
        List of dicts: [{'name': ..., 'id': ..., 'size': ..., 'modified': ...}, ...]
        or empty list on failure.
    """
    site_id = get_sharepoint_site_id()
    if not site_id:
        return []

    token = _get_token()
    if not token:
        return []

    if folder_path:
        url = f"{Config.GRAPH_API_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children"
    else:
        url = f"{Config.GRAPH_API_BASE}/sites/{site_id}/drive/root/children"

    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )

        if resp.status_code == 200:
            items = resp.json().get('value', [])
            files = []
            for item in items:
                files.append({
                    'name': item.get('name', ''),
                    'id': item.get('id', ''),
                    'size': item.get('size', 0),
                    'modified': item.get('lastModifiedDateTime', ''),
                    'is_folder': 'folder' in item,
                })
            return files

        logger.error(f"SharePoint: List files failed — HTTP {resp.status_code}: {resp.text[:300]}")
        return []

    except Exception as e:
        logger.error(f"SharePoint: List files error — {e}")
        return []


def _search_for_file(file_name):
    """
    Search the SharePoint site's document library for a file by name.

    Returns:
        Dict with file info if found, None otherwise.
    """
    site_id = get_sharepoint_site_id()
    if not site_id:
        return None

    token = _get_token()
    if not token:
        return None

    # Use Graph search endpoint
    url = f"{Config.GRAPH_API_BASE}/sites/{site_id}/drive/root/search(q='{file_name}')"

    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=30,
        )

        if resp.status_code == 200:
            items = resp.json().get('value', [])
            # Find exact or close match
            for item in items:
                if item.get('name', '').lower() == file_name.lower():
                    return {
                        'name': item.get('name'),
                        'id': item.get('id'),
                        'size': item.get('size', 0),
                        'modified': item.get('lastModifiedDateTime', ''),
                        'web_url': item.get('webUrl', ''),
                    }
            # If no exact match, return first result if any
            if items:
                item = items[0]
                return {
                    'name': item.get('name'),
                    'id': item.get('id'),
                    'size': item.get('size', 0),
                    'modified': item.get('lastModifiedDateTime', ''),
                    'web_url': item.get('webUrl', ''),
                }
            return None

        logger.error(f"SharePoint: Search failed — HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    except Exception as e:
        logger.error(f"SharePoint: Search error — {e}")
        return None


def test_sharepoint_access():
    """
    Test SharePoint access: verify site exists and search for the target file.
    Does NOT download the file — just verifies access.
    Resets cached IDs so each test is a fresh lookup.

    Returns:
        Dict with test results: {
            'success': bool,
            'site_name': str,
            'site_found': bool,
            'file_found': bool,
            'file_name': str or None,
            'file_size': int or None,
            'file_modified': str or None,
            'file_count': int (total files in root),
            'error': str or None,
        }
    """
    result = {
        'success': False,
        'site_name': Config.SHAREPOINT_SITE_NAME,
        'site_found': False,
        'file_found': False,
        'file_name': None,
        'file_size': None,
        'file_modified': None,
        'file_count': 0,
        'error': None,
    }

    try:
        # Reset cached IDs so test always does a fresh lookup
        global _cached_site_id, _cached_drive_id
        _cached_site_id = None
        _cached_drive_id = None

        # Step 1: Check site access
        site_id = get_sharepoint_site_id()
        if not site_id:
            result['error'] = 'Could not access SharePoint site. Check SHAREPOINT_SITE_NAME and Sites.Read.All permission.'
            return result

        result['site_found'] = True

        # Step 2: List files in root to show connectivity
        root_files = list_drive_files()
        result['file_count'] = len(root_files)

        # Step 3: Search for target file
        target = 'TWG - April 2025 LE.xlsx'
        file_info = _search_for_file(target)

        if file_info:
            result['file_found'] = True
            result['file_name'] = file_info['name']
            result['file_size'] = file_info['size']
            result['file_modified'] = file_info.get('modified', '')
            result['success'] = True
        else:
            result['success'] = True  # Site access works even if file not found
            result['error'] = f'Site accessible but file "{target}" not found in search results.'

        return result

    except Exception as e:
        result['error'] = str(e)
        return result


def read_excel_from_sharepoint(file_name):
    """
    Download and parse an Excel file from SharePoint.
    Returns an openpyxl Workbook object, or None on failure.

    This function is for future use — not wired into any route yet.
    """
    file_info = _search_for_file(file_name)
    if not file_info:
        logger.error(f"SharePoint: File '{file_name}' not found.")
        return None

    site_id = get_sharepoint_site_id()
    token = _get_token()
    if not site_id or not token:
        return None

    # Download file content
    url = f"{Config.GRAPH_API_BASE}/sites/{site_id}/drive/items/{file_info['id']}/content"

    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {token}'},
            timeout=60,
        )

        if resp.status_code == 200:
            from openpyxl import load_workbook
            wb = load_workbook(BytesIO(resp.content), read_only=True, data_only=True)
            logger.info(f"SharePoint: Loaded '{file_name}' ({len(resp.content):,} bytes)")
            return wb

        logger.error(f"SharePoint: Download failed — HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    except Exception as e:
        logger.error(f"SharePoint: Download error for '{file_name}': {e}")
        return None
