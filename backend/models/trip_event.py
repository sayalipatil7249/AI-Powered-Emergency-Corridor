from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# What happened on the way, per request, in time order: dispatch, stops
# (where and how long the ambulance stood still), signals switched green,
# police alerts, accidents, re-routes, the AI deadlock watch's decisions,
# arrival. Written when the trip ends
# (backend/services/admin_service.py); shown on the admin trip page and
# summed up per junction in the problem-junction report.
TRIP_EVENT_KINDS = (
    "DISPATCH", "STOP", "SIGNAL", "POLICE", "ACCIDENT", "REROUTE", "AI",
    "ARRIVAL", "END",
)


class TripEvent(Base):
    __tablename__ = "trip_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    request_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("ambulance_requests.request_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Seconds after dispatch
    seconds: Mapped[float] = mapped_column(Float, nullable=False)

    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    road_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # The junction involved (for a stop: the one it was waiting to enter)
    junction_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    junction_name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    junction_signal: Mapped[bool | None] = mapped_column(nullable=True)

    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)

    # How long it stood still (STOP events)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Extra data, e.g. the new route's points for a REROUTE
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
