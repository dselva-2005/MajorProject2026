import json

from django.contrib.gis.geos import Point
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import VehicleLocation, JourneySegment
from .services import process_vehicle_trace, ValhallaError


@csrf_exempt
def add_location(request, vehicle_id):

    if request.method != "POST":
        return JsonResponse(
            {"error": "Only POST requests are allowed."},
            status=405
        )

    try:
        data = json.loads(request.body)

        latitude = float(data["latitude"])
        longitude = float(data["longitude"])

    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return JsonResponse(
            {"error": "Invalid latitude/longitude."},
            status=400
        )

    point = Point(
        longitude,
        latitude,
        srid=4326
    )

    location = VehicleLocation.objects.create(
        vehicle_id=vehicle_id,
        point=point
    )

    return JsonResponse(
        {
            "status": "ok",
            "vehicle_id": vehicle_id,
            "latitude": latitude,
            "longitude": longitude,
            "timestamp": location.timestamp,
        }
    )


def get_path(request, vehicle_id):

    if request.method != "GET":
        return JsonResponse(
            {"error": "Only GET requests are allowed."},
            status=405
        )

    locations = (
        VehicleLocation.objects
        .filter(vehicle_id=vehicle_id)
        .order_by("timestamp")
    )

    coordinates = []

    for location in locations:
        coordinates.append(
            [
                location.point.y,
                location.point.x
            ]
        )

    return JsonResponse(
        {
            "vehicle_id": vehicle_id,
            "coordinates": coordinates
        }
    )


@csrf_exempt
def delete_locations(request, vehicle_id):

    if request.method != "DELETE":
        return JsonResponse(
            {"error": "Only DELETE requests are allowed."},
            status=405
        )

    # ---------------------------------------------------------
    # Delete both raw GPS data and processed journey data.
    #
    # The deletion is transactional so that we don't end up
    # with raw data deleted while processed data remains,
    # or vice versa.
    # ---------------------------------------------------------

    with transaction.atomic():

        deleted_locations, _ = (
            VehicleLocation.objects
            .filter(vehicle_id=vehicle_id)
            .delete()
        )

        deleted_segments, _ = (
            JourneySegment.objects
            .filter(vehicle_id=vehicle_id)
            .delete()
        )

    return JsonResponse(
        {
            "status": "ok",
            "vehicle_id": vehicle_id,
            "deleted_locations": deleted_locations,
            "deleted_journey_segments": deleted_segments,
        }
    )


def get_toll_distance(request, vehicle_id):

    if request.method != "GET":
        return JsonResponse(
            {"error": "Only GET requests are allowed."},
            status=405
        )

    # ---------------------------------------------------------
    # 1. Check whether raw GPS data exists.
    #
    # Raw GPS data is stored in VehicleLocation.
    # The frontend must never use this data directly.
    # ---------------------------------------------------------

    latest_location = (
        VehicleLocation.objects
        .filter(vehicle_id=vehicle_id)
        .order_by("-timestamp")
        .first()
    )

    if latest_location is None:
        return JsonResponse(
            {
                "vehicle_id": vehicle_id,
                "error": "No GPS data found for this vehicle."
            },
            status=404
        )

    # ---------------------------------------------------------
    # 2. Check the latest processed journey.
    #
    # JourneySegment is the authoritative processed data.
    #
    # If there are no processed segments, processing is required.
    # If raw GPS is newer than the latest processed segment,
    # processing is also required.
    # ---------------------------------------------------------

    latest_segment = (
        JourneySegment.objects
        .filter(vehicle_id=vehicle_id)
        .order_by("-end_time")
        .first()
    )

    processing_required = (
        latest_segment is None
        or latest_location.timestamp > latest_segment.end_time
    )

    # ---------------------------------------------------------
    # 3. Process raw GPS through Valhalla when required.
    #
    # This means the frontend can simply request:
    #
    # GET /api/vehicles/<vehicle_id>/toll-distance/
    #
    # and the backend makes sure the processed data is up to date.
    # ---------------------------------------------------------

    if processing_required:

        try:
            process_vehicle_trace(vehicle_id)

        except ValhallaError as error:
            return JsonResponse(
                {
                    "vehicle_id": vehicle_id,
                    "error": str(error)
                },
                status=502
            )

        except ValueError as error:
            return JsonResponse(
                {
                    "vehicle_id": vehicle_id,
                    "error": str(error)
                },
                status=400
            )

        except Exception as error:
            return JsonResponse(
                {
                    "vehicle_id": vehicle_id,
                    "error": f"Failed to process vehicle journey: {error}"
                },
                status=500
            )

    # ---------------------------------------------------------
    # 4. Get the latest processed journey.
    # ---------------------------------------------------------

    segments = (
        JourneySegment.objects
        .filter(vehicle_id=vehicle_id)
        .order_by("start_time", "edge_index")
    )

    if not segments.exists():
        return JsonResponse(
            {
                "vehicle_id": vehicle_id,
                "error": "No processed journey found for this vehicle."
            },
            status=404
        )

    # ---------------------------------------------------------
    # 5. Calculate distances from processed segments.
    # ---------------------------------------------------------

    total_distance = 0.0

    national_distance = 0.0
    state_distance = 0.0
    other_distance = 0.0

    processed_segments = []

    for segment in segments:

        distance_meters = segment.distance_meters
        distance_km = distance_meters / 1000.0

        total_distance += distance_km

        # -----------------------------------------------------
        # Toll distance
        # -----------------------------------------------------

        if segment.toll:

            if segment.category == "national":
                national_distance += distance_km

            elif segment.category == "state":
                state_distance += distance_km

            else:
                other_distance += distance_km

        # -----------------------------------------------------
        # Processed segment for frontend
        # -----------------------------------------------------

        processed_segments.append(
            {
                "edge_index": segment.edge_index,
                "way_id": segment.way_id,

                "start_fraction": segment.start_fraction,
                "end_fraction": segment.end_fraction,

                "distance_meters": round(
                    distance_meters,
                    3
                ),

                "distance_km": round(
                    distance_km,
                    6
                ),

                "toll": segment.toll,

                "ref": segment.ref,

                "category": segment.category,

                "start_point": [
                    segment.start_point.y,
                    segment.start_point.x
                ],

                "end_point": [
                    segment.end_point.y,
                    segment.end_point.x
                ],

                "start_time": segment.start_time.isoformat(),
                "end_time": segment.end_time.isoformat(),
            }
        )

    # ---------------------------------------------------------
    # 6. Total toll distance.
    # ---------------------------------------------------------

    total_tolled_distance = (
        national_distance
        + state_distance
        + other_distance
    )

    # ---------------------------------------------------------
    # 7. Return processed journey + toll distance.
    # ---------------------------------------------------------

    return JsonResponse(
        {
            "vehicle_id": vehicle_id,

            "total_distance_meters": round(
                total_distance * 1000.0,
                3
            ),

            "total_distance_km": round(
                total_distance,
                3
            ),

            "national_tolled_distance_meters": round(
                national_distance * 1000.0,
                3
            ),

            "national_tolled_distance_km": round(
                national_distance,
                3
            ),

            "state_tolled_distance_meters": round(
                state_distance * 1000.0,
                3
            ),

            "state_tolled_distance_km": round(
                state_distance,
                3
            ),

            "other_tolled_distance_meters": round(
                other_distance * 1000.0,
                3
            ),

            "other_tolled_distance_km": round(
                other_distance,
                3
            ),

            "total_tolled_distance_meters": round(
                total_tolled_distance * 1000.0,
                3
            ),

            "total_tolled_distance_km": round(
                total_tolled_distance,
                3
            ),

            "processed_segments": processed_segments,
        }
    )
