import sumolib

NET_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"

HOSPITAL_LAT = 18.53354
HOSPITAL_LON = 73.87715

net = sumolib.net.readNet(NET_FILE)

x, y = net.convertLonLat2XY(HOSPITAL_LON, HOSPITAL_LAT)

print("Hospital SUMO coordinates:")
print("X:", x)
print("Y:", y)

print("\nNearby vehicle-accessible edges:")

edges = net.getNeighboringEdges(x, y, r=5000)

candidates = []

for edge, distance in edges:
    permissions = edge.getPermissions()

    # Only consider edges that allow emergency vehicles
    if "emergency" not in permissions:
        continue

    candidates.append((distance, edge))

candidates.sort(key=lambda item: item[0])

for distance, edge in candidates[:15]:
    shape = edge.getShape()

    print("\n--------------------------------")
    print("Edge:", edge.getID())
    print("Distance:", round(distance, 2), "meters")
    print("Length:", round(edge.getLength(), 2), "meters")
    print("From:", edge.getFromNode().getID())
    print("To:", edge.getToNode().getID())
    print("Start:", shape[0])
    print("End:", shape[-1])