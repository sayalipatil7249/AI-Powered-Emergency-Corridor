from pydantic import BaseModel, ConfigDict


class TrafficSignalCreate(BaseModel):
    signal_id: str
    latitude: float
    longitude: float
    state: str = "NORMAL"


class TrafficSignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    signal_id: str
    latitude: float
    longitude: float
    state: str


class TrafficSignalUpdate(BaseModel):
    state: str