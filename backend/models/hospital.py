from sqlalchemy import Integer, String, Float
from sqlalchemy.orm import Mapped, mapped_column
from geoalchemy2 import Geography

from backend.database import Base


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[int] = mapped_column(Integer,primary_key=True,index=True,)

    hospital_id: Mapped[str] = mapped_column(String(50),unique=True,nullable=False,index=True,)

    name: Mapped[str] = mapped_column(String(255),nullable=False,)

    latitude: Mapped[float] = mapped_column(Float,nullable=False,)

    longitude: Mapped[float] = mapped_column(Float,nullable=False,)

    location: Mapped[object | None] = mapped_column(
        Geography(
            geometry_type="POINT",
            srid=4326,
        ),
        nullable=True,
    )

    address: Mapped[str | None] = mapped_column(String(500),nullable=True,)