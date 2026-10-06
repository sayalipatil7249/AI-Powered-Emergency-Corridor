from pydantic import BaseModel, ConfigDict


class HospitalCreate(BaseModel):
    hospital_id: str
    name: str
    latitude: float
    longitude: float
    address: str | None = None


class HospitalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hospital_id: str
    name: str
    latitude: float
    longitude: float
    address: str | None


class NearestHospitalResponse(BaseModel):
    ambulance_id: str
    hospital_id: str
    name: str
    latitude: float
    longitude: float
    address: str
    distance_meters: float