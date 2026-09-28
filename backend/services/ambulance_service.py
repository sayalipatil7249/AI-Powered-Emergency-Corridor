#this file creates the ambulance records 
from sqlalchemy.orm import Session

from backend.models.ambulance import Ambulance
from backend.schemas.ambulance import AmbulanceCreate , AmbulanceUpdate

from geoalchemy2.elements import WKTElement 
#GeoAlchemy converts the GPS coordinates i.e. longitude and latitude into a PostGIS geographic point for future spatial calculations.

#1.create ambulance 
def create_ambulance(db: Session,ambulance_data: AmbulanceCreate,):
    ambulance = Ambulance(
    ambulance_id=ambulance_data.ambulance_id,
        latitude=ambulance_data.latitude,
        longitude=ambulance_data.longitude,
        location=WKTElement(
            f"POINT({ambulance_data.longitude} {ambulance_data.latitude})",
            srid=4326,
        ),
        speed=ambulance_data.speed,
        direction=ambulance_data.direction,
        destination=ambulance_data.destination,
        emergency_status=ambulance_data.emergency_status,
    )

    db.add(ambulance)
    db.commit()
    db.refresh(ambulance)

    return ambulance


#2.get ambulance's current information
def get_ambulance(db: Session,ambulance_id: str,):
    return (
        db.query(Ambulance).filter(Ambulance.ambulance_id == ambulance_id).first())

#3. Update ambulance information
def update_ambulance(db: Session, ambulance_id: str,ambulance_data: AmbulanceUpdate,):
    ambulance = get_ambulance(db, ambulance_id)

    if ambulance is None:
        return None

    ambulance.latitude = ambulance_data.latitude
    ambulance.longitude = ambulance_data.longitude
    ambulance.speed = ambulance_data.speed
    ambulance.direction = ambulance_data.direction
    ambulance.destination = ambulance_data.destination

    if ambulance_data.emergency_status is not None:
        ambulance.emergency_status = ambulance_data.emergency_status

    ambulance.location = WKTElement(
        f"POINT({ambulance_data.longitude} {ambulance_data.latitude})",
        srid=4326,
    )

    db.commit()
    db.refresh(ambulance)

    return ambulance