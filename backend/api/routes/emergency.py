from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.schemas.emergency import EmergencyRequest
from backend.services.emergency_service import (
    create_emergency_request,
)
from backend.services.route_service import RoutingError


router = APIRouter(
    prefix="/emergencies",
    tags=["Emergency"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


@router.post("/")
def create_emergency(emergency_data: EmergencyRequest, db: Session = Depends(get_db),):
    try:
        result = create_emergency_request(
            db,
            emergency_data.ambulance_id,
            emergency_data.hospital_id,
            emergency_data.emergency_status,
        )
    except RoutingError as error:
        raise HTTPException(
            status_code=502,
            detail=str(error),
        )

    if "error" in result:
        raise HTTPException(
            status_code=404,
            detail=result["error"],
        )

    return result

