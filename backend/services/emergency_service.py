from datetime import datetime

from sqlalchemy.orm import Session

from backend.models.emergency import Emergency

from backend.services.ambulance_service import get_ambulance
from backend.services.hospital_service import get_hospital
from backend.services.route_service import get_route
from backend.services.eta_service import calculate_signal_etas


def create_emergency_request(
    db: Session,
    ambulance_id: str,
    hospital_id: str,
    emergency_status: bool,
):
    ambulance = get_ambulance(db, ambulance_id)

    if ambulance is None:
        return {"error": "Ambulance not found"}

    hospital = get_hospital(db, hospital_id)

    if hospital is None:
        return {"error": "Hospital not found"}

    # Calculate the route when the emergency starts
    route = get_route(
        ambulance.latitude,
        ambulance.longitude,
        hospital.latitude,
        hospital.longitude,
    )

    # Calculate initial ETA for every traffic signal
    signal_etas = calculate_signal_etas(
        junctions=route["junctions"],
        route_coordinates=route["geometry"]["coordinates"],
        total_route_distance=route["distance_meters"],
        total_route_duration=route["duration_seconds"],
    )

    # Update ambulance emergency information
    ambulance.destination = hospital.name
    ambulance.emergency_status = emergency_status

    # Close any older ACTIVE emergency for this ambulance,
    # so only the newest trip is ACTIVE.
    now = datetime.now()

    older_emergencies = (
        db.query(Emergency)
        .filter(
            Emergency.ambulance_id == ambulance.ambulance_id,
            Emergency.status == "ACTIVE",
        )
        .all()
    )

    for older_emergency in older_emergencies:
        older_emergency.status = "CANCELLED"
        older_emergency.completed_at = now

    # Create emergency record with the saved route.
    # The timestamp keeps emergency_id unique, so the same
    # ambulance can have more than one emergency.
    emergency = Emergency(
        emergency_id=f"E-{ambulance.ambulance_id}-{now:%Y%m%d%H%M%S%f}",
        ambulance_id=ambulance.ambulance_id,
        hospital_id=hospital.hospital_id,
        status="ACTIVE",
        route_geometry=route["geometry"],
        route_distance_meters=route["distance_meters"],
        route_duration_seconds=route["duration_seconds"],
        route_junctions=signal_etas,
    )

    db.add(emergency)

    db.commit()
    db.refresh(ambulance)
    db.refresh(emergency)

    return {
        "emergency_id": emergency.emergency_id,
        "ambulance_id": ambulance.ambulance_id,
        "hospital_id": hospital.hospital_id,
        "hospital_name": hospital.name,
        "emergency_status": ambulance.emergency_status,
        "route_distance_meters": emergency.route_distance_meters,
        "route_duration_seconds": emergency.route_duration_seconds,
        "signal_count": len(signal_etas),
    }

#Which active emergency and saved route belong to ambulance id?
def get_active_emergency(
    db: Session,
    ambulance_id: str,
):
    return (
        db.query(Emergency)
        .filter(
            Emergency.ambulance_id == ambulance_id,
            Emergency.status == "ACTIVE",
        )
        .order_by(Emergency.created_at.desc())
        .first()
    )