import os
import sys

sumo_home = os.environ.get("SUMO_HOME")

if not sumo_home:
    raise RuntimeError("SUMO_HOME is not set.")

sys.path.append(os.path.join(sumo_home, "tools"))

import sumolib


NETWORK_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"
ROUTE_FILE = "simulation/sumo/pune_network_v2/emergency_corridor.rou.xml"

J1 = "672160723"
J2 = "12420420707"
J3 = "cluster_11461816123_11461816125_11461816131_11461816132_#6more"

net = sumolib.net.readNet(NETWORK_FILE)


def get_node(node_id):
    node = net.getNode(node_id)

    if node is None:
        raise RuntimeError(f"Junction not found: {node_id}")

    return node


def find_route(start_edge, end_edge):
    path, cost = net.getShortestPath(
        start_edge,
        end_edge
    )

    if not path:
        raise RuntimeError(
            f"No route between {start_edge.getID()} "
            f"and {end_edge.getID()}"
        )

    return path, cost


j1 = get_node(J1)
j2 = get_node(J2)
j3 = get_node(J3)


# We need a road BEFORE J1.
j1_incoming = j1.getIncoming()

# We need a road AFTER J3.
j3_outgoing = j3.getOutgoing()

if not j1_incoming:
    raise RuntimeError("J1 has no incoming roads.")

if not j3_outgoing:
    raise RuntimeError("J3 has no outgoing roads.")


best_route = None
best_cost = None
best_start = None
best_destination = None


# Try every incoming edge to J1 and every outgoing edge from J3.
for start_edge in j1_incoming:

    for destination_edge in j3_outgoing:

        try:

            path, cost = find_route(
                start_edge,
                destination_edge
            )

            if best_cost is None or cost < best_cost:

                best_route = path
                best_cost = cost
                best_start = start_edge
                best_destination = destination_edge

        except Exception:
            continue


if best_route is None:
    raise RuntimeError(
        "Could not find a continuous route through the corridor."
    )


edge_ids = []

for edge in best_route:

    edge_id = edge.getID()

    if not edge_ids or edge_ids[-1] != edge_id:
        edge_ids.append(edge_id)


# Verify every edge exists.
for edge_id in edge_ids:

    if net.getEdge(edge_id) is None:
        raise RuntimeError(
            f"Invalid edge detected: {edge_id}"
        )


# Find traffic lights along the final route.
signals = []

for edge_id in edge_ids:

    edge = net.getEdge(edge_id)

    node = edge.getToNode()

    if node.getType() == "traffic_light":

        signal_id = node.getID()

        if signal_id not in signals:
            signals.append(signal_id)


print("=" * 100)
print("CONTINUOUS EMERGENCY CORRIDOR ROUTE")
print("=" * 100)

print()
print("Start edge:", best_start.getID())
print("Destination edge:", best_destination.getID())

print()
print("Total route cost:", round(best_cost, 2))
print("Total edges:", len(edge_ids))

print()
print("Traffic lights encountered:")

for index, signal in enumerate(signals, start=1):
    print(f"{index}. {signal}")

print()
print("Traffic lights:", len(signals))

print()
print("Route edges:")
print("=" * 100)

for index, edge_id in enumerate(edge_ids, start=1):
    print(f"{index:03d}. {edge_id}")


# We want our three selected corridor signals.
required_signals = [J1, J2, J3]

missing = [
    signal
    for signal in required_signals
    if signal not in signals
]

if missing:

    print()
    print("WARNING:")
    print("The route does not contain all three selected junctions.")

    for signal in missing:
        print("Missing:", signal)

else:

    print()
    print("SUCCESS:")
    print("The route contains J1 -> J2 -> J3.")


# Generate XML only after route verification.
route_string = " ".join(edge_ids)

route_xml = f"""<?xml version="1.0" encoding="UTF-8"?>

<routes>

    <vType
        id="normal_car"
        accel="2.6"
        decel="4.5"
        sigma="0.5"
        length="5"
        maxSpeed="13.9"
    />

    <vType
        id="ambulance"
        accel="3.0"
        decel="5.0"
        sigma="0.2"
        length="5"
        maxSpeed="20"
        vClass="emergency"
    />

    <route
        id="emergency_route"
        edges="{route_string}"
    />

    <vehicle
        id="ambulance_01"
        type="ambulance"
        route="emergency_route"
        depart="0"
    />

</routes>
"""


with open(ROUTE_FILE, "w", encoding="utf-8") as file:
    file.write(route_xml)


print()
print("=" * 100)
print("Route file generated:")
print(ROUTE_FILE)
print("=" * 100)