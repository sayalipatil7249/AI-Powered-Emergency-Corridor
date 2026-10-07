from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# What the system did to get one ambulance there fast: the optimal route
# used, how many signals were switched green (the corridor), police sent
# to roads without signals, re-routes. One row per finished request.
class RouteOptimizationLog(Base):
    __tablename__ = "route_optimization_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    request_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("ambulance_requests.request_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The fastest route from the route planner was driven (no re-route)
    optimal_route_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    signals_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Signals switched / held green for the ambulance
    signals_cleared: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # True when every signal on the route was cleared
    corridor_cleared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    police_alerts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Alerts where officers reached the road
    police_on_scene: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    reroutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    incidents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Arrived no later than planned (+ a small tolerance)
    fast_arrival: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Planned minus actual (positive = faster than planned)
    seconds_vs_plan: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
