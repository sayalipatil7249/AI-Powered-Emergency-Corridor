import os
import traci

SUMO_BINARY = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"
NORMAL_TRAFFIC = "simulation/sumo/pune_network_v2/normal_traffic.rou.xml"
AMBULANCE_ROUTE = "simulation/sumo/pune_network_v2/ambulance.rou.xml"

DESTINATION_EDGES = [
    "218681353#2",
    "1117977442",
]

traci.start([
    SUMO_BINARY,
    "--net-file", NET_FILE,
    "--route-files", f"{NORMAL_TRAFFIC},{AMBULANCE_ROUTE}",
    "--step-length", "1",
])

print("Connected to SUMO.")

try:
    for destination in DESTINATION_EDGES:
        print(f"\nChecking destination: {destination}")

        try:
            route = traci.simulation.findRoute(
                "282484394#3",
                destination,
                vType="ambulance"
            )

            print("Route cost:", route.cost)
            print("Route edges:", route.edges)

            if route.edges:
                print("Destination is reachable.")
            else:
                print("Destination is NOT reachable.")

        except Exception as error:
            print("Error:", error)

finally:
    traci.close()
    print("\nSUMO closed.")