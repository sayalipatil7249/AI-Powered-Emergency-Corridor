"""
Several ambulances at once: send more ambulances, set each patient's
condition (the medic crew's priority), and read the priority log.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.routes.planning import Point, TripRequest, plan_trip
from backend.api.routes.simulation import trip_from_plan
from backend.database import SessionLocal
from backend.services.priority_log import list_changes
from backend.services.simulation_service import (
    MAX_AMBULANCES,
    SimulationNotRunning,
    demo_trip,
    simulation_service,
)
from corridor import priority
from simulation.sumo import route_planner


router = APIRouter(
    prefix="/fleet",
    tags=["Ambulances and priority"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ConditionUpdate(BaseModel):
    # corridor/priority.py, e.g. "cardiac_arrest"
    condition: str
    # Who set it (optional, kept in the log)
    changed_by: str | None = None
    # "crew" (the ambulance's medics) or "admin" (control room override)
    source: Literal["crew", "admin"] = "crew"


class CrossingRequest(BaseModel):
    condition: str | None = None


class AmbulanceRequest(BaseModel):
    # "trip": start -> hospital; "demo": the demo trip; "crossing": an
    # ambulance that meets Ambulance 1 at one of its signals (demo)
    kind: Literal["trip", "demo", "crossing"] = "trip"
    start: Point | None = None
    hospital: Point | None = None
    condition: str | None = None
    # The whole journey: a 108 ambulance from its station via the
    # patient (see planning.TripRequest).
    from_base: bool = False


class FleetStart(BaseModel):
    # Ambulance 1 first (a trip or the demo trip), then the others.
    ambulances: list[AmbulanceRequest]


def _run(action):
    """Run a simulation action; turn its errors into HTTP errors."""
    try:
        return action()
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except (ValueError, SimulationNotRunning, TimeoutError) as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.get("/conditions")
def get_conditions():
    """Patient conditions the crew can choose, most urgent first."""
    return priority.condition_list()


@router.get("/")
def get_fleet():
    """Every ambulance of the current run, with its priority, give-way
    instruction and the referee's decisions."""
    state = simulation_service.get_state()
    return {
        "ambulances": state.get("ambulances", []),
        "referee": state.get("referee"),
    }


def prepare_fleet(request: FleetStart):
    """Plan independent crew requests in acceptance order, without dispatch."""
    bookings = request.ambulances
    if not bookings:
        raise HTTPException(status_code=400, detail="Add at least one ambulance request.")
    if len(bookings) > MAX_AMBULANCES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_AMBULANCES} ambulances at once in this demo.",
        )
    if bookings[0].kind == "crossing":
        raise HTTPException(
            status_code=400,
            detail="Ambulance 1 needs its own trip; a crossing ambulance meets it.",
        )

    trips = []
    profiles = []
    used_entries = set()
    used_units = []  # 108 ambulances already given to earlier requests
    for number, booking in enumerate(bookings, 1):
        if booking.kind == "demo":
            trip = demo_trip()
        elif booking.kind == "trip":
            if booking.start is None or booking.hospital is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"Ambulance {number} needs a start and a hospital.",
                )
            request_trip = TripRequest(
                start=booking.start, hospital=booking.hospital,
                from_base=booking.from_base, condition=booking.condition,
            )
            trip = trip_from_plan(request_trip, plan_trip(request_trip, used_units))
            if trip.get("unit"):
                used_units.append(trip["unit"]["id"])
        else:
            trip = route_planner.crossing_trip_for_route(
                trips[0]["roads"], skip_entries=used_entries
            )
            if trip is None:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Ambulance {number}: no side road crosses Ambulance "
                        "1's route at a signal in its first 7 minutes. "
                        "Try a longer trip for Ambulance 1."
                    ),
                )
            used_entries.add(trip["crossing_entry"])
        try:
            condition = priority.check_condition(booking.condition or priority.DEFAULT_CONDITION)
            trip, profile = route_planner.coordinate_trip(
                trip, condition, profiles, f"Ambulance {number}"
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))
        trips.append(trip)
        profiles.append(profile)

    # Later requests can give an earlier crew a planned wait. Refresh every
    # displayed estimate from the final fleet, not just its acceptance time.
    from corridor.routing import schedule
    final = schedule(profiles)
    for trip, profile, delay in zip(trips, profiles, final['delays']):
        decision = trip['routing_decision']
        decision['estimated_wait_seconds'] = round(delay, 1)
        decision['estimated_seconds'] = round(profile['seconds'] + delay, 1)
        if not decision['changed']:
            decision['explanation'] = (
                f"Route kept. Expect ~{delay:.0f} s give-way."
                if delay else "Route kept. No delay expected."
            )
    return trips, profiles


@router.post("/preview")
def preview_fleet(request: FleetStart):
    trips, profiles = prepare_fleet(request)
    return route_planner.corridor_preview(trips, profiles)


@router.post("/start")
def start_fleet(request: FleetStart):
    """Dispatch the same deterministic routes shown by /preview."""
    trips, profiles = prepare_fleet(request)
    bookings = request.ambulances
    try:
        result = simulation_service.start(
            trips[0], bookings[0].condition,
            extra=[
                (trip, booking.condition)
                for trip, booking in zip(trips[1:], bookings[1:])
            ],
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return {
        **result,
        "ambulances": [
            {
                "label": f"Ambulance {number}",
                "start_name": trip["start_name"],
                "hospital_name": trip["hospital_name"],
                "crosses": trip.get("crosses"),
                "routing_decision": trip.get("routing_decision"),
            }
            for number, trip in enumerate(trips, 1)
        ],
    }


@router.post("/ambulances")
def add_ambulance(trip: TripRequest):
    """Send another ambulance on a planned trip (start point, hospital,
    and the patient's condition) while the simulation runs."""
    planned = plan_trip(trip)
    return _run(lambda: simulation_service.add_ambulance(
        trip_from_plan(trip, planned), trip.condition
    ))


@router.post("/ambulances/crossing")
def add_crossing_ambulance(request: CrossingRequest | None = None):
    """Demo: another ambulance that reaches one of the first ambulance's
    next signals from a side road at about the same time."""
    condition = request.condition if request else None
    return _run(lambda: simulation_service.add_crossing_ambulance(condition))


@router.put("/ambulances/{vehicle_id}/condition")
def set_condition(vehicle_id: str, update: ConditionUpdate):
    """The crew sets their patient's condition. Applies at once (no
    approval); every change is logged."""
    return _run(lambda: simulation_service.set_condition(
        vehicle_id, update.condition, update.changed_by, update.source
    ))


class PoliceCall(BaseModel):
    # "crew" (the ambulance's medics) or "control room" (Admin)
    called_by: Literal["crew", "control room"] = "crew"


@router.post("/ambulances/{vehicle_id}/call-police")
def call_police(vehicle_id: str, request: PoliceCall | None = None):
    """The ambulance is stuck: call the police station that can reach
    the jam fastest, now (at most once every 2 min per ambulance)."""
    called_by = request.called_by if request else "crew"
    return _run(lambda: simulation_service.call_police(vehicle_id, called_by))


@router.post("/ambulances/{vehicle_id}/hospital-declines")
def hospital_declines(vehicle_id: str):
    """Test: the ambulance's hospital can't take its patient any more
    (e.g. its cath lab just became busy). The 108 call centre finds the
    next suitable hospital and the ambulance is diverted at once."""
    return _run(lambda: simulation_service.hospital_declines(vehicle_id))


@router.get("/priority-log")
def get_priority_log(limit: int = 100, db: Session = Depends(get_db)):
    """Every priority setting, newest first (priority_changes table)."""
    return [
        {
            "trip_id": row.trip_id,
            "ambulance_id": row.ambulance_id,
            "ambulance_label": row.ambulance_label,
            "condition": row.condition,
            "condition_label": priority.CONDITIONS.get(row.condition, (row.condition,))[0],
            "level": row.level,
            "previous_condition": row.previous_condition,
            "source": row.source,
            "changed_by": row.changed_by,
            "simulation_time": row.simulation_time,
            "created_at": row.created_at,
        }
        for row in list_changes(db, limit)
    ]
