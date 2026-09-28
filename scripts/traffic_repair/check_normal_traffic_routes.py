import xml.etree.ElementTree as ET
import sumolib

NET = "simulation/sumo/pune_network_v2/expanded_network/expanded.net.xml.gz"
ROUTES = "simulation/sumo/pune_network_v2/normal_traffic.rou.xml"

print("Loading expanded network...")
net = sumolib.net.readNet(NET)

valid_edges = {edge.getID() for edge in net.getEdges()}

print("Loading normal traffic routes...")
tree = ET.parse(ROUTES)
root = tree.getroot()

vehicle_count = 0
invalid_vehicle_count = 0
invalid_edges = set()

for vehicle in root.findall("vehicle"):

    vehicle_count += 1

    route = vehicle.find("route")

    if route is None:
        continue

    edges = route.get("edges", "").split()

    vehicle_invalid_edges = [
        edge_id
        for edge_id in edges
        if edge_id not in valid_edges
    ]

    if vehicle_invalid_edges:

        invalid_vehicle_count += 1

        print()
        print("Vehicle:", vehicle.get("id"))
        print("Invalid edges:", vehicle_invalid_edges)

        for edge_id in vehicle_invalid_edges:
            invalid_edges.add(edge_id)

print()
print("=" * 60)
print("SUMMARY")
print("=" * 60)

print("Vehicles found:", vehicle_count)
print("Vehicles with invalid routes:", invalid_vehicle_count)
print("Unique invalid edges:", len(invalid_edges))

print()

for edge_id in sorted(invalid_edges):
    print("INVALID:", edge_id)