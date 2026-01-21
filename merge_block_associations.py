"""
Merge Block Associations Datasets

This script:
1. Reads the existing active_block_associations.geojson
2. Reads NYC_Block_Associations2.csv (different parameters)
3. Filters for block party/street festival events with "association" in the name
4. Aggregates by LOCATION (same as existing geocoder)
5. Finds new locations not in the existing dataset
6. Geocodes new locations and merges them into the existing GeoJSON
"""

import csv
import requests
import json
import re
import time
from datetime import datetime
from collections import defaultdict

# NYC Geoclient API credentials
SUBSCRIPTION_KEY = "cbb1cf4e14f74da19eaa4010a2bf64d9"
GEOCLIENT_BASE_URL = "https://api.nyc.gov/geoclient/v2"

# Input/output files
INPUT_CSV = "/Users/delaneyconnor/Downloads/NYC_Block_Associations2.csv"
EXISTING_GEOJSON = "/Users/delaneyconnor/Desktop/GRAD SCHOO/Year 2/Semester 1/Methods 3/VS Code/CB303 Block Associations/active_block_associations.geojson"
OUTPUT_GEOJSON = "/Users/delaneyconnor/Desktop/GRAD SCHOO/Year 2/Semester 1/Methods 3/VS Code/CB303 Block Associations/active_block_associations.geojson"

# Years range for tracking
ALL_YEARS = list(range(2008, 2026))  # 2008-2025


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


def load_existing_geojson(filepath):
    """
    Load existing GeoJSON and extract location keys and data.
    """
    with open(filepath, 'r') as f:
        data = json.load(f)

    existing_locations = {}
    for feature in data['features']:
        props = feature['properties']
        location = props.get('event_location', '')
        borough = props.get('event_borough', '')

        # Reconstruct the location key
        location_key = normalize_location_key(location, borough)
        if location_key:
            existing_locations[location_key] = feature

    return data, existing_locations


def read_new_data(input_file):
    """
    Read CSV and filter for block party/street festival associations.
    Returns a dict mapping normalized location -> {events, years, association_names}
    """
    print("Reading new dataset...")

    # First pass: collect all events
    all_events = []
    seen_event_ids = set()

    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            event_name = row.get('Event Name', '').lower()
            event_type = row.get('Event Type', '').lower()
            event_id = row.get('Event ID', '')
            location = row.get('Event Location', '')
            borough = row.get('Event Borough', '')
            date_str = row.get('Start Date/Time', '')

            # Filter for events with "association" in name
            if 'association' not in event_name:
                continue

            # Filter for Block Party OR Street Festival events
            is_block_party = (
                event_type == 'block party' or
                event_type == 'street festival' or
                'block party' in event_name or
                'street fair' in event_name or
                'festival' in event_name
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

            all_events.append(row)

    print(f"  Found {len(all_events)} unique events in new dataset")

    # Second pass: aggregate by LOCATION
    locations = defaultdict(lambda: {
        'events': [],
        'years': set(),
        'association_names': set(),
        'most_recent_event': None,
        'most_recent_date': ''
    })

    for event in all_events:
        location = event.get('Event Location', '')
        borough = event.get('Event Borough', '')
        date_str = event.get('Start Date/Time', '')
        year = extract_year(date_str)
        assoc_name = extract_association_name(event.get('Event Name', ''))

        location_key = normalize_location_key(location, borough)
        if not location_key:
            continue

        locations[location_key]['events'].append(event)
        locations[location_key]['association_names'].add(assoc_name)
        if year:
            locations[location_key]['years'].add(year)

        if date_str > locations[location_key]['most_recent_date']:
            locations[location_key]['most_recent_date'] = date_str
            locations[location_key]['most_recent_event'] = event

    print(f"  Aggregated into {len(locations)} unique block locations")
    return locations


def merge_adjacent_years(locations):
    """
    Share years between adjacent/overlapping segments.
    If two segments share the same main street AND at least one cross street,
    they are adjacent and should share their year histories.
    """
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

    return merged_count


def main():
    print("=" * 60)
    print("Merge Block Associations Datasets")
    print("=" * 60)

    # Step 1: Load existing GeoJSON
    print("\nLoading existing data...")
    existing_geojson, existing_locations = load_existing_geojson(EXISTING_GEOJSON)
    print(f"  Existing locations: {len(existing_locations)}")

    # Step 2: Read new dataset
    new_locations = read_new_data(INPUT_CSV)

    # Step 3: Find locations that need to be added or updated
    locations_to_add = {}
    locations_to_update = {}

    for location_key, data in new_locations.items():
        if location_key in existing_locations:
            # Location exists - check if we need to update years
            locations_to_update[location_key] = data
        else:
            # New location - needs geocoding
            locations_to_add[location_key] = data

    print(f"\n  New locations to geocode: {len(locations_to_add)}")
    print(f"  Existing locations to update with new years: {len(locations_to_update)}")

    # Step 4: Update existing features with new year data
    updated_count = 0
    for feature in existing_geojson['features']:
        props = feature['properties']
        location = props.get('event_location', '')
        borough = props.get('event_borough', '')
        location_key = normalize_location_key(location, borough)

        if location_key and location_key in locations_to_update:
            new_data = locations_to_update[location_key]

            # Merge years
            existing_years = set(props.get('years_active', []))
            new_years = new_data['years']
            combined_years = sorted(existing_years | new_years)

            # Merge association names
            existing_names = set(props.get('all_association_names', []))
            new_names = new_data['association_names']
            combined_names = existing_names | new_names

            # Update event count
            existing_count = props.get('total_block_parties', 0)
            new_event_count = len(new_data['events'])

            # Update properties
            props['years_active'] = combined_years
            props['years_inactive'] = [y for y in ALL_YEARS if y not in combined_years]
            props['years_active_count'] = len(combined_years)
            props['first_year'] = min(combined_years) if combined_years else None
            props['last_year'] = max(combined_years) if combined_years else None
            props['all_association_names'] = list(combined_names)
            props['unique_association_names_count'] = len(combined_names)
            props['total_block_parties'] = existing_count + new_event_count

            updated_count += 1

    print(f"  Updated {updated_count} existing locations with new year data")

    # Step 5: Geocode new locations
    if locations_to_add:
        print(f"\nGeocoding {len(locations_to_add)} new locations...")

        new_features = []
        geocode_cache = {}
        success_count = 0
        fail_count = 0

        location_list = list(locations_to_add.items())

        for i, (location_key, data) in enumerate(location_list):
            on_street, cross_one, cross_two, borough = location_key
            event = data['most_recent_event']
            years = sorted(data['years'])
            assoc_names = data['association_names']

            if (i + 1) % 20 == 0:
                print(f"  Progress: {i+1}/{len(location_list)}")

            cache_key = location_key

            if cache_key in geocode_cache:
                result = geocode_cache[cache_key]
            else:
                result = geocode_blockface(on_street, cross_one, cross_two, borough)
                if result:
                    geocode_cache[cache_key] = result
                time.sleep(0.1)  # Rate limiting

            if result:
                most_recent_assoc_name = extract_association_name(event.get('Event Name', ''))
                years_active = sorted(years)
                years_inactive = [y for y in ALL_YEARS if y not in years]
                total_parties = len(data['events'])
                first_year = min(years) if years else None
                last_year = max(years) if years else None

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
                        'association_name': most_recent_assoc_name,
                        'all_association_names': list(assoc_names),
                        'event_name': event.get('Event Name', ''),
                        'event_type': event.get('Event Type', ''),
                        'event_borough': borough.title(),
                        'event_location': location_display,
                        'community_board': event.get('Community Board', '').strip().rstrip(','),
                        'police_precinct': event.get('Police Precinct', '').strip().rstrip(','),
                        'last_event_date': data['most_recent_date'],
                        'total_block_parties': total_parties,
                        'years_active': years_active,
                        'years_inactive': years_inactive,
                        'first_year': first_year,
                        'last_year': last_year,
                        'years_active_count': len(years_active),
                        'unique_association_names_count': len(assoc_names),
                        'source': 'NYC_Block_Associations2.csv'
                    }
                }
                new_features.append(feature)
                success_count += 1
            else:
                fail_count += 1

        print(f"\nGeocoding complete:")
        print(f"  Success: {success_count}")
        print(f"  Failed: {fail_count}")

        # Add new features to existing GeoJSON
        existing_geojson['features'].extend(new_features)

    # Step 6: Update metadata and save
    existing_geojson['metadata'] = {
        'generated': datetime.now().isoformat(),
        'total_locations': len(existing_geojson['features']),
        'years_range': f"{min(ALL_YEARS)}-{max(ALL_YEARS)}",
        'aggregation': 'by_location',
        'source': 'NYC Open Data - Permitted Event Information (merged from multiple datasets)'
    }

    with open(OUTPUT_GEOJSON, 'w') as f:
        json.dump(existing_geojson, f, indent=2)

    print(f"\nSaved: {OUTPUT_GEOJSON}")
    print(f"Total block locations mapped: {len(existing_geojson['features'])}")

    # Print borough breakdown
    borough_counts = defaultdict(int)
    for f in existing_geojson['features']:
        borough_counts[f['properties']['event_borough']] += 1

    print("\nBreakdown by borough:")
    for borough, count in sorted(borough_counts.items(), key=lambda x: -x[1]):
        print(f"  {borough}: {count}")

    # Print activity stats
    total_parties = sum(f['properties']['total_block_parties'] for f in existing_geojson['features'])
    avg_years = sum(f['properties']['years_active_count'] for f in existing_geojson['features']) / len(existing_geojson['features']) if existing_geojson['features'] else 0
    multi_name_locations = sum(1 for f in existing_geojson['features'] if f['properties']['unique_association_names_count'] > 1)

    print(f"\nActivity stats:")
    print(f"  Total block parties tracked: {total_parties}")
    print(f"  Average years active per location: {avg_years:.1f}")
    print(f"  Locations with multiple association names: {multi_name_locations}")


if __name__ == "__main__":
    main()
