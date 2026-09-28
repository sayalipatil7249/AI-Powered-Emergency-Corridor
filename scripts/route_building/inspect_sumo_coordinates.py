#convert sumo co-ordinates into GPS longitude , latitude 

import gzip
import xml.etree.ElementTree as ET
from pyproj import Transformer

#location of V2 SUMO network.
NETWORK_FILE = "simulation/sumo/pune_network_v2/osm.net.xml.gz"

#Open the compressed SUMO XML
with gzip.open(NETWORK_FILE, "rt", encoding="utf-8") as f:             #rt means read text 
    tree = ET.parse(f)   #reads the XML structure.

#Get the root XML element...root represents the top-level <net> element.
root = tree.getroot()

#Find SUMO's location information...SUMO's network contains a <location> element that tells us how its coordinates were generated.
location = root.find("location")

#Read the netOffset
net_offset = location.attrib["netOffset"]
offset_x, offset_y = map(float, net_offset.split(","))

#Print the coordinate information
print("SUMO netOffset:", net_offset)
print("Projection:", location.attrib.get("projParameter"))
print()

#Create the coordinate converter...Convert coordinates from UTM Zone 43N to normal GPS latitude/longitude.
transformer = Transformer.from_crs(
    "EPSG:32643",   #WGS84 / UTM Zone 43N
    "EPSG:4326",    #WGS84 latitude/longitude
    always_xy=True
)

signals = []

#find traffic light junction 
for junction in root.findall("junction"):
    #If this junction isn't controlled by a traffic light, ignore it.
    if junction.attrib.get("type") != "traffic_light":
        continue

    #Get sumo signal ID e.g. 245646965
    signal_id = junction.attrib["id"]

    #et SUMO's x and y
    x = float(junction.attrib["x"])
    y = float(junction.attrib["y"])

    #Undo SUMO's offset
    utm_x = x - offset_x
    utm_y = y - offset_y

    #Convert UTM to GPS
    longitude, latitude = transformer.transform(utm_x, utm_y)

    #store the result 
    signals.append({
        "signal_id": signal_id,
        "x": x,
        "y": y,
        "latitude": latitude,
        "longitude": longitude,
    })

print("Traffic-light junctions found:", len(signals))
print()

for signal in signals:
    print(
        f'{signal["signal_id"]} | '
        f'x={signal["x"]:.2f} | '
        f'y={signal["y"]:.2f} | '
        f'lat={signal["latitude"]:.6f} | '
        f'lon={signal["longitude"]:.6f}'
    )






#======================================== NOTES =================================================
"""
1)gzip => Our SUMO network is: osm.net.xml.gz => The .gz means the XML file is compressed.So gzip allows Python to open it directly.
2)ElementTree =>The SUMO network is an XML file.  ElementTree allows Python to read this XML structure.
3)pyproj can convert between coordinate systems.
In our case:
        UTM Zone 43N
            ↓
            WGS84
            ↓
        latitude / longitude
4)SUMO x/y → remove offset → UTM → convert with pyproj → GPS coordinates → match backend signals.

5) Whole Project Idea: 
        SUMO network
               │
               ▼
       Read osm.net.xml.gz
               │
               ▼
       Find traffic lights
               │
               ▼
          Get x and y
               │
               ▼
        Undo netOffset
               │
               ▼
          UTM coordinates
               │
               ▼
       pyproj Transformer
               │
               ▼
       Latitude / Longitude
               │
               ▼
      Match with our backend

"""