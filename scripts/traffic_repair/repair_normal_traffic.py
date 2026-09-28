import xml.etree.ElementTree as ET
import traci
import os

INPUT_FILE = "simulation/sumo/pune_network_v2/normal_traffic.rou.xml"
OUTPUT_FILE = "simulation/sumo/pune_network_v2/normal_traffic_expanded.rou.xml"

SUMO = os.path.join(
    os.environ["SUMO_HOME"],
    "bin",
    "sumo"
)

NET_FILE = (
    "simulation/sumo/pune_network_v2/"
    "expanded_network/expanded.net.xml.gz"
)

# Vehicle ID -> original start edge, destination edge
REPAIR_VEHICLES = {
    "17": ("-817794772#0", "211082575#1"),
    "28": ("855240397", "218681352#4"),
    "51": ("-1524249975", "1126679747#10"),
}


print("Loading expanded network...")

traci.start([
    SUMO,
    "--net-file",
    NET_FILE
])

replacement_routes = {}

for vehicle_id, (start_edge, destination_edge) in REPAIR_VEHICLES.items():

    result = traci.simulation.findRoute(
        start_edge,
        destination_edge
    )

    if not result.edges:
        raise RuntimeError(
            f"No route found for vehicle {vehicle_id}"
        )

    replacement_routes[vehicle_id] = list(result.edges)

    print(
        f"Vehicle {vehicle_id}: "
        f"{len(result.edges)} edges, "
        f"cost={result.cost:.2f}"
    )

traci.close()

print("\nCreating repaired traffic file...")

tree = ET.parse(INPUT_FILE)
root = tree.getroot()

for vehicle in root.findall("vehicle"):

    vehicle_id = vehicle.get("id")

    if vehicle_id not in replacement_routes:
        continue

    route_element = vehicle.find("route")

    if route_element is None:
        continue

    old_route = route_element.get("edges")

    new_route = " ".join(
        replacement_routes[vehicle_id]
    )

    route_element.set("edges", new_route)

    print(f"\nRepaired vehicle {vehicle_id}")
    print("Old route:")
    print(old_route)
    print("New route:")
    print(new_route)

tree.write(
    OUTPUT_FILE,
    encoding="UTF-8",
    xml_declaration=True
)

print("\n========================================")
print("REPAIRED FILE CREATED")
print("========================================")
print(OUTPUT_FILE)