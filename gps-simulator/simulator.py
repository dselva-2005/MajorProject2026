import math
import random
import time

import psycopg
import requests


# ============================================================
# Configuration
# ============================================================

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "tollmap",
    "user": "tollapp",
    "password": "tollapp_dev_password",
}

DJANGO_URL = "http://localhost:8000"
VALHALLA_URL = "http://localhost:8002"

VEHICLE_ID = "VH001"

LOCATION_API = f"{DJANGO_URL}/api/vehicles/{VEHICLE_ID}/locations/"

ROUTE_API = f"{VALHALLA_URL}/route"


# ============================================================
# Simulation settings
# ============================================================

SEND_INTERVAL = 2.0

# Approximately 2 metres of GPS noise.
GPS_NOISE = 0.00002

MIN_SPEED_KMH = 40
MAX_SPEED_KMH = 80

# Desired ACTUAL Valhalla route distance.
MIN_TRIP_DISTANCE_KM = 10.0
MAX_TRIP_DISTANCE_KM = 15.0

# Destination is generated this far from the toll start
# in straight-line distance.
MIN_DESTINATION_DISTANCE_KM = 7.0
MAX_DESTINATION_DISTANCE_KM = 11.0

# Number of attempts to find a route in the desired range.
MAX_JOURNEY_ATTEMPTS = 15


# ============================================================
# Road classes
# ============================================================

MAJOR_HIGHWAYS = (
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
)


# ============================================================
# Distance
# ============================================================


def distance_meters(
    lat1,
    lon1,
    lat2,
    lon2,
):
    """
    Calculate great-circle distance between
    two latitude/longitude coordinates.
    """

    earth_radius = 6371000.0

    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)

    dlat = lat2_rad - lat1_rad

    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )

    return (
        earth_radius
        * 2
        * math.atan2(
            math.sqrt(a),
            math.sqrt(1 - a),
        )
    )


# ============================================================
# GPS noise
# ============================================================


def add_gps_noise(
    lat,
    lon,
):
    """
    Add small GPS measurement error.
    """

    return (
        lat
        + random.uniform(
            -GPS_NOISE,
            GPS_NOISE,
        ),
        lon
        + random.uniform(
            -GPS_NOISE,
            GPS_NOISE,
        ),
    )


# ============================================================
# Database connection
# ============================================================


def get_connection():
    return psycopg.connect(**DB_CONFIG)


# ============================================================
# Select random tolled-road point
# ============================================================


def get_random_toll_point():
    """
    Select a random point directly ON an actual
    toll=yes road.

    This is intentionally simple.

    No spatial search.
    No nearby-road search.
    No local-road search.

    The journey starts directly on the toll road.
    """

    query = """
        SELECT
            ST_Y(point_4326) AS lat,
            ST_X(point_4326) AS lon,
            way_id,
            name,
            highway,
            ref,
            toll

        FROM (
            SELECT
                ST_Transform(
                    ST_LineInterpolatePoint(
                        roads.geom,
                        random()
                    ),
                    4326
                ) AS point_4326,

                roads.way_id,
                roads.name,
                roads.highway,
                roads.ref,
                roads.toll

            FROM roads

            WHERE roads.toll = 'yes'
              AND roads.highway = ANY(%s)
              AND roads.geom IS NOT NULL

            ORDER BY random()

            LIMIT 1
        ) AS selected_road;
    """

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (list(MAJOR_HIGHWAYS),),
            )

            row = cursor.fetchone()

    if row is None:
        raise RuntimeError("No tolled major road found in database.")

    (
        lat,
        lon,
        way_id,
        name,
        highway,
        ref,
        toll,
    ) = row

    return {
        "lat": float(lat),
        "lon": float(lon),
        "way_id": int(way_id),
        "name": name or "Unnamed",
        "highway": highway or "",
        "ref": ref or "",
        "toll": toll or "",
    }


# ============================================================
# Generate nearby destination
# ============================================================


def get_destination(
    start,
):
    """
    Generate a destination coordinate a short distance
    from the toll-road starting point.

    We deliberately do NOT query PostGIS here.

    Valhalla will snap this coordinate to the nearest
    routable road.
    """

    distance_km = random.uniform(
        MIN_DESTINATION_DISTANCE_KM,
        MAX_DESTINATION_DISTANCE_KM,
    )

    bearing = random.uniform(
        0.0,
        2.0 * math.pi,
    )

    earth_radius_km = 6371.0

    start_lat = math.radians(start["lat"])

    start_lon = math.radians(start["lon"])

    angular_distance = distance_km / earth_radius_km

    destination_lat = math.asin(
        math.sin(start_lat) * math.cos(angular_distance)
        + math.cos(start_lat) * math.sin(angular_distance) * math.cos(bearing)
    )

    destination_lon = start_lon + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(start_lat),
        math.cos(angular_distance) - math.sin(start_lat) * math.sin(destination_lat),
    )

    return {
        "lat": math.degrees(destination_lat),
        "lon": math.degrees(destination_lon),
    }


# ============================================================
# Decode Valhalla polyline6
# ============================================================


def decode_polyline6(encoded):
    """
    Decode Valhalla's polyline6 geometry.
    """

    coordinates = []

    index = 0

    latitude = 0
    longitude = 0

    factor = 1000000

    while index < len(encoded):
        result = 0
        shift = 0

        while True:
            if index >= len(encoded):
                raise RuntimeError("Invalid polyline6 geometry.")

            byte = ord(encoded[index]) - 63

            index += 1

            result |= (byte & 0x1F) << shift

            shift += 5

            if byte < 0x20:
                break

        if result & 1:
            delta_lat = ~(result >> 1)
        else:
            delta_lat = result >> 1

        latitude += delta_lat

        result = 0
        shift = 0

        while True:
            if index >= len(encoded):
                raise RuntimeError("Invalid polyline6 geometry.")

            byte = ord(encoded[index]) - 63

            index += 1

            result |= (byte & 0x1F) << shift

            shift += 5

            if byte < 0x20:
                break

        if result & 1:
            delta_lon = ~(result >> 1)
        else:
            delta_lon = result >> 1

        longitude += delta_lon

        coordinates.append(
            (
                latitude / factor,
                longitude / factor,
            )
        )

    return coordinates


# ============================================================
# Ask Valhalla for a route
# ============================================================


def get_route(
    start,
    destination,
):
    """
    Route from the toll road directly to the destination.

    The starting point is already on a toll=yes road.

    No extra toll waypoint is necessary.
    """

    payload = {
        "locations": [
            {
                "lat": start["lat"],
                "lon": start["lon"],
                "type": "break",
            },
            {
                "lat": destination["lat"],
                "lon": destination["lon"],
                "type": "break",
            },
        ],
        "costing": "auto",
        "units": "kilometers",
        "shape_format": "polyline6",
    }

    try:
        response = requests.post(
            ROUTE_API,
            json=payload,
            timeout=30,
        )

        response.raise_for_status()

    except requests.RequestException as error:
        raise RuntimeError(f"Valhalla route request failed: {error}") from error

    try:
        data = response.json()

    except ValueError as error:
        raise RuntimeError("Valhalla returned invalid JSON.") from error

    try:
        legs = data["trip"]["legs"]

    except (
        KeyError,
        TypeError,
    ) as error:
        raise RuntimeError("Valhalla returned a route without legs.") from error

    if not legs:
        raise RuntimeError("Valhalla returned an empty route.")

    coordinates = []

    for leg in legs:
        try:
            encoded_shape = leg["shape"]

        except (
            KeyError,
            TypeError,
        ) as error:
            raise RuntimeError("Valhalla returned a leg without geometry.") from error

        leg_coordinates = decode_polyline6(encoded_shape)

        if not leg_coordinates:
            continue

        if not coordinates:
            coordinates.extend(leg_coordinates)

        else:
            coordinates.extend(leg_coordinates[1:])

    if len(coordinates) < 2:
        raise RuntimeError("Valhalla route contains insufficient coordinates.")

    return coordinates, data


# ============================================================
# Get route distance
# ============================================================


def get_route_distance_km(
    route_data,
):
    """
    Extract actual Valhalla route distance.
    """

    try:
        summary = route_data["trip"]["summary"]

        return float(
            summary.get(
                "length",
                0,
            )
        )

    except (
        KeyError,
        TypeError,
        ValueError,
    ):
        return 0.0


# ============================================================
# Build journey
# ============================================================


def build_journey():
    """
    Build a simple journey:

        TOLLED ROAD
             ↓
          10–15 km
             ↓
        DESTINATION

    The starting point is always on a real toll=yes road.

    We only query PostGIS once to find the starting point.
    """

    # --------------------------------------------------------
    # Pick the toll road ONCE.
    # --------------------------------------------------------

    start = get_random_toll_point()

    print()
    print("Selected toll-road starting point:")

    print(f"  Road    : {start['name']}")

    print(f"  Way     : {start['way_id']}")

    print(f"  Highway : {start['highway']}")

    print(f"  Ref     : {start['ref'] or 'None'}")

    print(f"  Toll    : {start['toll']}")

    print(f"  Point   : {start['lat']:.6f}, {start['lon']:.6f}")

    # --------------------------------------------------------
    # Try a few nearby destinations until Valhalla gives us
    # an actual 10–15 km route.
    # --------------------------------------------------------

    for attempt in range(
        1,
        MAX_JOURNEY_ATTEMPTS + 1,
    ):
        destination = get_destination(start)

        straight_line_distance = (
            distance_meters(
                start["lat"],
                start["lon"],
                destination["lat"],
                destination["lon"],
            )
            / 1000.0
        )

        print()
        print(f"Route attempt {attempt}/{MAX_JOURNEY_ATTEMPTS}")

        print(f"  Straight-line: {straight_line_distance:.2f} km")

        try:
            coordinates, route_data = get_route(
                start,
                destination,
            )

        except RuntimeError as error:
            print(f"  Route failed: {error}")

            continue

        route_distance_km = get_route_distance_km(route_data)

        print(f"  Valhalla route: {route_distance_km:.2f} km")

        if MIN_TRIP_DISTANCE_KM <= route_distance_km <= MAX_TRIP_DISTANCE_KM:
            print("  Valid 10–15 km journey found.")

            return (
                start,
                destination,
                coordinates,
                route_data,
            )

        print("  Route outside 10–15 km. Trying another destination.")

    raise RuntimeError("Could not find a 10–15 km route from the selected toll road.")


# ============================================================
# Send GPS point
# ============================================================


def send_location(
    lat,
    lon,
):
    """
    Send one GPS coordinate to Django.
    """

    response = requests.post(
        LOCATION_API,
        json={
            "latitude": lat,
            "longitude": lon,
        },
        timeout=10,
    )

    response.raise_for_status()

    print(f"GPS  {lat:.6f}, {lon:.6f}")


# ============================================================
# Follow route
# ============================================================


def drive_route(
    coordinates,
):
    """
    Follow the Valhalla-generated route.
    """

    speed_kmh = random.uniform(
        MIN_SPEED_KMH,
        MAX_SPEED_KMH,
    )

    speed_mps = speed_kmh / 3.6

    print()
    print(f"Speed: {speed_kmh:.1f} km/h")

    for index in range(len(coordinates) - 1):
        lat1, lon1 = coordinates[index]

        lat2, lon2 = coordinates[index + 1]

        segment_distance = distance_meters(
            lat1,
            lon1,
            lat2,
            lon2,
        )

        if segment_distance <= 0:
            continue

        travel_time = segment_distance / speed_mps

        steps = max(
            1,
            math.ceil(travel_time / SEND_INTERVAL),
        )

        for step in range(steps):
            progress = step / steps

            lat = lat1 + (lat2 - lat1) * progress

            lon = lon1 + (lon2 - lon1) * progress

            noisy_lat, noisy_lon = add_gps_noise(
                lat,
                lon,
            )

            try:
                send_location(
                    noisy_lat,
                    noisy_lon,
                )

            except requests.RequestException as error:
                print(f"GPS send failed: {error}")

                print("Retrying...")

                time.sleep(2)

                continue

            time.sleep(SEND_INTERVAL)

    # --------------------------------------------------------
    # Final position.
    # --------------------------------------------------------

    final_lat, final_lon = coordinates[-1]

    noisy_lat, noisy_lon = add_gps_noise(
        final_lat,
        final_lon,
    )

    send_location(
        noisy_lat,
        noisy_lon,
    )


# ============================================================
# Print trip information
# ============================================================


def print_trip(
    start,
    destination,
    route_data,
):
    """
    Print journey information.
    """

    try:
        summary = route_data["trip"]["summary"]

        length = summary.get(
            "length",
            0,
        )

        time_seconds = summary.get(
            "time",
            0,
        )

    except (
        KeyError,
        TypeError,
    ):
        length = 0
        time_seconds = 0

    print()
    print("=" * 70)

    print("NEW TOLL JOURNEY")

    print("=" * 70)

    print(f"Start          : {start['lat']:.6f}, {start['lon']:.6f}")

    print(f"Start road     : {start['name']}")

    print(f"Start way      : {start['way_id']}")

    print(f"Start type     : {start['highway']}")

    print(f"Start ref      : {start['ref'] or 'None'}")

    print(f"Toll tag       : {start['toll']}")

    print()

    print(f"Destination    : {destination['lat']:.6f}, {destination['lon']:.6f}")

    print()

    print(f"Route          : {length:.2f} km")

    print(f"ETA            : {time_seconds / 60:.1f} minutes")

    print("=" * 70)


# ============================================================
# Main
# ============================================================


def main():

    print()
    print("=" * 70)

    print("INDIA GPS VEHICLE SIMULATOR")

    print("=" * 70)

    print(f"Vehicle        : {VEHICLE_ID}")

    print(f"GPS interval   : {SEND_INTERVAL}s")

    print("GPS noise      : ~2 metres")

    print(f"Speed          : {MIN_SPEED_KMH}-{MAX_SPEED_KMH} km/h")

    print(f"Trip distance  : {MIN_TRIP_DISTANCE_KM}-{MAX_TRIP_DISTANCE_KM} km")

    print("Start          : Tolled road")

    print("Routing        : Valhalla")

    print("Behaviour      : Toll Road → Destination")

    print("=" * 70)

    while True:
        try:
            (
                start,
                destination,
                coordinates,
                route_data,
            ) = build_journey()

            print_trip(
                start,
                destination,
                route_data,
            )

            print(f"Route points: {len(coordinates)}")

            print()
            print("Starting journey...")

            drive_route(coordinates)

            print()
            print("Reached destination.")

            print()
            print("Preparing next journey...")

            time.sleep(5)

        except KeyboardInterrupt:
            print()
            print("Simulator stopped.")

            break

        except Exception as error:
            print()
            print("Simulator error:")

            print(f"  {error}")

            print()
            print("Waiting 5 seconds before retrying...")

            time.sleep(5)


# ============================================================
# Start
# ============================================================

if __name__ == "__main__":
    main()
