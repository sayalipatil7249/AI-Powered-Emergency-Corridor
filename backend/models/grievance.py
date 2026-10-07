from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# SYSTEM: filed automatically by the app when something goes wrong.
# DRIVER: the ambulance crew (medic / driver), who use the app.
GRIEVANCE_ROLES = (
    "DRIVER", "CALL_CENTRE", "HOSPITAL", "POLICE", "USER", "OTHER", "SYSTEM",
)
GRIEVANCE_CATEGORIES = (
    "DELAY", "ROUTE", "HOSPITAL", "POLICE", "VEHICLE", "STAFF", "APP", "OTHER",
)
GRIEVANCE_PRIORITIES = ("LOW", "MEDIUM", "HIGH")
GRIEVANCE_STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED", "CLOSED")


# A complaint (ticket) raised by a user, driver, hospital or police
# officer, optionally about one ambulance request. Admins move it from
# OPEN to IN_PROGRESS to RESOLVED / CLOSED with a resolution note.
class Grievance(Base):
    __tablename__ = "grievances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # e.g. "GRV-00012"
    ticket_no: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)

    # The request it is about (ambulance_requests.request_id), if any.
    # Not a foreign key: a ticket may name a trip older than the log.
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)

    raised_by_role: Mapped[str] = mapped_column(String(20), nullable=False)

    raised_by_name: Mapped[str | None] = mapped_column(String(120), nullable=True)

    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="MEDIUM")

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN", index=True)

    subject: Mapped[str] = mapped_column(String(200), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)

    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
