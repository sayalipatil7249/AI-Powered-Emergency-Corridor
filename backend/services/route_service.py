import certifi
import requests

from backend.services.junction_service import (
    extract_intersections,
    cluster_intersections,
    get_traffic_signals,
    match_traffic_signals,
)


OSRM_URL = "https://router.project-osrm.org/route/v1/driving"


#Raised when the OSRM routing service is down or cannot find a route.
#The API routes turn this into a 502 error instead of crashing with a 500.
class RoutingError(Exception):
    pass


def get_route(
    start_latitude: float,
    start_longitude: float,
    end_latitude: float,
    end_longitude: float,
):
    coordinates = (
        f"{start_longitude},{start_latitude};"
        f"{end_longitude},{end_latitude}"
    )

    url = f"{OSRM_URL}/{coordinates}"

    params = {
        "overview": "full",
        "geometries": "geojson",
        "steps": "true",
    }

    try:
        response = requests.get(
            url,
            params=params,
            timeout=10,
            verify=certifi.where(),
        )

        response.raise_for_status()

        data = response.json()

    except (requests.RequestException, ValueError) as error:
        raise RoutingError(
            f"OSRM routing service unavailable: {error}"
        ) from error

    if data.get("code") != "Ok":
        raise RoutingError(
            f"OSRM routing failed: {data.get('code')}"
        )

    routes = data.get("routes", [])

    if not routes:
        raise RoutingError(
            "OSRM did not return a route."
        )

    route = routes[0]

    intersections = extract_intersections(route["legs"])

    junctions = cluster_intersections(intersections,threshold_meters=30,)

    traffic_signals = get_traffic_signals(junctions)

    matched_junctions = match_traffic_signals(
        junctions,
        traffic_signals,
        threshold_meters=30,
    )

    return {
        "distance_meters": route["distance"],
        "duration_seconds": route["duration"],
        "geometry": route["geometry"],
        "intersections": intersections,
        "junctions": matched_junctions,
        "traffic_signals": traffic_signals,
    }