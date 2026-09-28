from pydantic import BaseModel

class CorridorJunction(BaseModel):
    signal_number: int
    latitude: float
    longitude: float
    route_distance_meters: float
    eta_seconds: float
    state: str


class EmergencyCorridorResponse(BaseModel):
    ambulance_id: str
    hospital_id: str
    corridor: list[CorridorJunction]