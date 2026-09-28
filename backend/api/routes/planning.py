"""
Trip planning for the dashboard: find places, list hospitals and plan the
fastest ambulance route between them (simulation/sumo/route_planner.py).
"""

import threading
import time
from functools import lru_cache

import certifi
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from simulation.sumo import route_planner
from backend.services.police_station_service import contacts_by_name

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


@router.get("/area")
def get_area():
    """The simulated area: trips must start and end inside it."""
    return route_planner.area()


@router.get("/hospitals")
def get_hospitals():
    """Hospitals inside the simulated area."""
    return route_planner.hospitals()


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


@router.get("/search")
def search_places(q: str):
    """Places in the simulated area matching a name (OpenStreetMap)."""

    query = q.strip()
    if len(query) < 3:
        return []

    try:
        results = _search(query.lower())
    except requests.RequestException as error:
        raise HTTPException(
            status_code=502,
            detail=f"Place search is unavailable: {error}",
        )

    # The same place is often listed more than once (e.g. node + building).
    places = {}
    for name, latitude, longitude in results:
        # The search box is a rectangle; keep places inside the area itself.
        if not route_planner.inside_area(latitude, longitude):
            continue
        places.setdefault(
            name, {"name": name, "latitude": latitude, "longitude": longitude}
        )

    return list(places.values())


def plan_trip(trip: TripRequest):
    """Plan a trip or raise a 400 with a readable reason."""

    try:
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
