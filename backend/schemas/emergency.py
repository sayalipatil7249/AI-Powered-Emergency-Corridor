from pydantic import BaseModel

class EmergencyRequest(BaseModel):
    ambulance_id: str
    hospital_id: str
    emergency_status: bool = True