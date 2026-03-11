"""
Shared Constants & Helpers
Centralized territory maps, exclusion sets, and utility functions
used by all database query modules.
"""

# ── US Territory mapping (code → display name) ──
TERRITORY_MAP_US = {
    '000': 'LA',
    '001': 'LA',
    '010': 'China',
    '114': 'Seattle',
    '126': 'Denver',
    '204': 'Columbus',
    '206': 'Jacksonville',
    '210': 'Houston',
    '211': 'Dallas',
    '218': 'San Antonio',
    '221': 'Kansas City',
    '302': 'Nashville',
    '305': 'Levittown,PA',
    '307': 'Charlotte',
    '312': 'Atlanta',
    '324': 'Indianapolis',
    '900': 'Central Billing',
}

# ── Canada Territory mapping (code → display name) ──
TERRITORY_MAP_CA = {
    '501': 'Vancouver',
    '502': 'Toronto',
    '503': 'Montreal',
}

# ── Excluded customers (shared by bookings and open orders) ──
BOOKINGS_EXCLUDED_CUSTOMERS = frozenset({
    'W1VAN', 'W1TOR', 'W1MON', 'MISC', 'TWGMARKET', 'EMP-US', 'TEST123'
})

# ── Excluded customers for per-salesman tracker ──
# Only exclude test accounts; warehouse/internal transfers are legitimate for tracker
TRACKER_EXCLUDED_CUSTOMERS = frozenset({'TEST123'})


# ── Product line grouping (plinid → display category) ──
# Maps raw icitem.plinid codes to roll-up categories for charts/reports.
# Any plinid not listed here falls into 'MISCELLANEOUS'.
PRODUCT_LINE_MAP = {
    # ACCE — accessories
    'LUGNUT': 'ACCE', 'METAL': 'ACCE', 'RHI': 'ACCE',
    'AIRSPD': 'ACCE', 'POWER': 'ACCE', 'SPINNE': 'ACCE', 'WCAP': 'ACCE',
    # BA4X4 — body armor / off-road
    'BODAMR': 'BA4X4', 'BODLFT': 'BA4X4',
    # TIRE
    'TIRE': 'TIRE', 'AMP': 'TIRE', 'DURUN': 'TIRE',
    'LAND': 'TIRE', 'TIRC': 'TIRE',
    # TPMS
    'TPMS': 'TPMS', 'MAX': 'TPMS', 'ITM': 'TPMS',
    # TS — Tuff Stuff Overland
    'TS': 'TS',
    # WHEEL
    'AT': 'WHEEL', 'CALI': 'WHEEL', 'DL': 'WHEEL',
    'ION': 'WHEEL', 'IONB': 'WHEEL', 'IONT': 'WHEEL',
    'KRAZE': 'WHEEL', 'MAYHEM': 'WHEEL', 'MAZZI': 'WHEEL',
    'OE': 'WHEEL', 'RIDLER': 'WHEEL', 'TOUREN': 'WHEEL',
    'SF': 'WHEEL', 'STEEL': 'WHEEL',
    'AKITA': 'WHEEL', 'BACCAR': 'WHEEL', 'CRAGAR': 'WHEEL',
    'DETROI': 'WHEEL', 'DIP': 'WHEEL', 'IONF': 'WHEEL',
    'MASINI': 'WHEEL', 'MPW': 'WHEEL', 'SACCHI': 'WHEEL',
    'VELOCH': 'WHEEL',
    # MISCELLANEOUS — explicit misc codes
    'DEFECT': 'MISCELLANEOUS', 'FRT': 'MISCELLANEOUS',
    'TAX': 'MISCELLANEOUS', 'XMC': 'MISCELLANEOUS',
}


def map_product_line(raw_plinid):
    """Map a raw plinid code to its roll-up display category.
    Falls back to MISCELLANEOUS for unknown codes."""
    code = (raw_plinid or '').strip().upper()
    if not code:
        return 'MISCELLANEOUS'
    return PRODUCT_LINE_MAP.get(code, 'MISCELLANEOUS')


def map_territory(code, region='US'):
    """Map a territory code to its display name."""
    code = (code or '').strip()
    if region == 'CA':
        return TERRITORY_MAP_CA.get(code, 'Others')
    return TERRITORY_MAP_US.get(code, 'Others')


def resolve_territory_code(cu_terr, sm_terr):
    """
    Resolve which territory code to use.
    If customer territory is '900' (Central Billing), use it.
    Otherwise, use the sales order master territory.
    """
    cu = (cu_terr or '').strip()
    if cu == '900':
        return cu
    return (sm_terr or '').strip()