from django.urls import path
from .views import (
    add_location,
    get_path,
    delete_locations,
    get_toll_distance,
)

urlpatterns = [
    path("vehicles/<str:vehicle_id>/locations/", add_location, name="add-location"),
    path(
        "vehicles/<str:vehicle_id>/locations/delete/",
        delete_locations,
        name="delete-locations",
    ),
    path("vehicles/<str:vehicle_id>/path/", get_path, name="get-path"),
    path(
        "vehicles/<str:vehicle_id>/toll-distance/",
        get_toll_distance,
        name="get-toll-distance",
    ),
]
