from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from geoalchemy2 import Geography

class Ambulance(Base):
    __tablename__ = "ambulances"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,)

    ambulance_id: Mapped[str] = mapped_column(String(50),unique=True,nullable=False,index=True,)

    latitude: Mapped[float] = mapped_column(Float,nullable=False,)

    longitude: Mapped[float] = mapped_column(Float,nullable=False,)

    #location is a PostGIS geographic POINT
    location: Mapped[object | None] = mapped_column(
        Geography(
            geometry_type="POINT",
            srid=4326,   #srid=4326 tells PostGIS that the coordinates use the standard GPS coordinate system.
        ),
        nullable=True,
    )

    speed: Mapped[float] = mapped_column(Float,default=0.0,)

    direction: Mapped[str | None] = mapped_column(String(50),nullable=True,)

    destination: Mapped[str | None] = mapped_column(String(255),nullable=True,)

    emergency_status: Mapped[bool] = mapped_column(Boolean,default=False,)