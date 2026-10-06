import os
import traci

SUMO = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET = "simulation/sumo/pune_network_v2/expanded_network/expanded.net.xml.gz"
ROUTE = "simulation/sumo/pune_network_v2/ambulance.rou.xml"

JUNCTIONS = [
    "672160723",
    "cluster_11459261068_11459269187_11459269431_672160725",
    "cluster_11459329985_11459329986_11459329987_245646882",
]

traci.start([
    SUMO,
    "--net-file", NET,
    "--route-files", ROUTE,
])

route = traci.simulation.findRoute(
    "282484394#3",
    "-206994680#1",
    vType="ambulance"
).edges

print("Route length:", len(route))
print()

for junction in JUNCTIONS:
    print("=" * 70)
    print("Junction:", junction)

    if junction not in traci.trafficlight.getIDList():
        print("Traffic light NOT FOUND")
        continue

    controlled_links = traci.trafficlight.getControlledLinks(junction)

    incoming_edges = set()

    for link_group in controlled_links:
        for link in link_group:
            if link:
                incoming_lane = link[0]
                incoming_edge = incoming_lane.rsplit("_", 1)[0]
                incoming_edges.add(incoming_edge)

    print("Incoming edges:")
    for edge in sorted(incoming_edges):
        print(" ", edge)

    print("\nMatching route positions:")

    found = False

    for index, edge in enumerate(route):
        if edge in incoming_edges:
            print(" ", index, "->", edge)
            found = True

    if not found:
        print("  No matching route edge found.")

traci.close()

print("\nCheck complete.")