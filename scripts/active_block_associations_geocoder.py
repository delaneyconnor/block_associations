"""
Active Block Associations Geocoder

This script:
1. Reads and cleans NYC_Block_Associations.csv
2. Filters for block party events that indicate a block association
3. Aggregates by LOCATION (not association name) to track all years of block parties
4. Geocodes using NYC Geoclient Blockface API
5. Outputs GeoJSON with block party history per location for web mapping
"""

import csv
import requests
import json
import re
import time
from datetime import datetime
from collections import defaultdict

# NYC Geoclient API credentials (same as block_party_geocoder.py)
SUBSCRIPTION_KEY = "cbb1cf4e14f74da19eaa4010a2bf64d9"
GEOCLIENT_BASE_URL = "https://api.nyc.gov/geoclient/v2"

# Input/output files
INPUT_CSV = "/Users/delaneyconnor/Downloads/NYC_Block_Associations3.csv"
OUTPUT_GEOJSON = "active_block_associations.geojson"

# Years range for tracking
ALL_YEARS = list(range(2008, 2026))  # 2008-2025

# Regex to detect block association indicators in event names.
# Catches: full word, abbreviations (assn/assoc), typos (assaction/assocation),
# standalone "BA", and acronyms ending in "BA" (PPUABA, LPBA, SOBA, SABA, etc.)
ASSOC_RE = re.compile(
    r'\bassociation\b'      # full word
    r'|\bassoc\.?\b'        # assoc or assoc.
    r'|\bassn\.?\b'         # assn or assn.
    r'|\bassocation\b'      # common typo
    r'|\bassosciation\b'    # another typo
    r'|\bassaction\b'       # typo seen in data ("ASSACTION")
    r'|\bba\b'              # standalone BA (e.g. "DEAN STREET BA")
    r'|\b\w{2,}ba\b',       # acronyms ending in BA (PPUABA, LPBA, SOBA, CSBA, etc.)
    re.IGNORECASE
)


def clean_street_prefix(street_name):
    """
    Remove leading address numbers like "550-A" from street names.
    e.g., "550-A LEXINGTON AVENUE" -> "LEXINGTON AVENUE"

    But preserve numbered street names like "8 STREET" or "93 AVENUE"
    """
    name = street_name.strip()

    # Pattern for address prefixes: number (optionally with letter/dash) followed by a named street
    # e.g., "550-A LEXINGTON AVENUE" or "26 126 STREET"
    # But NOT "8 STREET" or "93 AVENUE" (where the number IS the street name)

    # Check if this looks like "NUMBER STREET/AVENUE/etc" (a numbered street name)
    # These should NOT be cleaned
    numbered_street_pattern = r'^\d+\s*(ST|STREET|AVE|AVENUE|RD|ROAD|PL|PLACE|BLVD|BOULEVARD|DR|DRIVE|PKWY|PARKWAY|LANE|LN|CT|COURT|TER|TERRACE)\b'
    if re.match(numbered_street_pattern, name, re.IGNORECASE):
        return name

    # Check for ordinal numbered streets like "1ST", "2ND", "93RD", "45TH"
    ordinal_pattern = r'^\d+(ST|ND|RD|TH)\s'
    if re.match(ordinal_pattern, name, re.IGNORECASE):
        return name

    # Remove address prefixes like "550-A ", "26 " when followed by another street name
    # Pattern: digits (optional dash+letter) followed by space and then more content
    cleaned = re.sub(r'^\d+[-]?[A-Z]?\s+(?=\d|\w{2,})', '', name, flags=re.IGNORECASE)
    return cleaned.strip()


def parse_location(location_text):
    """
    Parse location strings like:
    "AMSTERDAM AVENUE between WEST 96 STREET and WEST 97 STREET"
    """
    if not location_text:
        return None

    text = re.sub(r'\s+', ' ', location_text.strip())

    match = re.match(
        r"(.+?)\s+between\s+(.+?)\s+and\s+(.+)",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    # Clean any leading address numbers from street names
    on_street = clean_street_prefix(match.group(1).strip())
    cross_one = clean_street_prefix(match.group(2).strip())
    cross_two = clean_street_prefix(match.group(3).strip())

    return {
        'on_street': on_street,
        'cross_street_one': cross_one,
        'cross_street_two': cross_two
    }


def normalize_street_name(name):
    """
    Normalize street names for better API matching.
    """
    name = name.upper().strip()
    name = re.sub(r'\s+', ' ', name)

    replacements = [
        (r'\bAVENUE\b', 'AVE'),
        (r'\bSTREET\b', 'ST'),
        (r'\bBOULEVARD\b', 'BLVD'),
        (r'\bDRIVE\b', 'DR'),
        (r'\bPLACE\b', 'PL'),
        (r'\bROAD\b', 'RD'),
        (r'\bPARKWAY\b', 'PKWY'),
        (r'\bEAST\b', 'E'),
        (r'\bWEST\b', 'W'),
        (r'\bNORTH\b', 'N'),
        (r'\bSOUTH\b', 'S'),
    ]

    for pattern, replacement in replacements:
        name = re.sub(pattern, replacement, name)

    return name


def normalize_location_key(location_text, borough):
    """
    Create a normalized location key for grouping events by the same block.
    This handles variations in how the same location might be entered.
    """
    if not location_text:
        return None

    # Handle multi-segment locations (take first segment)
    if ',' in location_text:
        location_text = location_text.split(',')[0].strip()

    parsed = parse_location(location_text)
    if not parsed:
        return None

    # Normalize all street names
    on_street = normalize_street_name(parsed['on_street'])
    cross_one = normalize_street_name(parsed['cross_street_one'])
    cross_two = normalize_street_name(parsed['cross_street_two'])

    # Sort cross streets alphabetically for consistency
    # (so "A between B and C" matches "A between C and B")
    cross_streets = sorted([cross_one, cross_two])

    return (on_street, cross_streets[0], cross_streets[1], borough.upper())


def geocode_blockface(on_street, cross_one, cross_two, borough):
    """
    Call NYC Geoclient Blockface API to get coordinates for a street segment.
    """
    on_street = normalize_street_name(on_street)
    cross_one = normalize_street_name(cross_one)
    cross_two = normalize_street_name(cross_two)

    params = {
        'onStreet': on_street,
        'crossStreetOne': cross_one,
        'crossStreetTwo': cross_two,
        'borough': borough
    }

    headers = {
        'Ocp-Apim-Subscription-Key': SUBSCRIPTION_KEY,
        'Cache-Control': 'no-cache'
    }

    url = f"{GEOCLIENT_BASE_URL}/blockface.json"

    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        blockface = data.get('blockface', {})
        return_code = blockface.get('geosupportReturnCode', '')

        if return_code not in ['00', '01']:
            return None

        from_lat = blockface.get('latitudeOfFromIntersection')
        from_lon = blockface.get('longitudeOfFromIntersection')
        to_lat = blockface.get('latitudeOfToIntersection')
        to_lon = blockface.get('longitudeOfToIntersection')

        if from_lat and from_lon and to_lat and to_lon:
            lat = (float(from_lat) + float(to_lat)) / 2
            lon = (float(from_lon) + float(to_lon)) / 2
        elif from_lat and from_lon:
            lat = float(from_lat)
            lon = float(from_lon)
        else:
            return None

        return {
            'latitude': lat,
            'longitude': lon
        }

    except Exception as e:
        return None


def extract_association_name(event_name):
    """
    Extract just the association name from event name.
    """
    match = re.search(r'(.+?association(?:\s+inc\.?)?)', event_name, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return event_name


def extract_year(date_string):
    """
    Extract year from date string like "11/12/2017 09:00:00 AM"
    """
    if not date_string:
        return None

    # Try to parse MM/DD/YYYY format
    match = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', date_string)
    if match:
        return int(match.group(3))

    # Try ISO format
    match = re.search(r'(\d{4})-\d{2}-\d{2}', date_string)
    if match:
        return int(match.group(1))

    return None


def clean_and_aggregate_by_location(input_file):
    """
    Read CSV, filter for block party associations, and aggregate by LOCATION.
    Strategy:
    1. First, identify all locations that have EVER had an "association" event
    2. Then, collect ALL block party events at those locations (regardless of name)
    This ensures we capture all years even when naming varies.

    Returns a dict mapping normalized location -> {events, years, association_names}
    """
    print("Reading and aggregating data by LOCATION...")

    # First pass: identify all locations that have ever had an "association" block party
    association_locations = set()
    all_block_parties = []  # All block parties (we'll filter later)
    seen_event_ids = set()

    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            event_name = row.get('Event Name', '').lower()
            event_type = row.get('Event Type', '').lower()
            event_id = row.get('Event ID', '')
            location = row.get('Event Location', '')
            borough = row.get('Event Borough', '')

            # Filter for Block Party events only
            is_block_party = (
                event_type == 'block party' or
                'block party' in event_name
            )
            if not is_block_party:
                continue

            # Skip if no location with "between" pattern
            if 'between' not in location.lower():
                continue

            # Deduplicate by event_id
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

            # Get location key
            location_key = normalize_location_key(location, borough)
            if not location_key:
                continue

            # Track if this location has an association event
            if ASSOC_RE.search(event_name):
                association_locations.add(location_key)

            all_block_parties.append((row, location_key))

    print(f"  Found {len(all_block_parties)} total block party events")
    print(f"  Found {len(association_locations)} locations with 'association' in event name")

    # Second pass: collect ALL block parties at association locations
    all_events = []
    for row, location_key in all_block_parties:
        if location_key in association_locations:
            all_events.append(row)

    print(f"  Including {len(all_events)} block party events at association locations")

    # Second pass: aggregate by LOCATION (not association name)
    locations = defaultdict(lambda: {
        'events': [],
        'years': set(),
        'association_names': set(),  # Track all names used at this location
        'most_recent_event': None,
        'most_recent_date': ''
    })

    for event in all_events:
        location = event.get('Event Location', '')
        borough = event.get('Event Borough', '')
        date_str = event.get('Start Date/Time', '')
        year = extract_year(date_str)
        assoc_name = extract_association_name(event.get('Event Name', ''))

        # Create normalized location key
        location_key = normalize_location_key(location, borough)
        if not location_key:
            continue

        locations[location_key]['events'].append(event)
        locations[location_key]['association_names'].add(assoc_name)
        if year:
            locations[location_key]['years'].add(year)

        # Track most recent event
        if date_str > locations[location_key]['most_recent_date']:
            locations[location_key]['most_recent_date'] = date_str
            locations[location_key]['most_recent_event'] = event

    print(f"  Aggregated into {len(locations)} unique block locations")

    # Third pass: Share years between adjacent/overlapping segments
    # If two segments share the same main street AND at least one cross street,
    # they are adjacent and should share their year histories
    print("  Merging years for adjacent block segments...")

    # Group locations by main street and borough
    street_groups = defaultdict(list)
    for location_key in locations.keys():
        on_street, cross_one, cross_two, borough = location_key
        street_groups[(on_street, borough)].append(location_key)

    merged_count = 0
    for (on_street, borough), segment_keys in street_groups.items():
        if len(segment_keys) < 2:
            continue

        # For each pair of segments on the same street, check if they share a cross street
        for i, key1 in enumerate(segment_keys):
            for key2 in segment_keys[i+1:]:
                _, cross1_a, cross1_b, _ = key1
                _, cross2_a, cross2_b, _ = key2

                # Check if they share a cross street (meaning they're adjacent)
                crosses1 = {cross1_a, cross1_b}
                crosses2 = {cross2_a, cross2_b}

                if crosses1 & crosses2:  # They share at least one cross street
                    # Merge years from both segments
                    combined_years = locations[key1]['years'] | locations[key2]['years']
                    combined_names = locations[key1]['association_names'] | locations[key2]['association_names']

                    locations[key1]['years'] = combined_years
                    locations[key2]['years'] = combined_years
                    locations[key1]['association_names'] = combined_names
                    locations[key2]['association_names'] = combined_names
                    merged_count += 1

    print(f"  Merged years for {merged_count} adjacent segment pairs")

    return locations


def main():
    print("=" * 60)
    print("Active Block Associations Geocoder")
    print("(Aggregating by LOCATION, not by association name)")
    print("=" * 60)

    # Step 1: Clean and aggregate data by location
    locations = clean_and_aggregate_by_location(INPUT_CSV)

    if not locations:
        print("No locations found!")
        return

    # Step 2: Geocode
    print("\nGeocoding locations...")

    features = []
    geocode_cache = {}
    success_count = 0
    fail_count = 0

    location_list = list(locations.items())

    for i, (location_key, data) in enumerate(location_list):
        on_street, cross_one, cross_two, borough = location_key
        event = data['most_recent_event']
        years = sorted(data['years'])
        assoc_names = data['association_names']

        if (i + 1) % 20 == 0:
            print(f"  Progress: {i+1}/{len(location_list)}")

        # Use the location key for geocoding (already normalized)
        cache_key = location_key

        if cache_key in geocode_cache:
            result = geocode_cache[cache_key]
        else:
            result = geocode_blockface(on_street, cross_one, cross_two, borough)
            if result:
                geocode_cache[cache_key] = result
            time.sleep(0.1)  # Rate limiting

        if result:
            # Pick the best association name:
            # 1. Prefer names with "association" in them
            # 2. Among those, pick the longest one
            assoc_names_with_assoc = [n for n in assoc_names if 'association' in n.lower()]
            if assoc_names_with_assoc:
                best_assoc_name = max(assoc_names_with_assoc, key=len)
            else:
                # Fall back to longest name overall
                best_assoc_name = max(assoc_names, key=len) if assoc_names else extract_association_name(event.get('Event Name', ''))

            # Calculate years active and inactive
            years_active = sorted(years)
            years_inactive = [y for y in ALL_YEARS if y not in years]
            total_parties = len(data['events'])

            # Find first and last year
            first_year = min(years) if years else None
            last_year = max(years) if years else None

            # Format location nicely
            location_display = event.get('Event Location', '')
            if ',' in location_display:
                location_display = location_display.split(',')[0].strip()

            feature = {
                'type': 'Feature',
                'geometry': {
                    'type': 'Point',
                    'coordinates': [result['longitude'], result['latitude']]
                },
                'properties': {
                    'event_id': event.get('Event ID', ''),
                    'association_name': best_assoc_name,
                    'all_association_names': list(assoc_names),  # All names used at this location
                    'event_name': event.get('Event Name', ''),
                    'event_type': event.get('Event Type', ''),
                    'event_borough': borough.title(),  # Capitalize properly
                    'event_location': location_display,
                    'community_board': event.get('Community Board', '').strip().rstrip(','),
                    'police_precinct': event.get('Police Precinct', '').strip().rstrip(','),
                    'last_event_date': data['most_recent_date'],
                    # Block party history (by location)
                    'total_block_parties': total_parties,
                    'years_active': years_active,
                    'years_inactive': years_inactive,
                    'first_year': first_year,
                    'last_year': last_year,
                    'years_active_count': len(years_active),
                    'unique_association_names_count': len(assoc_names)
                }
            }
            features.append(feature)
            success_count += 1
        else:
            fail_count += 1

    # Step 3: Output GeoJSON
    print(f"\nGeocoding complete:")
    print(f"  Success: {success_count}")
    print(f"  Failed: {fail_count}")

    geojson = {
        'type': 'FeatureCollection',
        'features': features,
        'metadata': {
            'generated': datetime.now().isoformat(),
            'total_locations': len(features),
            'years_range': f"{min(ALL_YEARS)}-{max(ALL_YEARS)}",
            'aggregation': 'by_location',
            'source': 'NYC Open Data - Permitted Event Information (filtered for block party associations)'
        }
    }

    with open(OUTPUT_GEOJSON, 'w') as f:
        json.dump(geojson, f, indent=2)

    print(f"\nSaved: {OUTPUT_GEOJSON}")
    print(f"Total block locations mapped: {len(features)}")

    # Print borough breakdown
    borough_counts = defaultdict(int)
    for f in features:
        borough_counts[f['properties']['event_borough']] += 1

    print("\nBreakdown by borough:")
    for borough, count in sorted(borough_counts.items(), key=lambda x: -x[1]):
        print(f"  {borough}: {count}")

    # Print activity stats
    total_parties = sum(f['properties']['total_block_parties'] for f in features)
    avg_years = sum(f['properties']['years_active_count'] for f in features) / len(features) if features else 0
    multi_name_locations = sum(1 for f in features if f['properties']['unique_association_names_count'] > 1)

    print(f"\nActivity stats:")
    print(f"  Total block parties tracked: {total_parties}")
    print(f"  Average years active per location: {avg_years:.1f}")
    print(f"  Locations with multiple association names: {multi_name_locations}")


if __name__ == "__main__":
    main()
