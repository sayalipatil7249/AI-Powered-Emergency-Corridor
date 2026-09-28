import os
import sys

sumo_home = os.environ.get("SUMO_HOME")

if not sumo_home:
    raise RuntimeError("SUMO_HOME is not set.")

sys.path.append(os.path.join(sumo_home, "tools"))

import sumolib


NETWORK_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"

net = sumolib.net.readNet(NETWORK_FILE)


J1 = "672160723"
J2 = "12420420707"
J3 = "cluster_11461816123_11461816125_11461816131_11461816132_#6more"


def get_node(node_id):
    node = net.getNode(node_id)

    if node is None:
        raise RuntimeError(f"Junction not found: {node_id}")

    return node


def get_best_route(start_node, end_node):

    best_path = None
    best_cost = None

    for start_edge in start_node.getOutgoing():

        for end_edge in end_node.getIncoming():

            try:
                path, cost = net.getShortestPath(
                    start_edge,
                    end_edge
                )

                if path and (best_cost is None or cost < best_cost):
                    best_path = path
                    best_cost = cost

            except Exception:
                pass

    return best_path, best_cost


j1 = get_node(J1)
j2 = get_node(J2)
j3 = get_node(J3)


# J1 -> J2
route_1, cost_1 = get_best_route(j1, j2)

if route_1 is None:
    raise RuntimeError("Could not find J1 -> J2 route.")


# J2 -> J3
route_2, cost_2 = get_best_route(j2, j3)

if route_2 is None:
    raise RuntimeError("Could not find J2 -> J3 route.")


# Combine routes without duplicating the connection.
full_route = route_1 + route_2


print("=" * 100)
print("EMERGENCY CORRIDOR ROUTE")
print("=" * 100)

print(f"\nJ1: {J1}")
print(f"J2: {J2}")
print(f"J3: {J3}")

print(f"\nJ1 -> J2 cost: {cost_1:.2f}")
print(f"J2 -> J3 cost: {cost_2:.2f}")
print(f"Total cost: {cost_1 + cost_2:.2f}")

print("\nChecking edges...")
print("=" * 100)


# Remove duplicate consecutive edges.
clean_edges = []

for edge in full_route:

    edge_id = edge.getID()

    if not clean_edges or clean_edges[-1] != edge_id:
        clean_edges.append(edge_id)


# Verify every edge actually exists.
for index, edge_id in enumerate(clean_edges, start=1):

    if net.getEdge(edge_id) is None:
        raise RuntimeError(
            f"Invalid edge detected: {edge_id}"
        )

    print(f"{index:03d}. {edge_id}")


print("=" * 100)

print("\nTraffic lights encountered:")

seen = []

for edge_id in clean_edges:

    edge = net.getEdge(edge_id)
    node = edge.getToNode()

    if node.getType() == "traffic_light":

        signal_id = node.getID()

        if signal_id not in seen:
            seen.append(signal_id)

            print(
                f"{len(seen)}. {signal_id}"
            )


print("\nTraffic lights:", len(seen))
print("Total edges:", len(clean_edges))