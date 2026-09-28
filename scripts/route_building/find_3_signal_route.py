import sumolib
from collections import deque

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"

START_EDGE = "282484394#3"

net = sumolib.net.readNet(NET_FILE)

start_edge = net.getEdge(START_EDGE)

queue = deque()
queue.append((start_edge, [], []))

visited = set()


def add_signal(edge, signal_list):
    tls = edge.getTLS()

    if tls is None:
        return

    tls_id = tls.getID()

    if tls_id not in signal_list:
        signal_list.append(tls_id)


while queue:

    edge, path, signals = queue.popleft()

    edge_id = edge.getID()

    state = (edge_id, tuple(signals))

    if state in visited:
        continue

    visited.add(state)

    current_path = path + [edge_id]

    current_signals = signals.copy()

    add_signal(edge, current_signals)

    # We found a route passing through at least 3 traffic lights
    if len(current_signals) >= 3:

        print("\nFOUND ROUTE WITH 3 SIGNAL CONTROLLERS")
        print("-----------------------------------")

        print(f"Total edges: {len(current_path)}")

        print("\nTraffic-light controllers:")

        for index, tls_id in enumerate(current_signals, start=1):
            print(f"{index}. {tls_id}")

        print("\nRoute edges:")

        for index, edge_id in enumerate(current_path, start=1):
            print(f"{index}. {edge_id}")

        print("\nDestination edge:")
        print(current_path[-1])

        break

    for next_edge in edge.getOutgoing():

        if next_edge.getID() not in current_path:
            queue.append(
                (
                    next_edge,
                    current_path,
                    current_signals
                )
            )

else:

    print("No route with 3 traffic-light controllers found.")