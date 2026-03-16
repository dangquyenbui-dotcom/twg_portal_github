# Plan: Region Goal Card + Cumulative Chart Update

## Changes Overview

### 1. Add Territory-to-Region Mapping (constants.py)
Add a `TERRITORY_TO_REGION` dict mapping portal territory display names → region keys:
```
TERRITORY_TO_REGION = {
    # WEST
    'Denver': 'WEST', 'LA': 'WEST', 'Seattle': 'WEST',
    # SOUTHEAST
    'Atlanta': 'SOUTHEAST', 'Charlotte': 'SOUTHEAST', 'Jacksonville': 'SOUTHEAST', 'Nashville': 'SOUTHEAST',
    # MIDWEST
    'Dallas': 'MIDWEST', 'Houston': 'MIDWEST', 'Kansas City': 'MIDWEST', 'San Antonio': 'MIDWEST',
    # NORTHEAST
    'Columbus': 'NORTHEAST', 'Indianapolis': 'NORTHEAST', 'Levittown,PA': 'NORTHEAST',
    # Canada
    'Vancouver': 'CANADA', 'Toronto': 'CANADA', 'Montreal': 'CANADA',
}
```

### 2. Add `get_region_invoiced()` (my_tracker_service.py)
New function that sums invoiced $ for ALL territories in a region. Similar to `get_territory_invoiced()` but collects all territory codes belonging to the region, then runs a single SUM query. Cached 15 min.

### 3. Update Route (routes/sales.py — my_tracker)
After fetching territory goal, also:
- Look up which region the territory belongs to via `TERRITORY_TO_REGION`
- Call `get_region_goal(region_key, year, month)` to get the region goal
- Call `get_region_invoiced(region_key, year, month)` to get region-wide invoiced total
- Pass `region_goal`, `region_name`, `region_invoiced` to the template

### 4. Add Region Goal KPI Card (my_tracker.html)
Add a new KPI card right after the Territory Goal card:
- Color: different shade (blue or teal) to distinguish from purple territory
- Label: "{REGION} Goal" (e.g. "WEST Goal")
- Value: "$X,XXXK"
- Sub-text: "XX.X% — $XXXK invoiced" with same met/close/behind coloring

### 5. Update Cumulative Chart (my_tracker.html)
- Change title: "Cumulative MTD vs Same Month Last Year" → "Cumulative MTD vs Territory & Region Goal"
- Remove the last-year (LY) dataset line
- Keep the current year cumulative line (green)
- Keep territory goal line (purple dashed)
- Add region goal line (new color, dashed) — flat horizontal line at region goal value
- Pass `region_goal` as JS variable

### 6. Remove LY Data Fetch (routes/sales.py)
Since we're removing "same month last year" from the chart, we can stop fetching `ly_data` — BUT keep the `ly_by_day` variable passed to template (just empty) to avoid breaking other references. Actually, let me check if ly_by_day is used elsewhere... if not, we can clean it up.

## Files Modified
1. `services/constants.py` — add TERRITORY_TO_REGION map
2. `services/my_tracker_service.py` — add get_region_invoiced()
3. `routes/sales.py` — fetch region goal + region invoiced, pass to template
4. `templates/sales/my_tracker.html` — add Region Goal card, update cumulative chart
