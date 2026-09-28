#bridge between our emergency-corridor logic and the simulated traffic lights.

from sqlalchemy.orm import Session
from geoalchemy2.elements import WKTElement

from backend.models.traffic_signal import TrafficSignal
from backend.schemas.traffic_signal import (
    TrafficSignalCreate,
    TrafficSignalUpdate,
)


def create_traffic_signal(db: Session, signal_data: TrafficSignalCreate,):

    signal = TrafficSignal(
        signal_id=signal_data.signal_id,
        latitude=signal_data.latitude,
        longitude=signal_data.longitude,
        location=WKTElement(
            f"POINT({signal_data.longitude} {signal_data.latitude})",
            srid=4326,
        ),
        state=signal_data.state,
    )

    db.add(signal)
    db.commit()
    db.refresh(signal)

    return signal


def get_traffic_signal(db: Session, signal_id: str,):
    return (
        db.query(TrafficSignal)
        .filter(
            TrafficSignal.signal_id == signal_id
        )
        .first()
    )


def update_traffic_signal_state(db: Session, signal_id: str,signal_data: TrafficSignalUpdate, ):
    signal = get_traffic_signal(db,signal_id,)

    if signal is None:
        return None

    signal.state = signal_data.state

    db.commit()
    db.refresh(signal)

    return signal