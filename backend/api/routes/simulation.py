import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from backend.api.routes.planning import TripRequest, plan_trip
from backend.services.simulation_service import simulation_service


router = APIRouter(
    prefix="/simulation",
    tags=["Simulation"],
)


@router.post("/start")
def start_simulation(trip: TripRequest | None = None):
    """
    Start the simulation. With a start point and hospital, the ambulance
    takes the fastest route between them; without, the demo trip.
    """

    if trip is None:
        return simulation_service.start()

    planned = plan_trip(trip)

    return simulation_service.start({
        "roads": planned["roads"],
        "depart_position": planned["depart_position"],
        "arrival_position": planned["arrival_position"],
        "start_name": trip.start.name or "Selected start",
        "hospital_name": planned["hospital_name"],
        "start_point": [trip.start.latitude, trip.start.longitude],
        "hospital_point": [trip.hospital.latitude, trip.hospital.longitude],
    })


@router.post("/playback-speed")
def set_playback_speed(speed: int):
    """Watch the trip faster: 1, 2, 5 or 10 simulated seconds per second."""
    try:
        return simulation_service.set_playback_speed(speed)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


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