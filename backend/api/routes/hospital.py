from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.schemas.hospital import (
    HospitalCreate,
    HospitalResponse,
    NearestHospitalResponse,
)

from backend.services.hospital_service import (
    create_hospital,
    get_hospital,
    get_nearest_hospital,
)


router = APIRouter(
    prefix="/hospitals",
    tags=["Hospitals"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

#create hospital
@router.post("/", response_model=HospitalResponse)
def add_hospital(hospital_data: HospitalCreate, db: Session = Depends(get_db),):
    if get_hospital(db, hospital_data.hospital_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Hospital already exists",
        )

    return create_hospital(db, hospital_data)

#find nearest hospital
@router.get("/nearest/{ambulance_id}", response_model=NearestHospitalResponse,)
def find_nearest_hospital(ambulance_id: str,db: Session = Depends(get_db),):
    hospital = get_nearest_hospital(db, ambulance_id)

    if hospital is None:
        raise HTTPException(
            status_code=404,
            detail="Ambulance or hospital not found",
        )

    return hospital


# Get hospital information by hospital ID
@router.get("/{hospital_id}", response_model=HospitalResponse)
def get_hospital_by_id(hospital_id: str,db: Session = Depends(get_db),):
    hospital = get_hospital(db, hospital_id)

    if hospital is None:
        raise HTTPException(
            status_code=404,
            detail="Hospital not found",
        )

    return hospital