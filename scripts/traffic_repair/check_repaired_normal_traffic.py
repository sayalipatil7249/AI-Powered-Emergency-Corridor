import xml.etree.ElementTree as ET
import traci
import os

NET_FILE = (
    "simulation/sumo/pune_network_v2/"
    "expanded_network/expanded.net.xml.gz"
)

ROUTE_FILE = (
    "simulation/sumo/pune_network_v2/"
    "normal_traffic_expanded.rou.xml"
)

SUMO = os.path.join(
    os.environ["SUMO_HOME"],
    "bin",
    "sumo"
)

print("Loading expanded network...")

traci.start([
    SUMO,
    "--net-file",
    NET_FILE
])

network_edges = set(traci.edge.getIDList())

traci.close()

print("Loading repaired normal traffic...")

root = ET.parse(ROUTE_FILE).getroot()

vehicle_count = 0
invalid_vehicle_count = 0
invalid_edges = set()

for vehicle in root.findall("vehicle"):

    vehicle_count += 1

    vehicle_id = vehicle.get("id")
    route = vehicle.find("route")

    if route is None:
        continue

    edges = route.get("edges", "").split()

    vehicle_invalid = [
        edge for edge in edges
        if edge not in network_edges
    ]

    if vehicle_invalid:

        invalid_vehicle_count += 1
        invalid_edges.update(vehicle_invalid)

        print(f"\nVehicle: {vehicle_id}")
        print("Invalid edges:", vehicle_invalid)

print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)

print("Vehicles found:", vehicle_count)
print("Vehicles with invalid routes:", invalid_vehicle_count)
print("Unique invalid edges:", len(invalid_edges))

for edge in sorted(invalid_edges):
    print("INVALID:", edge)