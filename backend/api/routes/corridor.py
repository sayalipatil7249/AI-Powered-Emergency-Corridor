from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal

from backend.services.ambulance_service import get_ambulance
from backend.services.emergency_service import get_active_emergency

from backend.services.eta_service import (
    calculate_ambulance_route_position,
    calculate_dynamic_signal_etas,
)

from backend.services.signal_service import update_signal_states


router = APIRouter(
    prefix="/corridors",
    tags=["Emergency Corridor"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


@router.get("/{ambulance_id}")
def get_emergency_corridor(
    ambulance_id: str,
    db: Session = Depends(get_db),
):

    ambulance = get_ambulance(
        db,
        ambulance_id,
    )

    if ambulance is None:
        raise HTTPException(
            status_code=404,
            detail="Ambulance not found",
        )

    emergency = get_active_emergency(
        db,
        ambulance_id,
    )

    if emergency is None:
        raise HTTPException(
            status_code=404,
            detail="Active emergency not found",
        )

    # Get the original saved route
    route_coordinates = emergency.route_geometry["coordinates"]

    # Find ambulance position on the saved route
    ambulance_position = calculate_ambulance_route_position(
        ambulance_latitude=ambulance.latitude,
        ambulance_longitude=ambulance.longitude,
        route_coordinates=route_coordinates,
    )

    ambulance_route_distance = (
        ambulance_position["travelled_distance_meters"]
    )

    # Calculate ETA from current ambulance position
    dynamic_signal_etas = calculate_dynamic_signal_etas(
        signal_etas=emergency.route_junctions,
        ambulance_route_distance=ambulance_route_distance,
        total_route_distance=emergency.route_distance_meters,
        total_route_duration=emergency.route_duration_seconds,
    )

    # Update corridor signal states
    corridor = update_signal_states(
        signal_etas=dynamic_signal_etas,
        ambulance_route_distance=ambulance_route_distance,
    )

    return {
        "emergency_id": emergency.emergency_id,
        "ambulance_id": ambulance.ambulance_id,
        "hospital_id": emergency.hospital_id,
        "ambulance_route_distance_meters": ambulance_route_distance,
        "corridor": corridor,
    }