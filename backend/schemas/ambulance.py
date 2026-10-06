from pydantic import BaseModel, ConfigDict


class AmbulanceCreate(BaseModel):
    ambulance_id: str
    latitude: float
    longitude: float
    speed: float = 0.0
    direction: str | None = None
    destination: str | None = None
    emergency_status: bool = False


class AmbulanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ambulance_id: str
    latitude: float
    longitude: float
    speed: float
    direction: str | None
    destination: str | None
    emergency_status: bool


class AmbulanceUpdate(BaseModel):
    latitude: float
    longitude: float
    speed: float = 0.0
    direction: str | None = None
    destination: str | None = None
    emergency_status: bool | None = None