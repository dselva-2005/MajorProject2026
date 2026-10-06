import json
import urllib.error
import urllib.request

from django.contrib.gis.geos import Point
from django.db import connection, transaction

from .models import (
    JourneySegment,
    MatchedLocation,
    VehicleLocation,
)


VALHALLA_URL = "http://localhost:8002/trace_attributes"


class ValhallaError(Exception):
    """Raised when Valhalla cannot process a GPS trace."""
    pass


def classify_road(ref):
    """
    Classify an OSM road using its ref.

    National:
        NH...
        NE-...

    State:
        SH...

    Other:
        Everything else.
    """

    ref = (ref or "").strip().upper()

    if (
        ref.startswith("NH")
        or ref.startswith("NE-")
    ):
        return "national"

    if ref.startswith("SH"):
        return "state"

    return "other"


def match_trace_with_valhalla(locations):
    """
    Send raw GPS points to Valhalla for map matching.

    Returns Valhalla's trace_attributes response.
    """

    shape = [
        {
            "lat": location.point.y,
            "lon": location.point.x,
        }
        for location in locations
    ]

    request_body = {
        "shape": shape,
        "costing": "auto",
        "shape_match": "map_snap",
        "filters": {
            "action": "include",
            "attributes": [
                "edge.way_id",
                "edge.length",
                "edge.begin_shape_index",
                "edge.end_shape_index",
                "matched.point",
                "matched.type",
                "matched.edge_index",
                "matched.distance_along_edge",
            ],
        },
    }

    data = json.dumps(
        request_body
    ).encode("utf-8")

    request = urllib.request.Request(
        VALHALLA_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=15,
        ) as response:

            return json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

    except urllib.error.HTTPError as error:

        details = error.read().decode(
            "utf-8",
            errors="replace",
        )

        raise ValhallaError(
            f"Valhalla returned HTTP "
            f"{error.code}: {details}"
        ) from error

    except urllib.error.URLError as error:

        raise ValhallaError(
            f"Could not connect to Valhalla: "
            f"{error}"
        ) from error

    except json.JSONDecodeError as error:

        raise ValhallaError(
            f"Invalid response from Valhalla: "
            f"{error}"
        ) from error


def get_road_data(way_ids):
    """
    Get OSM metadata for the Valhalla way IDs.

    Valhalla:
        way_id

    PostGIS:
        toll
        ref

    A way_id can occur in multiple rows in the
    imported roads table.

    Therefore toll status is aggregated so that
    if ANY matching row has toll=yes, the
    resulting way is considered tolled.
    """

    if not way_ids:
        return {}

    with connection.cursor() as cursor:

        cursor.execute(
            """
            SELECT
                way_id,
                BOOL_OR(toll = 'yes') AS is_toll,
                MAX(ref) AS ref
            FROM roads
            WHERE way_id = ANY(%s)
            GROUP BY way_id
            """,
            [list(way_ids)],
        )

        return {
            int(row[0]): {
                "toll": bool(row[1]),
                "ref": row[2] or "",
            }
            for row in cursor.fetchall()
        }


def get_edge_distance_km(
    edge,
    start_fraction,
    end_fraction,
):
    """
    Calculate travelled distance on one Valhalla edge.

    edge.length is in kilometres.

    distance_along_edge is treated as a
    normalized fraction from 0.0 to 1.0.
    """

    length_km = float(
        edge.get(
            "length",
            0.0
        )
    )

    start_fraction = max(
        0.0,
        min(
            1.0,
            float(start_fraction)
        )
    )

    end_fraction = max(
        0.0,
        min(
            1.0,
            float(end_fraction)
        )
    )

    return abs(
        end_fraction - start_fraction
    ) * length_km


def calculate_segment_distance_km(
    edges,
    start_match,
    end_match,
):
    """
    Calculate network distance between two
    consecutive matched GPS points.

    Same edge:

        partial edge distance

    Different edges:

        remainder of starting edge
        + complete intermediate edges
        + beginning of destination edge
    """

    start_edge_index = start_match.get(
        "edge_index"
    )

    end_edge_index = end_match.get(
        "edge_index"
    )

    if (
        start_edge_index is None
        or end_edge_index is None
    ):
        raise ValhallaError(
            "Matched point is missing "
            "edge_index."
        )

    start_edge_index = int(
        start_edge_index
    )

    end_edge_index = int(
        end_edge_index
    )

    if (
        start_edge_index < 0
        or start_edge_index >= len(edges)
    ):
        raise ValhallaError(
            f"Invalid start edge index: "
            f"{start_edge_index}"
        )

    if (
        end_edge_index < 0
        or end_edge_index >= len(edges)
    ):
        raise ValhallaError(
            f"Invalid end edge index: "
            f"{end_edge_index}"
        )

    start_fraction = float(
        start_match.get(
            "distance_along_edge",
            0.0
        )
    )

    end_fraction = float(
        end_match.get(
            "distance_along_edge",
            0.0
        )
    )

    # Same edge.
    if start_edge_index == end_edge_index:

        return get_edge_distance_km(
            edges[start_edge_index],
            start_fraction,
            end_fraction,
        )

    total_distance_km = 0.0

    # Remaining portion of starting edge.
    total_distance_km += (
        get_edge_distance_km(
            edges[start_edge_index],
            start_fraction,
            1.0,
        )
    )

    # Complete edges between start and end.
    for edge_index in range(
        start_edge_index + 1,
        end_edge_index,
    ):

        total_distance_km += float(
            edges[edge_index].get(
                "length",
                0.0
            )
        )

    # Beginning portion of destination edge.
    total_distance_km += (
        get_edge_distance_km(
            edges[end_edge_index],
            0.0,
            end_fraction,
        )
    )

    return total_distance_km


def build_journey_segments(
    locations,
    matched_points,
    edges,
    road_data,
):
    """
    Convert Valhalla's matched edge path into
    JourneySegment records.

    Each JourneySegment represents one travelled
    portion of one Valhalla edge.

    Toll classification is performed per edge span.
    """

    journey_segments = []

    for index in range(
        1,
        len(matched_points)
    ):

        start_match = matched_points[
            index - 1
        ]

        end_match = matched_points[
            index
        ]

        start_edge_index = start_match.get(
            "edge_index"
        )

        end_edge_index = end_match.get(
            "edge_index"
        )

        if (
            start_edge_index is None
            or end_edge_index is None
        ):
            raise ValhallaError(
                f"Matched points {index - 1} "
                f"and {index} are missing "
                "edge_index."
            )

        start_edge_index = int(
            start_edge_index
        )

        end_edge_index = int(
            end_edge_index
        )

        if (
            start_edge_index < 0
            or start_edge_index >= len(edges)
        ):
            raise ValhallaError(
                f"Invalid start edge index: "
                f"{start_edge_index}"
            )

        if (
            end_edge_index < 0
            or end_edge_index >= len(edges)
        ):
            raise ValhallaError(
                f"Invalid end edge index: "
                f"{end_edge_index}"
            )

        start_fraction = float(
            start_match.get(
                "distance_along_edge",
                0.0
            )
        )

        end_fraction = float(
            end_match.get(
                "distance_along_edge",
                0.0
            )
        )

        # --------------------------------------------------
        # Same edge.
        # --------------------------------------------------

        if start_edge_index == end_edge_index:

            edge = edges[
                start_edge_index
            ]

            way_id = edge.get(
                "way_id"
            )

            if way_id is None:
                raise ValhallaError(
                    f"Valhalla edge "
                    f"{start_edge_index} has "
                    "no way_id."
                )

            way_id = int(
                way_id
            )

            road = road_data.get(
                way_id
            )

            if road is None:
                raise ValhallaError(
                    f"No PostGIS data "
                    f"for way {way_id}."
                )

            distance_km = (
                get_edge_distance_km(
                    edge,
                    start_fraction,
                    end_fraction,
                )
            )

            if distance_km > 0:

                start_point = Point(
                    float(
                        start_match["lon"]
                    ),
                    float(
                        start_match["lat"]
                    ),
                    srid=4326,
                )

                end_point = Point(
                    float(
                        end_match["lon"]
                    ),
                    float(
                        end_match["lat"]
                    ),
                    srid=4326,
                )

                ref = road["ref"]

                journey_segments.append(
                    JourneySegment(
                        vehicle_id=locations[
                            index - 1
                        ].vehicle_id,

                        start_time=locations[
                            index - 1
                        ].timestamp,

                        end_time=locations[
                            index
                        ].timestamp,

                        edge_index=start_edge_index,

                        way_id=way_id,

                        start_fraction=start_fraction,

                        end_fraction=end_fraction,

                        distance_meters=(
                            distance_km * 1000.0
                        ),

                        toll=road["toll"],

                        ref=ref,

                        category=classify_road(
                            ref
                        ),

                        start_point=start_point,

                        end_point=end_point,
                    )
                )

            continue

        # --------------------------------------------------
        # Different edges.
        #
        # Build:
        #
        #   remainder of first edge
        #   full intermediate edges
        #   beginning of final edge
        # --------------------------------------------------

        edge_spans = []

        # First edge:
        # start_fraction -> 1.0
        edge_spans.append(
            (
                start_edge_index,
                start_fraction,
                1.0,
            )
        )

        # Intermediate edges:
        # 0.0 -> 1.0
        for edge_index in range(
            start_edge_index + 1,
            end_edge_index,
        ):

            edge_spans.append(
                (
                    edge_index,
                    0.0,
                    1.0,
                )
            )

        # Final edge:
        # 0.0 -> end_fraction
        edge_spans.append(
            (
                end_edge_index,
                0.0,
                end_fraction,
            )
        )

        for (
            edge_index,
            span_start_fraction,
            span_end_fraction,
        ) in edge_spans:

            edge = edges[
                edge_index
            ]

            way_id = edge.get(
                "way_id"
            )

            if way_id is None:
                raise ValhallaError(
                    f"Valhalla edge "
                    f"{edge_index} has "
                    "no way_id."
                )

            way_id = int(
                way_id
            )

            road = road_data.get(
                way_id
            )

            if road is None:
                raise ValhallaError(
                    f"No PostGIS data "
                    f"for way {way_id}."
                )

            distance_km = (
                get_edge_distance_km(
                    edge,
                    span_start_fraction,
                    span_end_fraction,
                )
            )

            if distance_km <= 0:
                continue

            # --------------------------------------------------
            # Point geometry.
            #
            # The exact boundary coordinates of intermediate
            # Valhalla edges are not currently exposed by the
            # requested response attributes.
            #
            # Therefore we preserve the known GPS endpoints
            # rather than inventing coordinates.
            # --------------------------------------------------

            segment_start_point = Point(
                float(
                    start_match["lon"]
                ),
                float(
                    start_match["lat"]
                ),
                srid=4326,
            )

            segment_end_point = Point(
                float(
                    end_match["lon"]
                ),
                float(
                    end_match["lat"]
                ),
                srid=4326,
            )

            ref = road["ref"]

            journey_segments.append(
                JourneySegment(
                    vehicle_id=locations[
                        index - 1
                    ].vehicle_id,

                    start_time=locations[
                        index - 1
                    ].timestamp,

                    end_time=locations[
                        index
                    ].timestamp,

                    edge_index=edge_index,

                    way_id=way_id,

                    start_fraction=(
                        span_start_fraction
                    ),

                    end_fraction=(
                        span_end_fraction
                    ),

                    distance_meters=(
                        distance_km * 1000.0
                    ),

                    toll=road["toll"],

                    ref=ref,

                    category=classify_road(
                        ref
                    ),

                    start_point=(
                        segment_start_point
                    ),

                    end_point=(
                        segment_end_point
                    ),
                )
            )

    return journey_segments


def process_vehicle_trace(vehicle_id):
    """
    Process the complete raw GPS journey.

    Pipeline:

        VehicleLocation
              ↓
        Valhalla map matching
              ↓
        matched_points + edges
              ↓
        MatchedLocation
              ↓
        JourneySegment
              ↓
        Toll / journey statistics

    Raw GPS records are never modified or deleted.
    """

    locations = list(
        VehicleLocation.objects
        .filter(
            vehicle_id=vehicle_id
        )
        .order_by("timestamp")
    )

    if len(locations) < 2:

        raise ValueError(
            "At least 2 GPS points "
            "are required."
        )

    # --------------------------------------------------
    # Valhalla processing.
    # --------------------------------------------------

    valhalla_data = (
        match_trace_with_valhalla(
            locations
        )
    )

    edges = valhalla_data.get(
        "edges",
        []
    )

    matched_points = (
        valhalla_data.get(
            "matched_points",
            []
        )
    )

    if not edges:

        raise ValhallaError(
            "Valhalla returned no "
            "matched edges."
        )

    if not matched_points:

        raise ValhallaError(
            "Valhalla returned no "
            "matched points."
        )

    if len(matched_points) != len(
        locations
    ):

        raise ValhallaError(
            "Valhalla returned "
            f"{len(matched_points)} "
            "matched points for "
            f"{len(locations)} GPS points."
        )

    # --------------------------------------------------
    # Validate matched points.
    # --------------------------------------------------

    for index, matched_point in enumerate(
        matched_points
    ):

        match_type = matched_point.get(
            "type"
        )

        if match_type == "unmatched":

            raise ValhallaError(
                f"GPS point {index} "
                "could not be matched."
            )

        longitude = matched_point.get(
            "lon"
        )

        latitude = matched_point.get(
            "lat"
        )

        if (
            longitude is None
            or latitude is None
        ):

            raise ValhallaError(
                f"GPS point {index} "
                "has no valid matched "
                "coordinates."
            )

        edge_index = matched_point.get(
            "edge_index"
        )

        if edge_index is None:

            raise ValhallaError(
                f"GPS point {index} "
                "has no edge_index."
            )

        edge_index = int(
            edge_index
        )

        if (
            edge_index < 0
            or edge_index >= len(edges)
        ):

            raise ValhallaError(
                f"GPS point {index} "
                f"references invalid "
                f"edge {edge_index}."
            )

    # --------------------------------------------------
    # Collect all OSM way IDs.
    # --------------------------------------------------

    way_ids = set()

    for edge in edges:

        way_id = edge.get(
            "way_id"
        )

        if way_id is not None:

            way_ids.add(
                int(way_id)
            )

    if not way_ids:

        raise ValhallaError(
            "Valhalla returned no "
            "OSM way IDs."
        )

    # --------------------------------------------------
    # Fetch OSM metadata from PostGIS.
    # --------------------------------------------------

    road_data = get_road_data(
        way_ids
    )

    missing_way_ids = (
        way_ids
        - set(road_data.keys())
    )

    if missing_way_ids:

        raise ValhallaError(
            "The following Valhalla "
            "way IDs were not found "
            "in PostGIS: "
            f"{sorted(missing_way_ids)}"
        )

    # --------------------------------------------------
    # Create MatchedLocation records.
    # --------------------------------------------------

    processed_locations = []

    for index, matched_point in enumerate(
        matched_points
    ):

        longitude = float(
            matched_point["lon"]
        )

        latitude = float(
            matched_point["lat"]
        )

        edge_index = int(
            matched_point["edge_index"]
        )

        edge = edges[
            edge_index
        ]

        way_id = edge.get(
            "way_id"
        )

        if way_id is None:

            raise ValhallaError(
                f"Valhalla edge "
                f"{edge_index} has no "
                "way_id."
            )

        way_id = int(
            way_id
        )

        road = road_data.get(
            way_id
        )

        if road is None:

            raise ValhallaError(
                f"No PostGIS data "
                f"for way {way_id}."
            )

        # --------------------------------------------------
        # Distance from previous matched point.
        #
        # This remains in MatchedLocation because
        # it is useful information, but toll totals
        # come from JourneySegment.
        # --------------------------------------------------

        if index == 0:

            distance_meters = 0.0

        else:

            distance_km = (
                calculate_segment_distance_km(
                    edges,
                    matched_points[
                        index - 1
                    ],
                    matched_point,
                )
            )

            distance_meters = (
                distance_km * 1000.0
            )

        ref = road["ref"]

        category = classify_road(
            ref
        )

        processed_locations.append(
            MatchedLocation(
                vehicle_id=vehicle_id,

                point=Point(
                    longitude,
                    latitude,
                    srid=4326,
                ),

                way_id=way_id,

                distance_from_previous_meters=(
                    distance_meters
                ),

                toll=road["toll"],

                ref=ref,

                category=category,

                timestamp=locations[
                    index
                ].timestamp,
            )
        )

    # --------------------------------------------------
    # Build JourneySegments.
    # --------------------------------------------------

    journey_segments = (
        build_journey_segments(
            locations,
            matched_points,
            edges,
            road_data,
        )
    )

    # --------------------------------------------------
    # Atomically replace all processed data.
    # --------------------------------------------------

    with transaction.atomic():

        MatchedLocation.objects.filter(
            vehicle_id=vehicle_id
        ).delete()

        JourneySegment.objects.filter(
            vehicle_id=vehicle_id
        ).delete()

        MatchedLocation.objects.bulk_create(
            processed_locations
        )

        JourneySegment.objects.bulk_create(
            journey_segments
        )

    # --------------------------------------------------
    # Summary from JourneySegment.
    # --------------------------------------------------

    total_distance_meters = sum(
        segment.distance_meters
        for segment in journey_segments
    )

    toll_distance_meters = sum(
        segment.distance_meters
        for segment in journey_segments
        if segment.toll
    )

    national_distance_meters = sum(
        segment.distance_meters
        for segment in journey_segments
        if (
            segment.toll
            and segment.category == "national"
        )
    )

    state_distance_meters = sum(
        segment.distance_meters
        for segment in journey_segments
        if (
            segment.toll
            and segment.category == "state"
        )
    )

    other_distance_meters = sum(
        segment.distance_meters
        for segment in journey_segments
        if (
            segment.toll
            and segment.category == "other"
        )
    )

    return {
        "vehicle_id": vehicle_id,

        "raw_points": len(
            locations
        ),

        "matched_points": len(
            processed_locations
        ),

        "journey_segments": len(
            journey_segments
        ),

        "total_distance_meters": round(
            total_distance_meters,
            2,
        ),

        "total_distance_km": round(
            total_distance_meters / 1000.0,
            3,
        ),

        "national_tolled_distance_meters": round(
            national_distance_meters,
            2,
        ),

        "national_tolled_distance_km": round(
            national_distance_meters / 1000.0,
            3,
        ),

        "state_tolled_distance_meters": round(
            state_distance_meters,
            2,
        ),

        "state_tolled_distance_km": round(
            state_distance_meters / 1000.0,
            3,
        ),

        "other_tolled_distance_meters": round(
            other_distance_meters,
            2,
        ),

        "other_tolled_distance_km": round(
            other_distance_meters / 1000.0,
            3,
        ),

        "toll_distance_meters": round(
            toll_distance_meters,
            2,
        ),

        "toll_distance_km": round(
            toll_distance_meters / 1000.0,
            3,
        ),
    }
