"""
Live Pune traffic from TomTom, for the digital twin.

TomTom does not give individual cars (nobody publishes those); it gives
the live speed on each stretch of road. We probe the main roads of the
simulated area and the ambulance's route, then the simulation service
makes the simulated cars drive at those real speeds
(apply_to_simulation) and picks how much traffic to run.

Needs TOMTOM_API_KEY in .env (Traffic Flow API + Routing API enabled).
Requests are kept low: about 40 per snapshot, cached for 5 minutes.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import certifi
import requests

from simulation.sumo.sumo_bridge import CITY_SPEED_LIMIT, PROJECT_ROOT, sumo_to_latlon

logger = logging.getLogger(__name__)

FLOW_URL = (
    "https://api.tomtom.com/traffic/services/4/flowSegmentData/"
    "absolute/10/json"
)
ROUTING_URL = "https://api.tomtom.com/routing/1/calculateRoute/{points}/json"

CACHE_FILE = os.path.join(PROJECT_ROOT, "data", "live_traffic_cache.json")
CACHE_SECONDS = 300

# One probe per grid cell of main roads, plus one every so often along
# the ambulance route.
PROBE_GRID_METERS = 1000
ROUTE_PROBE_SPACING_METERS = 500
MAIN_ROAD_TYPES = ("highway.trunk", "highway.primary", "highway.secondary")

# Simulated cars drive at about this share of the speed limit
# (mean speedFactor in pune_vtypes.add.xml), so a road limit of
# real_speed / CAR_SPEED_FACTOR makes them drive at the real speed.
CAR_SPEED_FACTOR = 0.55
SLOWEST_LIMIT = 1.5  # m/s, so nothing stands completely still

# How congested the city is (live speed as a share of free-flow speed).
LIGHT_ABOVE = 0.8
NORMAL_ABOVE = 0.6


def api_key():
    return os.environ.get("TOMTOM_API_KEY") or None


def available():
    return api_key() is not None


# -----------------------------------------------------------------
# Where to probe
# -----------------------------------------------------------------

def probe_points(route_geometry=()):
    """[(latitude, longitude)] to ask TomTom about."""

    from simulation.sumo import route_planner

    net = route_planner._net()
    best_per_cell = {}

    for edge in net.getEdges():
        if edge.getType() not in MAIN_ROAD_TYPES:
            continue
        shape = edge.getShape()
        x, y = shape[len(shape) // 2]
        cell = (int(x // PROBE_GRID_METERS), int(y // PROBE_GRID_METERS))
        if (
            cell not in best_per_cell
            or edge.getLength() > best_per_cell[cell][0]
        ):
            best_per_cell[cell] = (edge.getLength(), x, y)

    points = []
    for _, x, y in best_per_cell.values():
        position = sumo_to_latlon(x, y)
        points.append((position["latitude"], position["longitude"]))

    # Along the ambulance route, roughly every ROUTE_PROBE_SPACING_METERS.
    travelled = ROUTE_PROBE_SPACING_METERS
    for (lat1, lon1), (lat2, lon2) in zip(route_geometry, route_geometry[1:]):
        travelled += _meters(lat1, lon1, lat2, lon2)
        if travelled >= ROUTE_PROBE_SPACING_METERS:
            points.append((lat2, lon2))
            travelled = 0.0

    return points


def _meters(lat1, lon1, lat2, lon2):
    from math import cos, hypot, radians
    dy = (lat2 - lat1) * 111_320
    dx = (lon2 - lon1) * 111_320 * cos(radians(lat1))
    return hypot(dx, dy)


# -----------------------------------------------------------------
# TomTom requests (cached)
# -----------------------------------------------------------------

def _load_cache():
    try:
        with open(CACHE_FILE) as file:
            return json.load(file)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w") as file:
        json.dump(cache, file)


def _flow_at(point):
    latitude, longitude = point
    response = requests.get(
        FLOW_URL,
        params={
            "point": f"{latitude:.5f},{longitude:.5f}",
            "unit": "KMPH",
            "key": api_key(),
        },
        timeout=10,
        verify=certifi.where(),
    )
    response.raise_for_status()
    data = response.json()["flowSegmentData"]

    return {
        "current_kmh": data["currentSpeed"],
        "free_kmh": data["freeFlowSpeed"],
        "closure": bool(data.get("roadClosure")),
        "confidence": data.get("confidence"),
        "coordinates": [
            [point_["latitude"], point_["longitude"]]
            for point_ in data["coordinates"]["coordinate"]
        ],
    }


def _segment_level(segment):
    if segment["closure"]:
        return "closed"
    share = segment["current_kmh"] / max(segment["free_kmh"], 1)
    if share >= LIGHT_ABOVE:
        return "free"
    if share >= 0.5:
        return "slow"
    return "jammed"


def fetch_snapshot(points):
    """
    Live flow for every probe point (cached per point for 5 minutes).
    Returns {"fetched_at", "segments", "speed_share", "level",
    "closures", "requests"}.
    """

    cache = _load_cache()
    now = time.time()
    results = {}
    to_fetch = []

    for point in points:
        key = f"{point[0]:.4f},{point[1]:.4f}"
        cached = cache.get(key)
        if cached and now - cached["time"] < CACHE_SECONDS:
            results[key] = cached["segment"]
        else:
            to_fetch.append((key, point))

    def fetch(item):
        key, point = item
        try:
            return key, _flow_at(point)
        except (requests.RequestException, KeyError, ValueError) as error:
            logger.warning("TomTom flow request failed at %s: %s", key, error)
            return key, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for key, segment in pool.map(fetch, to_fetch):
            if segment is not None:
                results[key] = segment
                cache[key] = {"time": now, "segment": segment}

    _save_cache(cache)

    # Nearby probes often return the same stretch of road: keep one.
    segments = {}
    for segment in results.values():
        first = segment["coordinates"][0] if segment["coordinates"] else None
        segments[json.dumps(first)] = segment

    segments = list(segments.values())
    for segment in segments:
        segment["level"] = _segment_level(segment)

    open_roads = [segment for segment in segments if not segment["closure"]]
    speed_share = (
        sum(segment["current_kmh"] for segment in open_roads)
        / max(sum(segment["free_kmh"] for segment in open_roads), 1)
    ) if open_roads else 1.0

    if speed_share >= LIGHT_ABOVE:
        level = "light"
    elif speed_share >= NORMAL_ABOVE:
        level = "normal"
    else:
        level = "heavy"

    logger.info(
        "Live traffic: %d road stretches (%d new requests), "
        "moving at %.0f%% of free-flow speed -> %s traffic.",
        len(segments), len(to_fetch), speed_share * 100, level,
    )

    return {
        "fetched_at": datetime.now().strftime("%H:%M"),
        "segments": segments,
        "speed_share": round(speed_share, 2),
        "level": level,
        "closures": sum(1 for segment in segments if segment["closure"]),
        "requests": len(to_fetch),
    }


def car_travel_minutes(start, end):
    """Live travel time (minutes) for a normal car from start to end."""

    points = f"{start[0]:.5f},{start[1]:.5f}:{end[0]:.5f},{end[1]:.5f}"
    try:
        response = requests.get(
            ROUTING_URL.format(points=points),
            params={"traffic": "true", "travelMode": "car", "key": api_key()},
            timeout=10,
            verify=certifi.where(),
        )
        response.raise_for_status()
        summary = response.json()["routes"][0]["summary"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as error:
        logger.warning("TomTom routing request failed: %s", error)
        return None

    return round(summary["travelTimeInSeconds"] / 60, 1)


# -----------------------------------------------------------------
# Apply to the running simulation
# -----------------------------------------------------------------

def apply_to_simulation(snapshot, traffic, road_cache):
    """
    Set the speed limit of the simulated roads under each TomTom stretch
    so simulated cars drive at the real live speed. Must run in the
    simulation thread. road_cache: dict reused between refreshes
    ({point key: road id}) so points are matched to roads only once.
    Returns the number of roads changed.
    """

    changed = set()

    for segment in snapshot["segments"]:
        live_speed = segment["current_kmh"] / 3.6
        limit = min(
            CITY_SPEED_LIMIT,
            max(SLOWEST_LIMIT, live_speed / CAR_SPEED_FACTOR),
        )

        # Every other point is plenty to find the roads underneath.
        for latitude, longitude in segment["coordinates"][::2]:
            key = f"{latitude:.5f},{longitude:.5f}"
            if key not in road_cache:
                road_cache[key] = traffic.snap_to_road(latitude, longitude)
            road_id = road_cache[key]

            if road_id and road_id not in changed:
                traffic.set_road_speed_limit(road_id, limit)
                changed.add(road_id)

    return len(changed)


def dashboard_summary(snapshot, car_minutes):
    """What the dashboard shows: summary + coloured road stretches."""

    def thin(coordinates, keep=25):
        step = max(1, len(coordinates) // keep)
        return coordinates[::step] + coordinates[-1:]

    return {
        "source": "TomTom",
        "fetched_at": snapshot["fetched_at"],
        "level": snapshot["level"],
        "speed_percent": round(snapshot["speed_share"] * 100),
        "closures": snapshot["closures"],
        "car_minutes": car_minutes,
        "segments": [
            {"level": segment["level"], "coordinates": thin(segment["coordinates"])}
            for segment in snapshot["segments"]
        ],
    }
