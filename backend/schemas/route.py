from typing import Any
from pydantic import BaseModel

#represents one upcoming traffic signal.
class SignalETA(BaseModel):
    signal_number: int
    latitude: float
    longitude: float
    route_distance_meters: float
    eta_seconds: float

#represents the complete route response.
class RouteResponse(BaseModel):
    distance_meters: float
    duration_seconds: float
    geometry: dict[str, Any]
    intersections: list[dict[str, Any]]
    junctions: list[dict[str, Any]]
    traffic_signals: list[dict[str, Any]]
    signal_etas: list[SignalETA]