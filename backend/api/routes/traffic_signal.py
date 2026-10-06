from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal

from backend.schemas.traffic_signal import (
    TrafficSignalCreate,
    TrafficSignalResponse,
    TrafficSignalUpdate,
)

from backend.services.traffic_signal_service import (
    create_traffic_signal,
    get_traffic_signal,
    update_traffic_signal_state,
)


router = APIRouter(
    prefix="/traffic-signals",
    tags=["Traffic Signals"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


@router.post("/",response_model=TrafficSignalResponse,)
def add_traffic_signal(signal_data: TrafficSignalCreate, db: Session = Depends(get_db), ):
    if get_traffic_signal(db, signal_data.signal_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Traffic signal already exists",
        )

    return create_traffic_signal(db, signal_data,)


@router.get("/{signal_id}",response_model=TrafficSignalResponse,)
def get_traffic_signal_by_id(signal_id: str,db: Session = Depends(get_db),):
    signal = get_traffic_signal(db,signal_id,)

    if signal is None:
        raise HTTPException(
            status_code=404,
            detail="Traffic signal not found",
        )

    return signal


@router.put("/{signal_id}/state", response_model=TrafficSignalResponse,)
def update_signal_state(
    signal_id: str,
    signal_data: TrafficSignalUpdate,
    db: Session = Depends(get_db),
):
    signal = update_traffic_signal_state(
        db,
        signal_id,
        signal_data,
    )

    if signal is None:
        raise HTTPException(
            status_code=404,
            detail="Traffic signal not found",
        )

    return signal