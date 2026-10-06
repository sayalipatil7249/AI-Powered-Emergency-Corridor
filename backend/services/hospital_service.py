from sqlalchemy import func, true
from sqlalchemy.orm import Session
from geoalchemy2.elements import WKTElement
from geoalchemy2 import Geography

from backend.models.hospital import Hospital
from backend.models.ambulance import Ambulance
from backend.schemas.hospital import HospitalCreate

#create new hospital
def create_hospital(db: Session, hospital_data: HospitalCreate,):
    hospital = Hospital(
        hospital_id=hospital_data.hospital_id,
        name=hospital_data.name,
        latitude=hospital_data.latitude,
        longitude=hospital_data.longitude,
        location=WKTElement(
            f"POINT({hospital_data.longitude} {hospital_data.latitude})",
            srid=4326,
        ),
        address=hospital_data.address,
    )

    db.add(hospital)
    db.commit()
    db.refresh(hospital)

    return hospital


#Get Hospital Information
def get_hospital(db: Session,hospital_id: str,):
    return (
        db.query(Hospital)
        .filter(Hospital.hospital_id == hospital_id)
        .first()
    )


#Find nearest Hospital
def get_nearest_hospital(db: Session, ambulance_id: str):
    distance = func.ST_Distance(
        Ambulance.location.cast(Geography),
        Hospital.location.cast(Geography),
    )

    result = (
        db.query(
            Ambulance.ambulance_id,
            Hospital.hospital_id,
            Hospital.name,
            Hospital.latitude,
            Hospital.longitude,
            Hospital.address,
            distance.label("distance_meters"),
        )
        .select_from(Ambulance)
        .join(Hospital, true())   #its acts like "CROSS JOIN hospitals"
        .filter(Ambulance.ambulance_id == ambulance_id)
        .order_by(distance.asc())
        .first()
    )

    return result
