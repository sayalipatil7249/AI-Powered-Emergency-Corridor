import os
import traci


SUMO_BINARY = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"
NORMAL_TRAFFIC = "simulation/sumo/pune_network_v2/normal_traffic.rou.xml"
AMBULANCE_ROUTE = "simulation/sumo/pune_network_v2/ambulance.rou.xml"

J2 = "cluster_11459261068_11459269187_11459269431_672160725"


traci.start([
    SUMO_BINARY,
    "--net-file", NET_FILE,
    "--route-files", f"{NORMAL_TRAFFIC},{AMBULANCE_ROUTE}",
])

print("\nConnected to SUMO.")
print("\nJ2 Complete Signal-Link Analysis")
print("=================================")

controlled_links = traci.trafficlight.getControlledLinks(J2)

print(f"\nJ2 has {len(controlled_links)} controlled signal indexes.\n")

for index, link_group in enumerate(controlled_links):

    print(f"Signal index {index}:")

    for link in link_group:

        incoming_lane = link[0]
        outgoing_lane = link[1]

        print(f"  incoming: {incoming_lane}")
        print(f"  outgoing: {outgoing_lane}")

print("\nAmbulance route around J2:")
print("--------------------------------")

route = None

while traci.simulation.getTime() < 45:

    traci.simulationStep()

    if "ambulance_01" not in traci.vehicle.getIDList():
        continue

    route_index = traci.vehicle.getRouteIndex("ambulance_01")

    if 3 <= route_index <= 9:

        if route is None:
            route = traci.vehicle.getRoute("ambulance_01")

        current_edge = traci.vehicle.getRoadID("ambulance_01")

        print(
            f"Time {traci.simulation.getTime():.0f}s | "
            f"Route index {route_index} | "
            f"Current edge {current_edge}"
        )


traci.close()

print("\nAnalysis complete.")