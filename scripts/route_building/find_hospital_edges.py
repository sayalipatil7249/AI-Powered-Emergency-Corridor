from pyproj import Transformer
import sumolib


# ---------------------------------------------------------
# Ruby Hall Clinic approximate destination
# ---------------------------------------------------------

HOSPITAL_LATITUDE = 18.53354
HOSPITAL_LONGITUDE = 73.87715


# ---------------------------------------------------------
# Same SUMO offsets used by sumo_bridge.py
# ---------------------------------------------------------

NET_OFFSET_X = -373877.28
NET_OFFSET_Y = -2045856.76


# ---------------------------------------------------------
# Convert WGS84 -> UTM Zone 43N
# ---------------------------------------------------------

wgs84_to_utm = Transformer.from_crs(
    "EPSG:4326",
    "EPSG:32643",
    always_xy=True,
)


utm_x, utm_y = wgs84_to_utm.transform(
    HOSPITAL_LONGITUDE,
    HOSPITAL_LATITUDE,
)


# ---------------------------------------------------------
# Convert UTM -> SUMO coordinates
# ---------------------------------------------------------

sumo_x = utm_x + NET_OFFSET_X
sumo_y = utm_y + NET_OFFSET_Y


print()
print("==============================================")
print(" RUBY HALL HOSPITAL SUMO DESTINATION CHECK")
print("==============================================")
print()

print(
    f"Hospital latitude  : {HOSPITAL_LATITUDE}"
)
print(
    f"Hospital longitude : {HOSPITAL_LONGITUDE}"
)

print()

print(
    f"UTM X : {utm_x:.3f}"
)
print(
    f"UTM Y : {utm_y:.3f}"
)

print()

print(
    f"SUMO X : {sumo_x:.3f}"
)
print(
    f"SUMO Y : {sumo_y:.3f}"
)

print()


# ---------------------------------------------------------
# Load SUMO network
# ---------------------------------------------------------

NET_FILE = (
    "simulation/sumo/"
    "pune_network_v2/"
    "expanded_network/"
    "expanded.net.xml.gz"
)

net = sumolib.net.readNet(NET_FILE)


# ---------------------------------------------------------
# Find nearby edges
# ---------------------------------------------------------

nearby_edges = net.getNeighboringEdges(
    sumo_x,
    sumo_y,
    500,
)


nearby_edges = sorted(
    nearby_edges,
    key=lambda item: item[1],
)


print("Nearby SUMO edges:")
print("----------------------------------------------")


if not nearby_edges:
    print("No nearby edges found.")
else:

    for index, (edge, distance) in enumerate(
        nearby_edges[:30],
        start=1,
    ):

        print(
            f"{index:02d}. "
            f"Edge: {edge.getID():<40} "
            f"Distance: {distance:.2f} m "
            f"Length: {edge.getLength():.2f} m"
        )


print()
print("==============================================")
print("END")
print("==============================================")