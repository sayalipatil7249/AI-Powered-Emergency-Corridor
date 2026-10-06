import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.api.routes.planning import TripRequest, plan_trip
from backend.services.simulation_service import (
    SimulationNotRunning,
    simulation_service,
)


router = APIRouter(
    prefix="/simulation",
    tags=["Simulation"],
)


def trip_from_plan(trip, planned):
    """What the simulation needs from a planned trip."""
    return {
        "roads": planned["roads"],
        "depart_position": planned["depart_position"],
        "arrival_position": planned["arrival_position"],
        "start_name": trip.start.name or "Selected start",
        "hospital_name": planned["hospital_name"],
        "stretches": planned["signalless_stretches"],
        "police_along_route": planned["police_along_route"],
        # With the whole journey the trip starts at the base.
        "start_point": (
            [planned["base"]["latitude"], planned["base"]["longitude"]]
            if planned.get("base") else [trip.start.latitude, trip.start.longitude]
        ),
        "hospital_point": [trip.hospital.latitude, trip.hospital.longitude],
        # The whole journey: the base the ambulance leaves and the
        # patient it picks up (None for a straight trip).
        "base": planned.get("base"),
        "pickup": planned.get("pickup"),
        # The 108 ambulance sent (corridor/dispatch.py), with the journey.
        "unit": planned.get("unit"),
    }


@router.post("/start")
def start_simulation(trip: TripRequest | None = None, condition: str | None = None):
    """
    Start the simulation. With a start point and hospital, the ambulance
    takes the fastest route between them; without, the demo trip.
    The patient's condition (corridor/priority.py) comes from the trip,
    or from ?condition= for the demo trip.
    """

    try:
        if trip is None:
            return simulation_service.start(condition=condition)

        planned = plan_trip(trip)
        return simulation_service.start(
            trip_from_plan(trip, planned), trip.condition or condition
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/playback-speed")
def set_playback_speed(speed: int):
    """Watch the trip faster: 1, 2, 5 or 10 simulated seconds per second."""
    try:
        return simulation_service.set_playback_speed(speed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/incident")
def simulate_incident():
    """
    Demo: a crash blocks a road without signals ahead of the ambulance.
    The police watch notices the jam, alerts (phones) the station, and
    the officers clear the crash when they arrive.
    """
    try:
        return simulation_service.create_incident()
    except (ValueError, SimulationNotRunning, TimeoutError) as error:
        raise HTTPException(status_code=409, detail=str(error))


@router.get("/state")
def get_simulation_state():
    return simulation_service.get_state()


@router.post("/stop")
def stop_simulation():
    return simulation_service.stop()


@router.websocket("/ws")
async def simulation_websocket(websocket: WebSocket):
    """Send live simulation state to connected clients."""

    await websocket.accept()

    print("WebSocket client connected.")

    try:

        while True:

            state = simulation_service.get_state()

            await websocket.send_json(state)

            # Wait 0.5 s, but listen meanwhile: the browser closing or the
            # server shutting down (e.g. an auto-reload) ends the loop.
            # Just sleeping would keep a reload waiting forever.
            try:
                message = await asyncio.wait_for(websocket.receive(), 0.5)
            except asyncio.TimeoutError:
                continue

            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))

    except WebSocketDisconnect:

        print("WebSocket client disconnected.")

    except Exception as error:

        print(
            f"WebSocket error: {error}"
        )

        try:
            await websocket.close()
        except Exception:
            pass