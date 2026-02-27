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