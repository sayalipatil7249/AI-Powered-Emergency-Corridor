from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.services.police_station_service import (
    get_station,
    list_calls,
    list_stations,
    mask_phone,
    set_phone,
    station_dict,
)


router = APIRouter(
    prefix="/police-stations",
    tags=["Police stations"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


class PhoneUpdate(BaseModel):
    # International format, e.g. "+919876543210"; null removes the number.
    phone: str | None = None


# All police stations with their contact numbers (partly hidden)
@router.get("/")
def get_police_stations(db: Session = Depends(get_db)):
    return [station_dict(row) for row in list_stations(db)]


# Every police alert and phone call, newest first
@router.get("/calls")
def get_police_calls(limit: int = 50, db: Session = Depends(get_db)):
    return [
        {
            "alert_id": row.alert_id,
            "station": row.station_name,
            "road": row.road,
            "alert_status": row.alert_status,
            "phone_called": row.phone_called,
            "phone_source": row.phone_source,
            "call_status": row.call_status,
            "call_detail": row.call_detail,
            "ambulance_eta_seconds": row.ambulance_eta_seconds,
            "police_eta_seconds": row.police_eta_seconds,
            "stopped_on_arrival": row.stopped_on_arrival,
            "stopped_after": row.stopped_after,
            "vehicles_waved": row.vehicles_waved,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in list_calls(db, min(max(limit, 1), 500))
    ]


# One station
@router.get("/{station_id}")
def get_police_station(station_id: str, db: Session = Depends(get_db)):
    row = get_station(db, station_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Police station not found")
    return station_dict(row)


# Save a station's contact number. TESTING: only test phones - never a
# real police number (a test call would be a false emergency).
@router.put("/{station_id}/phone")
def update_police_phone(
    station_id: str,
    update: PhoneUpdate,
    db: Session = Depends(get_db),
):
    try:
        row = set_phone(db, station_id, update.phone)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    if row is None:
        raise HTTPException(status_code=404, detail="Police station not found")

    result = station_dict(row)
    result["message"] = (
        f"Saved {mask_phone(row.phone)} for {row.name}."
        if row.phone else f"Removed the number of {row.name}."
    )
    return result
