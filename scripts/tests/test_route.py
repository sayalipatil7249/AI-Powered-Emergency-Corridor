from backend.services.route_service import get_route


result = get_route(
    start_latitude=18.523,
    start_longitude=73.859,
    end_latitude=18.530,
    end_longitude=73.870,
)

print("Distance:", result["distance_meters"], "meters")
print("Duration:", result["duration_seconds"], "seconds")

print("\nCandidate Junctions:")

for index, junction in enumerate(
    result["junctions"],
    start=1,
):
    print(
        f"Junction {index}: "
        f"Latitude={junction['latitude']}, "
        f"Longitude={junction['longitude']}, "
        f"OSRM Points={len(junction['points'])}"
    )