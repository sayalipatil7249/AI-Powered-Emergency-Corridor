from fastapi import APIRouter, Depends , HTTPException
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.schemas.ambulance import AmbulanceCreate, AmbulanceResponse , AmbulanceUpdate
from backend.services.ambulance_service import create_ambulance , get_ambulance , update_ambulance


router = APIRouter(
    prefix="/ambulances",
    tags=["Ambulances"],
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()

#1.Create Ambulance(i.e.Register an ambulance) add ambulance
@router.post("/", response_model=AmbulanceResponse)
def add_ambulance(ambulance_data: AmbulanceCreate, db: Session = Depends(get_db),):
    if get_ambulance(db, ambulance_data.ambulance_id) is not None:
        raise HTTPException(
            status_code=409,
            detail="Ambulance already exists",
        )

    return create_ambulance(db, ambulance_data)

#2.Find Ambulance's Current Details 
@router.get("/{ambulance_id}", response_model=AmbulanceResponse)
def get_ambulance_by_id(ambulance_id: str,db: Session = Depends(get_db),):
    return get_ambulance(db, ambulance_id)

#3.Updating ambulance information
@router.put("/{ambulance_id}", response_model=AmbulanceResponse)
def update_ambulance_by_id(ambulance_id: str,ambulance_data: AmbulanceUpdate,db: Session = Depends(get_db),):
    ambulance = update_ambulance(db,ambulance_id,ambulance_data,)

    if ambulance is None:
        raise HTTPException(
            status_code=404,
            detail="Ambulance not found",
        )

    return ambulance