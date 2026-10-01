from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# Request statuses and delay reasons (backend/services/admin_service.py).
REQUEST_STATUSES = ("IN_PROGRESS", "COMPLETED", "CANCELLED", "FAILED")
DELAY_REASONS = (
    "NONE",            # on time
    "TRAFFIC",         # queues / jams on the route
    "BAD_ROUTE",       # the planned route had to be changed
    "ACCIDENT",        # a crash blocked the road
    "VEHICLE_ISSUE",   # set by an admin
    "DRIVER_DELAY",    # set by an admin
    "OTHER",
)

# Whether the AI pre-trip model (ai/pretrip.py) gave a planned time.
# Without one there is no delay, so the trip is neither on time nor late.
PLAN_STATUSES = (
    "OK",              # planned_seconds is the model's estimate
    "MODEL_MISSING",   # ai/models/pretrip.joblib not found (not trained)
    "NO_ESTIMATE",     # model loaded, but no estimate for this route
    "ERROR",           # the estimate raised an error
)


# One ambulance request (trip): when it was dispatched and arrived, how
# long it took against the plan, and why it was late. Written by the live
# simulation (backend/services/simulation_service.py); an admin can
# correct the status or delay reason (PATCH /admin/requests/{request_id}).
class AmbulanceRequest(Base):
    __tablename__ = "ambulance_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # e.g. "REQ-20260929-101123"
    request_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)

    ambulance_id: Mapped[str] = mapped_column(String(50), nullable=False)

    start_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    hospital_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # IN_PROGRESS -> COMPLETED, CANCELLED (stopped) or FAILED (error)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # light / normal / heavy
    traffic_level: Mapped[str | None] = mapped_column(String(10), nullable=True)

    distance_meters: Mapped[float | None] = mapped_column(Float, nullable=True)

    # The route planner's estimate for this traffic level
    planned_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # One of PLAN_STATUSES. NULL on rows recorded before it existed:
    # those have a plan exactly when planned_seconds is set.
    plan_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    dispatched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)

    arrived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Dispatch to arrival (simulated seconds)
    response_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # How much longer than planned (0 when on time or early)
    delay_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    delay_reason: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    # True when an admin set the reason (not overwritten automatically)
    delay_reason_manual: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Time spent standing still and number of stops on the way
    stopped_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    stops: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # "simulation" (recorded by the live demo) or "manual"
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="simulation")

    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # The planned route [[lat, lon], ...] and the path actually driven
    # [[lat, lon, seconds after dispatch, speed m/s], ...] (every few
    # seconds), for the admin trip page's map.
    route_geometry: Mapped[list | None] = mapped_column(JSON, nullable=True)

    track: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
