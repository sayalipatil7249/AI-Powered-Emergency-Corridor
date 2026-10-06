from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class Emergency(Base):
    __tablename__ = "emergencies"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    emergency_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    ambulance_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("ambulances.ambulance_id"),
        nullable=False,
    )

    hospital_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("hospitals.hospital_id"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="ACTIVE",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    #OSRM road path
    route_geometry: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    #Complete route distance
    route_distance_meters: Mapped[float | None] = mapped_column(
        nullable=True,
    )

    #Initial OSRM travel time
    route_duration_seconds: Mapped[float | None] = mapped_column(
        nullable=True,
    )

    #Detected junctions + traffic signals
    route_junctions: Mapped[list | None] = mapped_column(
        JSON,
        nullable=True,
    )