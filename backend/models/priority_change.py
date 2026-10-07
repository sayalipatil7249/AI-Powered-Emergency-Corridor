from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# Every patient-priority setting of an ambulance: the dispatcher's at the
# start of a trip, and each change by the medic crew. Changes apply at
# once (no approval); this log is how misuse can be spotted afterwards.
class PriorityChange(Base):
    __tablename__ = "priority_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # e.g. "trip600"
    trip_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # e.g. "ambulance_02", and "Ambulance 2"
    ambulance_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    ambulance_label: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # corridor/priority.py: condition id and its level (1 = most urgent)
    condition: Mapped[str] = mapped_column(String(50), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_condition: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # "dispatch" (set when the trip started), "crew", or "admin"
    # (control room override)
    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # Name the crew member gave, if any
    changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    simulation_time: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
