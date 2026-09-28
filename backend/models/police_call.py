from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# One police alert and its phone call: "road X jammed ahead of the
# ambulance - station Y was phoned". Written when the alert is raised
# (corridor/police_watch.py) and updated as the call rings / is answered
# and as the officers arrive and finish.
class PoliceCall(Base):
    __tablename__ = "police_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # e.g. "trip601-S3-656"
    alert_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)

    station_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    road: Mapped[str | None] = mapped_column(String(255), nullable=True)

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    ambulance_eta_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    police_eta_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ALERTED -> EN_ROUTE -> ON_SCENE -> PASSED, or CANCELLED
    alert_status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Number called, partly hidden (+9198XXXXXX10), and where it came from:
    # "station" (police_stations.phone) or "default" (POLICE_ALERT_PHONE).
    phone_called: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # calling, ringing, in-progress, completed, no-answer, busy, failed,
    # or off / no_number / limit when no call was made
    call_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    call_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    call_detail: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # Stopped vehicles on the road when officers arrived / left (0-1)
    stopped_on_arrival: Mapped[float | None] = mapped_column(Float, nullable=True)
    stopped_after: Mapped[float | None] = mapped_column(Float, nullable=True)

    vehicles_waved: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
