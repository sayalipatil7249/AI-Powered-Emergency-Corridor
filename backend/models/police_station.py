from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


# A police station or chowki in the simulated area, with the phone
# number that is called when a road near it jams ahead of an ambulance
# (backend/services/police_notifier.py).
#
# Stations come from OpenStreetMap (police_stations.json) and are kept
# in sync at start-up; the phone number is only ever set through the API
# (PUT /police-stations/{station_id}/phone), never overwritten by the sync.
class PoliceStation(Base):
    __tablename__ = "police_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # e.g. "osm-node-2289023583"
    station_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Marathi name, e.g. "फरासखाना पोलीस ठाणे"
    name_local: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # "Police station" or "Police chowki" (a small post)
    kind: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Street the station is on
    road_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    latitude: Mapped[float] = mapped_column(Float, nullable=False)

    longitude: Mapped[float] = mapped_column(Float, nullable=False)

    location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326),
        nullable=True,
    )

    # Contact number for police alerts, international format (+91...).
    # TESTING: only test phones - never a real police number.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
