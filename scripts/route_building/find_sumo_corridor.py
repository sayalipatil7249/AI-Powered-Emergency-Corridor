import gzip
import xml.etree.ElementTree as ET
from pyproj import Transformer

NETWORK_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"

with gzip.open(NETWORK_FILE, "rt", encoding="utf-8") as f:
    tree = ET.parse(f)

root = tree.getroot()

location = root.find("location")

offset_x, offset_y = map(
    float,
    location.attrib["netOffset"].split(",")
)

transformer = Transformer.from_crs(
    "EPSG:32643",
    "EPSG:4326",
    always_xy=True
)

print("SUMO traffic-light locations")
print("=" * 80)

for junction in root.findall("junction"):
    if junction.attrib.get("type") != "traffic_light":
        continue

    signal_id = junction.attrib["id"]

    x = float(junction.attrib["x"])
    y = float(junction.attrib["y"])

    utm_x = x - offset_x
    utm_y = y - offset_y

    longitude, latitude = transformer.transform(
        utm_x,
        utm_y
    )

    print(
        f"{signal_id}\n"
        f"  Latitude : {latitude:.6f}\n"
        f"  Longitude: {longitude:.6f}\n"
        f"  SUMO X   : {x:.2f}\n"
        f"  SUMO Y   : {y:.2f}\n"
    )