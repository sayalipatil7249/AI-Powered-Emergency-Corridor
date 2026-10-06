import os
import traci

SUMO = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET = "simulation/sumo/pune_network_v2/expanded_network/expanded.net.xml.gz"
ROUTE = "simulation/sumo/pune_network_v2/expanded_network/ambulance_hospital.rou.xml"

traci.start([
    SUMO,
    "--net-file", NET,
    "--route-files", ROUTE,
    "--step-length", "1",
])

print("Running ambulance to hospital...")
print()

last_index = -1

for _ in range(500):
    traci.simulationStep()

    vehicles = traci.vehicle.getIDList()

    if "ambulance_01" not in vehicles:
        print("Ambulance is no longer active.")
        break

    route_index = traci.vehicle.getRouteIndex("ambulance_01")

    if route_index != last_index:
        edge = traci.vehicle.getRoadID("ambulance_01")
        speed = traci.vehicle.getSpeed("ambulance_01")

        print(
            f"Time {int(traci.simulation.getTime())}s | "
            f"Edge {edge} | "
            f"Route index {route_index} | "
            f"Speed {speed:.2f} m/s"
        )

        last_index = route_index

print()
print(
    "Ambulance still active:",
    "ambulance_01" in traci.vehicle.getIDList()
)

traci.close()