"""
NYC Block Party Permit Geocoder

This script:
1. Fetches permit data from NYC Open Data API
2. Parses "STREET between CROSS_A and CROSS_B" location format
3. Geocodes using NYC Geoclient Blockface API
4. Outputs GeoJSON for web mapping

Usage:
    python block_party_geocoder.py

Output:
    block_parties.geojson - Ready for web mapping
"""

import requests
import json
import re
import time
from datetime import datetime

# =============================================================================
# CONFIGURATION - Your NYC Geoclient API credentials
# =============================================================================
# Your subscription key from api-portal.nyc.gov
SUBSCRIPTION_KEY = "cbb1cf4e14f74da19eaa4010a2bf64d9"

# NYC Open Data endpoints for permitted events
# Current events (upcoming month) - does NOT have "Block Party" type
NYC_CURRENT_URL = "https://data.cityofnewyork.us/resource/tvpp-9vvx.json"
# Historical events (2008-present) - HAS "Block Party" type
NYC_HISTORICAL_URL = "https://data.cityofnewyork.us/resource/bkfu-528j.json"

# Geoclient API base URL (v2)
GEOCLIENT_BASE_URL = "https://api.nyc.gov/geoclient/v2"

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def parse_location(location_text):
    """
    Parse location strings like:
    "AMSTERDAM AVENUE between WEST   96 STREET and WEST   97 STREET"

    Returns dict with on_street, cross_street_one, cross_street_two
    or None if parsing fails.
    """
    if not location_text:
        return None

    # Normalize whitespace
    text = re.sub(r'\s+', ' ', location_text.strip())

    # Match pattern: "Street between CrossA and CrossB"
    match = re.match(
        r"(.+?)\s+between\s+(.+?)\s+and\s+(.+)",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    return {
        'on_street': match.group(1).strip(),
        'cross_street_one': match.group(2).strip(),
        'cross_street_two': match.group(3).strip()
    }


def normalize_street_name(name):
    """
    Normalize street names for better API matching.
    """
    name = name.upper().strip()

    # Remove extra whitespace
    name = re.sub(r'\s+', ' ', name)

    # Common abbreviations the API prefers
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


def geocode_blockface(on_street, cross_one, cross_two, borough):
    """
    Call NYC Geoclient Blockface API to get coordinates for a street segment.

    Returns dict with coordinates and metadata, or None if geocoding fails.
    """
    # Normalize street names
    on_street = normalize_street_name(on_street)
    cross_one = normalize_street_name(cross_one)
    cross_two = normalize_street_name(cross_two)

    # Build API request
    params = {
        'onStreet': on_street,
        'crossStreetOne': cross_one,
        'crossStreetTwo': cross_two,
        'borough': borough
    }

    # Authentication via header (Geoclient v2 method)
    headers = {
        'Ocp-Apim-Subscription-Key': SUBSCRIPTION_KEY,
        'Cache-Control': 'no-cache'
    }

    url = f"{GEOCLIENT_BASE_URL}/blockface.json"

    try:
        response = requests.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Check for successful geocode
        blockface = data.get('blockface', {})

        # Get return code - "00" or "01" means success
        return_code = blockface.get('geosupportReturnCode', '')

        if return_code not in ['00', '01']:
            error_msg = blockface.get('message', 'Unknown error')
            print(f"  Geocoding failed: {error_msg}")
            return None

        # Extract coordinates from intersection lat/lon fields
        from_lat = blockface.get('latitudeOfFromIntersection')
        from_lon = blockface.get('longitudeOfFromIntersection')
        to_lat = blockface.get('latitudeOfToIntersection')
        to_lon = blockface.get('longitudeOfToIntersection')

        # Calculate midpoint of the blockface
        if from_lat and from_lon and to_lat and to_lon:
            lat = (float(from_lat) + float(to_lat)) / 2
            lon = (float(from_lon) + float(to_lon)) / 2
        elif from_lat and from_lon:
            lat = float(from_lat)
            lon = float(from_lon)
        else:
            lat = None
            lon = None

        if lat and lon:
            return {
                'latitude': float(lat),
                'longitude': float(lon),
                'from_node': blockface.get('nodeIdFrom'),
                'to_node': blockface.get('nodeIdTo'),
                'street_code': blockface.get('streetCode1'),
                'raw_response': blockface
            }

        print(f"  No coordinates in response")
        return None

    except requests.exceptions.RequestException as e:
        print(f"  API request failed: {e}")
        return None
    except (KeyError, ValueError) as e:
        print(f"  Error parsing response: {e}")
        return None


def fetch_permit_data(limit=5000, event_type_filter="Block Party", use_historical=True, year_filter=None):
    """
    Fetch permit data from NYC Open Data API.

    Args:
        limit: Maximum number of records to fetch
        event_type_filter: Filter by event type (default: "Block Party")
        use_historical: If True, use historical dataset (has Block Party type)
        year_filter: Optional year to filter (e.g., 2024)

    Returns list of event records.
    """
    url = NYC_HISTORICAL_URL if use_historical else NYC_CURRENT_URL

    params = {
        '$limit': limit,
        '$order': 'start_date_time DESC'
    }

    # Build query - filter by event type and require "between" in location
    where_clauses = ["event_location like '%between%'"]

    if event_type_filter:
        where_clauses.append(f"event_type = '{event_type_filter}'")

    if year_filter:
        where_clauses.append(f"start_date_time >= '{year_filter}-01-01'")
        where_clauses.append(f"start_date_time < '{year_filter + 1}-01-01'")

    params['$where'] = ' AND '.join(where_clauses)

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data: {e}")
        return []


def create_geojson_feature(event, geocode_result):
    """
    Create a GeoJSON Feature from an event and its geocoded coordinates.
    """
    # Parse dates for display
    start_date = event.get('start_date_time', '')
    end_date = event.get('end_date_time', '')

    # Format dates nicely
    try:
        if start_date:
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            start_formatted = start_dt.strftime('%B %d, %Y %I:%M %p')
        else:
            start_formatted = 'Unknown'
    except:
        start_formatted = start_date

    try:
        if end_date:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            end_formatted = end_dt.strftime('%B %d, %Y %I:%M %p')
        else:
            end_formatted = 'Unknown'
    except:
        end_formatted = end_date

    return {
        'type': 'Feature',
        'geometry': {
            'type': 'Point',
            'coordinates': [geocode_result['longitude'], geocode_result['latitude']]
        },
        'properties': {
            'event_id': event.get('event_id', ''),
            'event_name': event.get('event_name', 'Unnamed Event'),
            'event_type': event.get('event_type', ''),
            'event_agency': event.get('event_agency', ''),
            'event_location': event.get('event_location', ''),
            'event_borough': event.get('event_borough', ''),
            'start_date': start_date,
            'end_date': end_date,
            'start_formatted': start_formatted,
            'end_formatted': end_formatted,
            'street_closure_type': event.get('street_closure_type', ''),
            'community_board': event.get('community_board', ''),
            'police_precinct': event.get('police_precinct', '')
        }
    }


def geocode_year(year, geocode_cache, verbose=True):
    """
    Geocode all block parties for a specific year.
    Returns tuple of (features, success_count, fail_count, total_events)
    """
    if verbose:
        print(f"\n{'='*60}")
        print(f"Processing year: {year}")
        print("="*60)

    # Fetch data for this year
    events = fetch_permit_data(
        limit=5000,
        event_type_filter="Block Party",
        use_historical=True,
        year_filter=year
    )

    if verbose:
        print(f"  Found {len(events)} Block Party events")

    if not events:
        return [], 0, 0, 0

    # Deduplicate
    seen_locations = {}
    unique_events = []
    for event in events:
        key = (event.get('event_location', ''), event.get('event_name', ''), event.get('event_borough', ''))
        if key not in seen_locations:
            seen_locations[key] = True
            unique_events.append(event)

    if verbose:
        print(f"  After deduplication: {len(unique_events)} unique Block Parties")

    events = unique_events
    features = []
    success_count = 0
    fail_count = 0

    for i, event in enumerate(events):
        location = event.get('event_location', '')
        borough = event.get('event_borough', '')
        event_name = event.get('event_name', 'Unknown')

        if verbose and (i + 1) % 50 == 0:
            print(f"  Progress: {i+1}/{len(events)}")

        parsed = parse_location(location)
        if not parsed:
            fail_count += 1
            continue

        cache_key = (parsed['on_street'], parsed['cross_street_one'], parsed['cross_street_two'], borough)
        if cache_key in geocode_cache:
            result = geocode_cache[cache_key]
        else:
            result = geocode_blockface(
                parsed['on_street'],
                parsed['cross_street_one'],
                parsed['cross_street_two'],
                borough
            )
            if result:
                geocode_cache[cache_key] = result
            time.sleep(0.1)

        if result:
            feature = create_geojson_feature(event, result)
            features.append(feature)
            success_count += 1
        else:
            fail_count += 1

    return features, success_count, fail_count, len(events)


def main():
    """
    Main function to fetch, geocode, and export permit data for all years.
    """
    import sys

    # Check for command line arguments
    if len(sys.argv) > 1:
        # Process specific year(s)
        years = [int(y) for y in sys.argv[1:]]
    else:
        # Process all years from 2008 to 2025
        years = list(range(2008, 2026))

    print("=" * 60)
    print("NYC Block Party Permit Geocoder - All Years")
    print("=" * 60)
    print(f"Processing years: {min(years)} to {max(years)}")

    # Global geocode cache to reuse across years
    geocode_cache = {}

    # Track overall stats
    total_stats = {
        'total_events': 0,
        'total_success': 0,
        'total_fail': 0
    }

    year_summaries = []

    for year in years:
        features, success, fail, total = geocode_year(year, geocode_cache, verbose=True)

        if total == 0:
            print(f"  No events found for {year}")
            continue

        # Save year-specific file
        geojson = {
            'type': 'FeatureCollection',
            'features': features,
            'metadata': {
                'generated': datetime.now().isoformat(),
                'year': year,
                'total_events': total,
                'geocoded_successfully': success,
                'failed': fail,
                'source': 'NYC Open Data - Permitted Event Information'
            }
        }

        output_file = f'block_parties_{year}.geojson'
        with open(output_file, 'w') as f:
            json.dump(geojson, f, indent=2)

        print(f"  Saved: {output_file} ({success}/{total} = {success/total*100:.1f}%)")

        total_stats['total_events'] += total
        total_stats['total_success'] += success
        total_stats['total_fail'] += fail

        year_summaries.append({
            'year': year,
            'total': total,
            'success': success,
            'fail': fail,
            'file': output_file
        })

    # Also save 2025 as the default block_parties.geojson for backwards compatibility
    if 2025 in years:
        features_2025, _, _, _ = geocode_year(2025, geocode_cache, verbose=False)
        geojson_2025 = {
            'type': 'FeatureCollection',
            'features': features_2025,
            'metadata': {
                'generated': datetime.now().isoformat(),
                'year': 2025,
                'source': 'NYC Open Data - Permitted Event Information'
            }
        }
        with open('block_parties.geojson', 'w') as f:
            json.dump(geojson_2025, f, indent=2)
        print(f"\n  Also saved: block_parties.geojson (2025 default)")

    # Final summary
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"  Years processed: {len(year_summaries)}")
    print(f"  Total events:    {total_stats['total_events']}")
    print(f"  Total geocoded:  {total_stats['total_success']} ({total_stats['total_success']/max(1,total_stats['total_events'])*100:.1f}%)")
    print(f"  Total failed:    {total_stats['total_fail']}")
    print(f"  Cache hits:      {len(geocode_cache)} unique locations cached")
    print("\nPer-year breakdown:")
    for s in year_summaries:
        pct = s['success']/max(1,s['total'])*100
        print(f"  {s['year']}: {s['success']:4d}/{s['total']:4d} ({pct:5.1f}%) -> {s['file']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
