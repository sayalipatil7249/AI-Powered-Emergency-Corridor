#Which signal is this, where is it, and what state is it currently in?

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from geoalchemy2 import Geography

from backend.database import Base


class TrafficSignal(Base):
    __tablename__ = "traffic_signals"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    signal_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    latitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    longitude: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    location: Mapped[object | None] = mapped_column(
        Geography(
            geometry_type="POINT",
            srid=4326,
        ),
        nullable=True,
    )

    state: Mapped[str] = mapped_column(
        String(30),
        default="NORMAL",
        nullable=False,
    )