import os
import traci

SUMO_BINARY = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"
NORMAL_TRAFFIC = "simulation/sumo/pune_network_v2/normal_traffic.rou.xml"
AMBULANCE_ROUTE = "simulation/sumo/pune_network_v2/ambulance.rou.xml"


sumo_command = [
    SUMO_BINARY,
    "--net-file", NET_FILE,
    "--route-files", f"{NORMAL_TRAFFIC},{AMBULANCE_ROUTE}",
    "--step-length", "1",
]

traci.start(sumo_command)

print("Connected to SUMO successfully.")

while traci.simulation.getMinExpectedNumber() > 0:

    traci.simulationStep()

    vehicles = traci.vehicle.getIDList()

    if "ambulance_01" in vehicles:

        position = traci.vehicle.getPosition("ambulance_01")
        speed = traci.vehicle.getSpeed("ambulance_01")

        print(
            f"Ambulance position: {position}, "
            f"speed: {speed:.2f} m/s"
        )

traci.close()

print("SUMO simulation finished.")