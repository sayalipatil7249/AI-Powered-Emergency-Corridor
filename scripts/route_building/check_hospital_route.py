import sumolib


NET_FILE = (
    "simulation/sumo/"
    "pune_network_v2/"
    "expanded_network/"
    "expanded.net.xml.gz"
)


CURRENT_FINAL_EDGE = "-206994680#1"

TARGET_EDGES = [
    "707646281",
    "1118318901",
    "-1118318901",
    "-1360824886",
    "1360824886",
    "206994680#1",
    "-206994680#2",
    "206994680#2",
]


net = sumolib.net.readNet(NET_FILE)


print()
print("==============================================")
print(" HOSPITAL ROUTE CONNECTIVITY CHECK")
print("==============================================")
print()


current_edge = net.getEdge(CURRENT_FINAL_EDGE)

print("Current final edge:")
print(f"  {CURRENT_FINAL_EDGE}")
print(f"  From: {current_edge.getFromNode().getID()}")
print(f"  To  : {current_edge.getToNode().getID()}")
print(f"  Length: {current_edge.getLength():.2f} m")

print()
print("Outgoing edges from current final edge:")
print("----------------------------------------------")

for edge in current_edge.getToNode().getOutgoing():

    print(
        f"{edge.getID():<40} "
        f"length={edge.getLength():.2f} m"
    )


print()
print("==============================================")
print(" TARGET EDGE CONNECTIVITY")
print("==============================================")
print()


for target_id in TARGET_EDGES:

    try:

        target_edge = net.getEdge(target_id)

        print()
        print(f"Target: {target_id}")

        print(
            f"  From: {target_edge.getFromNode().getID()}"
        )

        print(
            f"  To  : {target_edge.getToNode().getID()}"
        )

        print(
            f"  Length: {target_edge.getLength():.2f} m"
        )

        # Try to find a SUMO route from the current
        # final edge to this target edge.
        result = net.getShortestPath(
            current_edge,
            target_edge,
        )

        if result is None:

            print("  Route from current edge: NOT FOUND")

        else:

            path_edges, cost = result

            print(
                f"  Route from current edge: FOUND"
            )

            print(
                f"  Number of edges: {len(path_edges)}"
            )

            print(
                f"  Cost: {cost}"
            )

            print("  Path:")

            for edge in path_edges:

                print(
                    f"    -> {edge.getID()}"
                )

    except Exception as error:

        print(
            f"  ERROR: {error}"
        )


print()
print("==============================================")
print("END")
print("==============================================")