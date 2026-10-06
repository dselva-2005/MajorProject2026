from django.contrib.gis.db import models


class VehicleLocation(models.Model):
    """
    Raw GPS data received from the vehicle.
    """

    vehicle_id = models.CharField(max_length=50)

    point = models.PointField(geography=True, srid=4326)

    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.vehicle_id} - {self.timestamp}"


class MatchedLocation(models.Model):
    """
    GPS data processed by Valhalla.

    This is the data that will eventually be used by:
    - Frontend map
    - Toll calculation
    - Journey analysis
    """

    vehicle_id = models.CharField(max_length=50)

    point = models.PointField(geography=True, srid=4326)

    # OSM way ID returned by Valhalla
    way_id = models.BigIntegerField()

    # Distance travelled from the previous
    # matched GPS point
    distance_from_previous_meters = models.FloatField(default=0.0)

    # Whether the matched road is a toll road
    toll = models.BooleanField(default=False)

    # OSM road reference
    # Examples: NH48, SH1, SPH
    ref = models.CharField(max_length=100, blank=True)

    # national / state / other
    category = models.CharField(max_length=20, default="other")

    # Original GPS timestamp
    timestamp = models.DateTimeField()

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.vehicle_id} - way {self.way_id} - {self.timestamp}"


class JourneySegment(models.Model):
    """
    A processed portion of the journey corresponding
    to a Valhalla edge span.
    """

    vehicle_id = models.CharField(max_length=50)

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    # Exact Valhalla graph edge.
    edge_index = models.IntegerField()

    # OSM way represented by the Valhalla edge.
    way_id = models.BigIntegerField()

    # Portion of the edge travelled.
    start_fraction = models.FloatField()
    end_fraction = models.FloatField()

    distance_meters = models.FloatField()

    toll = models.BooleanField(default=False)

    ref = models.CharField(
        max_length=100,
        blank=True
    )

    category = models.CharField(
        max_length=20,
        default="other"
    )

    start_point = models.PointField(
        geography=True,
        srid=4326
    )

    end_point = models.PointField(
        geography=True,
        srid=4326
    )

    class Meta:
        ordering = ["start_time", "edge_index"]

    def __str__(self):
        return (
            f"{self.vehicle_id} - "
            f"edge {self.edge_index} - "
            f"way {self.way_id} - "
            f"{self.distance_meters:.2f}m"
        )
