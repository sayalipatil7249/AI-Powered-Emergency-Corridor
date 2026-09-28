from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal

from backend.schemas.route import RouteResponse

from backend.services.ambulance_service import get_ambulance
from backend.services.hospital_service import get_hospital
from backend.services.route_service import get_route, RoutingError
from backend.services.eta_service import calculate_signal_etas


router = APIRouter(
    prefix="/routes",
    tags=["Routes"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.get("/{ambulance_id}/{hospital_id}", response_model=RouteResponse,)
def calculate_ambulance_route(ambulance_id: str, hospital_id: str,db: Session = Depends(get_db),):
    # Get ambulance information through Ambulance Service
    ambulance = get_ambulance(db,ambulance_id,)

    if ambulance is None:
        raise HTTPException(
            status_code=404,
            detail="Ambulance not found",
        )

    # Get hospital information through Hospital Service
    hospital = get_hospital(db, hospital_id,)

    if hospital is None:
        raise HTTPException(
            status_code=404,
            detail="Hospital not found",
        )

    # Calculate route using OSRM
    try:
        route = get_route(
            ambulance.latitude,
            ambulance.longitude,
            hospital.latitude,
            hospital.longitude,
        )
    except RoutingError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        )

    # Calculate ETA for traffic signals
    signal_etas = calculate_signal_etas(
        junctions=route["junctions"],
        route_coordinates=route["geometry"]["coordinates"],
        total_route_distance=route["distance_meters"],
        total_route_duration=route["duration_seconds"],
    )

    route["signal_etas"] = signal_etas

    return route