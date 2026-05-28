"""
merge_new_associations.py

Adds newly-caught block associations (assn, assoc, BA-acronym, etc.) to the
existing GeoJSON without re-geocoding what's already there.

Steps:
  1. Load existing GeoJSON → location-keyed lookup
  2. Read NYC_Block_Associations3.csv
     a. First pass  — tag locations where any event matches ASSOC_RE or ASSOC_KEYWORDS
     b. Second pass — collect ALL events + names at those locations
  3. Merge year / name data into existing features
  4. Geocode locations NOT already in the GeoJSON
  5. Save updated GeoJSON
"""

import csv, json, re, time
from collections import defaultdict
from datetime import datetime

import requests

# ── Paths ────────────────────────────────────────────────────────────────────

INPUT_CSV       = "/Users/delaneyconnor/Downloads/NYC_Block_Associations3.csv"
EXISTING_GEOJSON = (
    "/Users/delaneyconnor/Desktop/GRAD SCHOO/Year 2/Semester 1"
    "/Methods 3/VS Code/CB303 Block Associations/data/active_block_associations.geojson"
)
OUTPUT_GEOJSON  = EXISTING_GEOJSON   # overwrite in place

# ── API ───────────────────────────────────────────────────────────────────────

SUBSCRIPTION_KEY    = "cbb1cf4e14f74da19eaa4010a2bf64d9"
GEOCLIENT_BASE_URL  = "https://api.nyc.gov/geoclient/v2"

# ── Year range ────────────────────────────────────────────────────────────────

ALL_YEARS   = list(range(2008, 2026))
ACTIVE_CUTOFF = 2022   # last_year >= this → status = "active"

# ── Association detection (mirrors ASSOC_RE in geocoder + HTML keywords) ─────

ASSOC_RE = re.compile(
    r'\bassociation\b'
    r'|\bassoc\.?\b'
    r'|\bassn\.?\b'
    r'|\bassocation\b'
    r'|\bassosciation\b'
    r'|\bassaction\b'
    r'|\bba\b'
    r'|\b\w{2,}ba\b',
    re.IGNORECASE
)

# Broader keyword list matching ASSOC_REQUIRED in the HTML client filter
HTML_KEYWORDS = re.compile(
    r'\b(association|assoc|block\s+club|neighbors?|neighbours?|tenants?|residents|'
    r'houses|council|coalition|civic|committee|co-op|cooperative|alliance|union)\b',
    re.IGNORECASE
)

def is_association(name: str) -> tuple[bool, str]:
    """Return (True, reason) if name suggests a block association."""
    if ASSOC_RE.search(name):
        m = ASSOC_RE.search(name)
        tok = m.group(0).lower()
        if 'association' in tok:   reason = 'keyword:association'
        elif 'assoc' in tok:       reason = 'keyword:assoc'
        elif 'assn' in tok:        reason = 'keyword:assn'
        elif tok == 'ba':          reason = 'pattern:ba_standalone'
        else:                      reason = 'pattern:ba_acronym'
        return True, reason
    kw = HTML_KEYWORDS.search(name)
    if kw:
        return True, f'keyword:{kw.group(1).lower()}'
    return False, ''

# ── Street normalisation (shared with existing scripts) ──────────────────────

def clean_street_prefix(name: str) -> str:
    name = name.strip()
    if re.match(r'^\d+\s*(ST|STREET|AVE|AVENUE|RD|ROAD|PL|PLACE|BLVD|BOULEVARD|'
                r'DR|DRIVE|PKWY|PARKWAY|LANE|LN|CT|COURT|TER|TERRACE)\b', name, re.I):
        return name
    if re.match(r'^\d+(ST|ND|RD|TH)\s', name, re.I):
        return name
    return re.sub(r'^\d+[-]?[A-Z]?\s+(?=\d|\w{2,})', '', name, flags=re.I).strip()

def normalize_street(name: str) -> str:
    name = name.upper().strip()
    name = re.sub(r'\s+', ' ', name)
    for pat, rep in [
        (r'\bAVENUE\b','AVE'), (r'\bSTREET\b','ST'), (r'\bBOULEVARD\b','BLVD'),
        (r'\bDRIVE\b','DR'),   (r'\bPLACE\b','PL'),  (r'\bROAD\b','RD'),
        (r'\bPARKWAY\b','PKWY'),(r'\bEAST\b','E'),   (r'\bWEST\b','W'),
        (r'\bNORTH\b','N'),    (r'\bSOUTH\b','S'),
    ]:
        name = re.sub(pat, rep, name)
    return name

def location_key(loc_text: str, borough: str):
    if not loc_text or 'between' not in loc_text.lower():
        return None
    loc_text = loc_text.split(',')[0].strip()
    m = re.match(r'(.+?)\s+between\s+(.+?)\s+and\s+(.+)', loc_text, re.I)
    if not m:
        return None
    on  = normalize_street(clean_street_prefix(m.group(1).strip()))
    c1  = normalize_street(clean_street_prefix(m.group(2).strip()))
    c2  = normalize_street(clean_street_prefix(m.group(3).strip()))
    return (on, *sorted([c1, c2]), borough.upper())

# ── Geocoding ─────────────────────────────────────────────────────────────────

def geocode(on, c1, c2, borough):
    params = {
        'onStreet': normalize_street(on),
        'crossStreetOne': normalize_street(c1),
        'crossStreetTwo': normalize_street(c2),
        'borough': borough,
    }
    try:
        r = requests.get(
            f"{GEOCLIENT_BASE_URL}/blockface.json", params=params,
            headers={'Ocp-Apim-Subscription-Key': SUBSCRIPTION_KEY, 'Cache-Control': 'no-cache'},
            timeout=30
        )
        r.raise_for_status()
        bf = r.json().get('blockface', {})
        if bf.get('geosupportReturnCode', '') not in ('00', '01'):
            return None
        fl, flon = bf.get('latitudeOfFromIntersection'), bf.get('longitudeOfFromIntersection')
        tl, tlon = bf.get('latitudeOfToIntersection'),  bf.get('longitudeOfToIntersection')
        if fl and flon and tl and tlon:
            return {'lat': (float(fl)+float(tl))/2, 'lon': (float(flon)+float(tlon))/2}
        if fl and flon:
            return {'lat': float(fl), 'lon': float(flon)}
    except Exception:
        pass
    return None

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_year(ds: str):
    m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', ds)
    if m: return int(m.group(3))
    m = re.search(r'(\d{4})-\d{2}-\d{2}', ds)
    if m: return int(m.group(1))
    return None

def best_name(names: set) -> str:
    """Pick the most descriptive association name from a set of event names."""
    with_assoc = [n for n in names if ASSOC_RE.search(n)]
    pool = with_assoc if with_assoc else list(names)
    return max(pool, key=len) if pool else ''

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Merge New Block Associations")
    print("=" * 60)

    # 1. Load existing GeoJSON
    print("\nLoading existing GeoJSON…")
    with open(EXISTING_GEOJSON) as f:
        gj = json.load(f)
    existing_features = gj['features']

    existing_by_key = {}
    for feat in existing_features:
        p = feat['properties']
        k = location_key(p.get('event_location',''), p.get('event_borough',''))
        if k:
            existing_by_key[k] = feat
    print(f"  Existing locations: {len(existing_by_key)}")

    # 2. Read CSV
    print(f"\nReading {INPUT_CSV}…")
    all_events   = []
    assoc_keys   = set()   # location keys that triggered a match
    seen_ids     = set()

    with open(INPUT_CSV, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            etype  = row.get('Event Type','').lower()
            ename  = row.get('Event Name','').strip()
            eid    = row.get('Event ID','')
            loc    = row.get('Event Location','')
            boro   = row.get('Event Borough','')

            is_bp  = etype == 'block party' or 'block party' in ename.lower()
            if not is_bp:
                continue
            if 'between' not in loc.lower():
                continue
            if eid in seen_ids:
                continue
            seen_ids.add(eid)

            k = location_key(loc, boro)
            if not k:
                continue

            hit, reason = is_association(ename)
            if hit:
                assoc_keys.add(k)

            all_events.append((row, k))

    print(f"  Unique block-party events: {len(all_events)}")
    print(f"  Association locations found: {len(assoc_keys)}")

    # 3. Aggregate ALL events at association locations
    locs = defaultdict(lambda: {
        'all_names': set(),
        'flag_reasons': set(),
        'years': set(),
        'latest_date': '',
        'latest_event': None,
        'cb': '',
        'precinct': '',
        'n_events': 0,
    })

    for row, k in all_events:
        if k not in assoc_keys:
            continue
        ename = row.get('Event Name','').strip()
        ds    = row.get('Start Date/Time','')
        yr    = extract_year(ds)
        hit, reason = is_association(ename)

        locs[k]['all_names'].add(ename)
        locs[k]['n_events'] += 1
        if yr:
            locs[k]['years'].add(yr)
        if hit:
            locs[k]['flag_reasons'].add(reason)
        if ds > locs[k]['latest_date']:
            locs[k]['latest_date'] = ds
            locs[k]['latest_event'] = row
            locs[k]['cb']           = row.get('Community Board','').strip().rstrip(',')
            locs[k]['precinct']     = row.get('Police Precinct','').strip().rstrip(',')

    # 4. Update years/names for locations already in GeoJSON
    updated = 0
    for k, data in locs.items():
        if k not in existing_by_key:
            continue
        p = existing_by_key[k]['properties']

        existing_years  = set(p.get('years_active', []))
        existing_names  = set(p.get('all_event_names', []))
        existing_flags  = set(p.get('flag_reasons', []))

        new_years = existing_years | data['years']
        new_names = existing_names | data['all_names']
        new_flags = existing_flags | data['flag_reasons']

        if new_years != existing_years or new_names != existing_names:
            p['years_active']        = sorted(new_years)
            p['years_inactive']      = [y for y in ALL_YEARS if y not in new_years]
            p['years_active_count']  = len(new_years)
            p['first_year']          = min(new_years) if new_years else p.get('first_year')
            p['last_year']           = max(new_years) if new_years else p.get('last_year')
            p['all_event_names']     = sorted(new_names)
            p['all_event_names_count'] = len(new_names)
            p['flag_reasons']        = sorted(new_flags)
            p['status']              = 'active' if (p.get('last_year') or 0) >= ACTIVE_CUTOFF else 'inactive'
            updated += 1

    print(f"  Updated {updated} existing locations with new year/name data")

    # 5. Geocode new locations
    new_keys = [k for k in assoc_keys if k not in existing_by_key]
    print(f"\nGeocoding {len(new_keys)} new locations…")

    new_features = []
    success = fail = 0
    cache = {}

    for i, k in enumerate(new_keys):
        on, c1, c2, boro = k
        data = locs[k]

        if (i + 1) % 25 == 0:
            print(f"  Progress: {i+1}/{len(new_keys)}")

        result = cache.get(k) or geocode(on, c1, c2, boro)
        if result:
            cache[k] = result
        time.sleep(0.1)

        if not result:
            fail += 1
            continue

        years  = sorted(data['years'])
        names  = data['all_names']
        event  = data['latest_event']
        loc_display = event.get('Event Location','').split(',')[0].strip()

        feat = {
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [result['lon'], result['lat']]},
            'properties': {
                'association_name':      best_name(names),
                'all_event_names':       sorted(names),
                'all_event_names_count': len(names),
                'event_borough':         boro.title(),
                'event_location':        loc_display,
                'community_board':       data['cb'],
                'police_precinct':       data['precinct'],
                'last_event_date':       data['latest_date'],
                'total_block_parties':   data['n_events'],
                'years_active':          years,
                'years_inactive':        [y for y in ALL_YEARS if y not in years],
                'first_year':            min(years) if years else None,
                'last_year':             max(years) if years else None,
                'years_active_count':    len(years),
                'status':                'active' if (max(years) if years else 0) >= ACTIVE_CUTOFF else 'inactive',
                'flag_reasons':          sorted(data['flag_reasons']),
            }
        }
        new_features.append(feat)
        success += 1

    print(f"  Geocoded: {success} succeeded, {fail} failed")

    # 6. Save
    gj['features'] = existing_features + new_features
    gj['metadata'] = {
        'generated':    datetime.now().isoformat(),
        'total_locations': len(gj['features']),
        'years_range':  f"{min(ALL_YEARS)}-{max(ALL_YEARS)}",
        'aggregation':  'by_location',
        'source':       'NYC Open Data - Permitted Event Information',
        'note':         'Expanded association detection: assn, assoc, BA acronyms, and keywords',
    }

    with open(OUTPUT_GEOJSON, 'w') as f:
        json.dump(gj, f, indent=2)

    print(f"\nSaved to {OUTPUT_GEOJSON}")
    print(f"Total locations: {len(gj['features'])} "
          f"({len(existing_features)} existing + {len(new_features)} new)")

    # Borough breakdown
    from collections import Counter
    boro_counts = Counter(f['properties']['event_borough'] for f in gj['features'])
    print("\nBorough breakdown:")
    for b, c in sorted(boro_counts.items(), key=lambda x: -x[1]):
        print(f"  {b}: {c}")


if __name__ == "__main__":
    main()
