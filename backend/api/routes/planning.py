"""
Trip planning for the dashboard: find places, list hospitals and plan the
fastest ambulance route between them (simulation/sumo/route_planner.py).
"""

import math
import threading
import time
from functools import lru_cache

import certifi
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from simulation.sumo import route_planner
from backend.services.police_station_service import contacts_by_name
from backend.services.simulation_service import simulation_service
from corridor import dispatch, hospital_care, priority

router = APIRouter(
    prefix="/plan",
    tags=["Trip planning"],
)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Nominatim's usage policy: identify the app, at most 1 request per second.
NOMINATIM_HEADERS = {"User-Agent": "EmergencyCorridorDemo/1.0 (student project)"}
_nominatim_lock = threading.Lock()
_last_request_time = 0.0


class Point(BaseModel):
    latitude: float
    longitude: float
    name: str | None = None


class TripRequest(BaseModel):
    start: Point
    hospital: Point
    # The patient's condition (corridor/priority.py), e.g. "stroke";
    # only used when the trip is started.
    condition: str | None = None
    # True: the whole journey, as 108 does it - the call centre sends the
    # fastest free ambulance (corridor/dispatch.py) from its station, it
    # picks the patient up at `start`, then drives to `hospital`.
    # False: straight from `start` to `hospital`.
    from_base: bool = False


@router.get("/area")
def get_area():
    """The simulated area: trips must start and end inside it."""
    return route_planner.area()


def _distance_meters(latitude, longitude, item):
    return math.hypot(
        (item["latitude"] - latitude) * 111_320,
        (item["longitude"] - longitude) * 111_320 * math.cos(math.radians(latitude)),
    )


def _hospitals(condition=None, latitude=None, longitude=None, booked=(),
               government_only=False):
    """Hospitals with their type, ownership and whether they can take
    this patient now ("free": free units of what the patient needs);
    with a condition only those that can treat it; nearest first with a
    point. booked: hospitals already chosen by ambulances not yet started."""

    board = simulation_service.hospital_board
    resource = hospital_care.need(condition) if condition else "emergency"
    taken = {}
    for name in booked:
        taken[name] = taken.get(name, 0) + 1
    result = []
    for item in route_planner.hospitals():
        if condition and not hospital_care.can_treat(item["name"], condition):
            continue
        entry = {**item, **hospital_care.describe(item["name"])}
        if government_only and entry["ownership"] != "government":
            continue
        entry["need"] = resource
        entry["need_label"] = hospital_care.RESOURCES[resource]
        entry["free"] = board.free(item["name"], resource, taken.get(item["name"], 0))
        if latitude is not None and longitude is not None:
            entry["distance_meters"] = round(_distance_meters(latitude, longitude, item))
        result.append(entry)
    if latitude is not None and longitude is not None:
        result.sort(key=lambda entry: entry["distance_meters"])
    return result


@router.get("/hospitals")
def get_hospitals(
    condition: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    government_only: bool = False,
):
    """Hospitals inside the simulated area, with their type, ownership
    and whether they can take the patient now. With a patient condition:
    only the hospitals that can treat it; with a point: nearest first."""
    if condition:
        try:
            priority.check_condition(condition)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
    return _hospitals(condition, latitude, longitude, government_only=government_only)


class HospitalCall(BaseModel):
    latitude: float
    longitude: float
    condition: str
    # Hospitals already chosen by ambulances in the list (not started yet)
    booked: list[str] = []
    # 108's default: government hospitals only (free treatment).
    government_only: bool = False


# Hospitals called, at most: the nearest suitable ones by straight line,
# then ranked by driving time.
CALL_CANDIDATES = 6


@router.post("/call-hospitals")
def call_hospitals(call: HospitalCall):
    """The 108 call centre pre-alerts hospitals: the nearest ones that
    can treat this patient, by driving time, until one can take the
    patient now (its cath lab, ICU bed, trauma team... is free).
    Returns {"calls": [{"name", "type_label", "ownership_label",
    "drive_minutes", "need_label", "answer"}], "hospital": the one that
    accepted, or None}."""

    try:
        priority.check_condition(call.condition)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    candidates = _hospitals(call.condition, call.latitude, call.longitude, call.booked,
                            government_only=call.government_only)
    candidates = candidates[:CALL_CANDIDATES]
    for entry in candidates:
        seconds = route_planner.drive_seconds(
            call.latitude, call.longitude, entry["latitude"], entry["longitude"]
        )
        entry["drive_minutes"] = round(seconds / 60, 1) if seconds is not None else None
    candidates.sort(key=lambda entry: (entry["drive_minutes"] is None, entry["drive_minutes"] or 0))

    calls, accepted = [], None
    for entry in candidates:
        if entry["drive_minutes"] is None:
            continue
        answer = "accepted" if entry["free"] > 0 else "declined"
        calls.append({
            "name": entry["name"],
            "type_label": entry["type_label"],
            "ownership_label": entry["ownership_label"],
            "drive_minutes": entry["drive_minutes"],
            "need_label": entry["need_label"],
            "answer": answer,
        })
        if answer == "accepted":
            accepted = entry
            break
    return {"calls": calls, "hospital": accepted}


def dispatch_unit(latitude, longitude, condition, busy=()):
    """108 dispatch for a patient: (unit or None, rows); see
    corridor/dispatch.py. busy: unit ids already taken (besides the
    ambulances on the road)."""

    condition = condition or priority.DEFAULT_CONDITION
    level = TRAFFIC_LEVEL_INDEX.get(
        (simulation_service.live_info or {}).get("level", "normal"), 1
    )
    return dispatch.choose(
        simulation_service.unit_fleet(),
        set(busy) | simulation_service.busy_units(),
        priority.level(condition) == 1,
        latitude, longitude,
        lambda unit: route_planner.drive_estimate(
            unit["latitude"], unit["longitude"], latitude, longitude, level
        ),
    )


# ai.pretrip.TRAFFIC_LEVELS order.
TRAFFIC_LEVEL_INDEX = {"light": 0, "normal": 1, "heavy": 2}


@router.get("/police")
def get_police_stations():
    """Police stations that can be sent to clear a jam for the ambulance,
    with their contact number from the database (partly hidden)."""
    phones = contacts_by_name()
    return [
        {
            **{
                key: station[key]
                for key in ("name", "name_local", "kind", "road_name",
                            "latitude", "longitude")
            },
            "phone": phones.get(station["name"]),
        }
        for station in route_planner.police_stations()
    ]


@lru_cache(maxsize=256)
def _search(query):
    global _last_request_time

    bounds = route_planner.area()

    with _nominatim_lock:
        wait = 1.0 - (time.time() - _last_request_time)
        if wait > 0:
            time.sleep(wait)

        response = requests.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "limit": 6,
                "countrycodes": "in",
                # Only places inside the simulated area.
                "viewbox": (
                    f"{bounds['west']},{bounds['north']},"
                    f"{bounds['east']},{bounds['south']}"
                ),
                "bounded": 1,
            },
            headers=NOMINATIM_HEADERS,
            timeout=10,
            verify=certifi.where(),
        )
        _last_request_time = time.time()

    response.raise_for_status()

    return tuple(
        (
            # Keep the first parts of the address: "Shaniwar Wada, Budhwar Peth"
            ", ".join(place["display_name"].split(", ")[:2]),
            float(place["lat"]),
            float(place["lon"]),
        )
        for place in response.json()
    )


@lru_cache(maxsize=256)
def geocode_pune(query):
    """A place anywhere in Pune (not only the simulated area), for
    telling the operator that a place exists but is outside the map:
    (name, latitude, longitude) or None."""
    global _last_request_time
    with _nominatim_lock:
        wait = 1.0 - (time.time() - _last_request_time)
        if wait > 0:
            time.sleep(wait)
        try:
            response = requests.get(
                NOMINATIM_URL,
                params={"q": f"{query}, Pune", "format": "jsonv2", "limit": 1,
                        "countrycodes": "in"},
                headers=NOMINATIM_HEADERS, timeout=10, verify=certifi.where(),
            )
            response.raise_for_status()
            results = response.json()
        except requests.RequestException:
            return None
        finally:
            _last_request_time = time.time()
    if not results:
        return None
    place = results[0]
    return (", ".join(place["display_name"].split(", ")[:2]),
            float(place["lat"]), float(place["lon"]))


@router.get("/search")
def search_places(q: str):
    """Places in the simulated area matching a name: streets, hospitals
    and police stations by the start of each word ("bund gard" works),
    then OpenStreetMap places (whole words only)."""

    query = q.strip()
    if len(query) < 3:
        return []

    places = {
        place["name"]: place for place in route_planner.search_places(query)
    }

    try:
        results = _search(query.lower())
    except requests.RequestException:
        results = ()  # the local names still answer

    # The same place is often listed more than once (e.g. node + building).
    for name, latitude, longitude in results:
        # The search box is a rectangle; keep places inside the area itself.
        if not route_planner.inside_area(latitude, longitude):
            continue
        places.setdefault(
            name, {"name": name, "latitude": latitude, "longitude": longitude}
        )

    return list(places.values())[:8]


@router.get("/where")
def place_at(latitude: float, longitude: float):
    """What is at a point on the map: a hospital or police station
    nearby, otherwise the nearest named street."""
    return route_planner.place_at(latitude, longitude)


def plan_trip(trip: TripRequest, busy_units=()):
    """Plan a trip or raise a 400 with a readable reason. With from_base
    the 108 call centre first picks the ambulance (not one of
    busy_units); the plan then has "unit" and "dispatch" (the ambulances
    considered)."""

    try:
        if trip.from_base:
            unit, rows = dispatch_unit(
                trip.start.latitude, trip.start.longitude, trip.condition, busy_units
            )
            if unit is None:
                raise route_planner.PlanningError(
                    "No 108 ambulance is free to reach the patient."
                )
            plan = route_planner.plan_journey(
                trip.start.latitude,
                trip.start.longitude,
                trip.hospital.latitude,
                trip.hospital.longitude,
                hospital_name=trip.hospital.name or "Hospital",
                pickup_name=trip.start.name or "Patient",
                base={
                    "name": f"{unit['id']} ({unit['kind']}) "
                    + ("on its way back" if unit.get("status") == "returning"
                       else f"at {unit['station']}"),
                    "latitude": unit["latitude"],
                    "longitude": unit["longitude"],
                },
            )
            plan["unit"] = {
                "id": unit["id"],
                "kind": unit["kind"],
                "kind_label": dispatch.KIND_LABELS[unit["kind"]],
                "station": unit["station"],
                "returning": unit.get("status") == "returning",
            }
            plan["dispatch"] = rows
            return plan
        return route_planner.plan_route(
            trip.start.latitude,
            trip.start.longitude,
            trip.hospital.latitude,
            trip.hospital.longitude,
            hospital_name=trip.hospital.name or "Hospital",
        )
    except route_planner.PlanningError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/route")
def plan_route(trip: TripRequest):
    """The fastest ambulance route from the start point to the hospital,
    with the police stations along it and their contact numbers."""
    plan = plan_trip(trip)
    phones = contacts_by_name()
    for station in plan.get("police_along_route", []):
        station["phone"] = phones.get(station["name"])
    return plan
