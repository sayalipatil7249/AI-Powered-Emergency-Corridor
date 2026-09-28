import asyncio
import json
import websockets

async def test_websocket():
    uri = "ws://127.0.0.1:8000/simulation/ws"
    print("Connecting to WebSocket...")
    async with websockets.connect(uri) as websocket:
        print("WebSocket connected.")
        for _ in range(10):
            message = await websocket.recv()
            state = json.loads(message)
            print(
                f"Time: {state.get('simulation_time')} | "
                f"Status: {state.get('status')}"
            )
            ambulance = state.get("ambulance")
            if ambulance:
                print(
                    f"Ambulance: "
                    f"{ambulance.get('vehicle_id')} | "
                    f"Speed: {ambulance.get('speed')}"
                )
            corridor = state.get("corridor", [])
            if corridor:
                print("Corridor:")
                for junction in corridor:
                    print(
                        f"  {junction['junction']} "
                        f"-> {junction['state']}"
                    )
            print("-" * 50)

if __name__ == "__main__":
    asyncio.run(test_websocket())