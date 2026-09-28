import os
import traci

SUMO = os.path.join(os.environ["SUMO_HOME"], "bin", "sumo")

NET = "simulation/sumo/pune_network_v2/expanded_network/expanded.net.xml.gz"
ROUTE_OUTPUT = "simulation/sumo/pune_network_v2/expanded_network/ambulance_hospital.rou.xml"

START_EDGE = "282484394#3"
J1_EXIT = "216582074#6"
J2_EXIT = "22841096#7"
HOSPITAL_EDGE = "-206994680#1"

traci.start([
    SUMO,
    "--net-file", NET,
    "--route-files",
    "simulation/sumo/pune_network_v2/ambulance.rou.xml",
])

print("Calculating ambulance corridor route...")

segment1 = traci.simulation.findRoute(
    START_EDGE,
    J1_EXIT,
    vType="ambulance"
).edges

segment2 = traci.simulation.findRoute(
    J1_EXIT,
    J2_EXIT,
    vType="ambulance"
).edges

segment3 = traci.simulation.findRoute(
    J2_EXIT,
    HOSPITAL_EDGE,
    vType="ambulance"
).edges

traci.close()

# Join segments without duplicating connection edges.
full_route = (
    list(segment1)
    + list(segment2[1:])
    + list(segment3[1:])
)

print("Segment 1:", len(segment1), "edges")
print("Segment 2:", len(segment2), "edges")
print("Segment 3:", len(segment3), "edges")
print("Final route:", len(full_route), "edges")

print("\nFinal route:")
for index, edge in enumerate(full_route):
    print(f"{index}: {edge}")

# Basic continuity check.
for i in range(len(full_route) - 1):
    if full_route[i] == full_route[i + 1]:
        raise RuntimeError(
            f"Duplicate consecutive edge at index {i}: {full_route[i]}"
        )

route_edges = " ".join(full_route)

xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<routes>
    <vType
        id="ambulance"
        accel="3.0"
        decel="5.0"
        sigma="0.2"
        length="5.0"
        maxSpeed="20.0"
        vClass="emergency"
    />

    <route
        id="emergency_corridor_to_hospital"
        edges="{route_edges}"
    />

    <vehicle
        id="ambulance_01"
        type="ambulance"
        route="emergency_corridor_to_hospital"
        depart="0"
    />
</routes>
'''

with open(ROUTE_OUTPUT, "w", encoding="utf-8") as file:
    file.write(xml)

print("\nRoute file created:")
print(ROUTE_OUTPUT)