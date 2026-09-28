"""
Extract named hospitals inside the simulated area from the OpenStreetMap
data, for the dashboard's hospital picker.

Output: simulation/sumo/pune_network_v2/hospitals.json

Usage (from the project root):
    python -m scripts.route_building.extract_hospitals
"""

import gzip
import json
import xml.etree.ElementTree as ET

import sumolib

OSM_FILE = "simulation/sumo/pune_network_v2/expanded_osm/osm_bbox.osm.xml.gz"
NET_FILE = "simulation/sumo/pune_network_v2/expanded_network/expanded.net.xml.gz"
OUTPUT = "simulation/sumo/pune_network_v2/hospitals.json"


# Entries this close together that share their first word are the same
# hospital tagged more than once (e.g. "Ruby Hall" / "Ruby Hall Clinic").
DUPLICATE_METERS = 200


def _distance_m(a, b):
    from math import cos, radians, hypot
    dy = (a["latitude"] - b["latitude"]) * 111_320
    dx = (a["longitude"] - b["longitude"]) * 111_320 * cos(radians(a["latitude"]))
    return hypot(dx, dy)


def _first_word(name):
    return name.lower().replace(".", " ").split()[0]


def _merge_duplicates(hospitals):
    """Keep one entry per hospital, preferring the longest proper name."""

    kept = []

    # Properly capitalised names first ("Ruby Hall Clinic" over
    # "Ruby hall hospital" or "KAMLA NEHRU"), then the longest.
    for hospital in sorted(
        hospitals,
        key=lambda item: (
            item["name"].isupper(),
            not item["name"].istitle(),
            -len(item["name"]),
        ),
    ):
        duplicate = any(
            _first_word(other["name"]) == _first_word(hospital["name"])
            and _distance_m(other, hospital) <= DUPLICATE_METERS
            for other in kept
        )
        if not duplicate:
            kept.append(hospital)

    return kept


def main():
    net = sumolib.net.readNet(NET_FILE)
    xmin, ymin, xmax, ymax = net.getBoundary()
    lon_min, lat_min = net.convertXY2LonLat(xmin, ymin)
    lon_max, lat_max = net.convertXY2LonLat(xmax, ymax)

    with gzip.open(OSM_FILE, "rt", encoding="utf-8") as file:
        root = ET.parse(file).getroot()

    nodes = {
        node.get("id"): (float(node.get("lat")), float(node.get("lon")))
        for node in root.iter("node")
    }

    hospitals = {}

    for element in list(root.iter("node")) + list(root.iter("way")):
        tags = {tag.get("k"): tag.get("v") for tag in element.iter("tag")}

        if tags.get("amenity") != "hospital" or not tags.get("name"):
            continue

        if element.tag == "node":
            lat, lon = float(element.get("lat")), float(element.get("lon"))
        else:
            points = [nodes[nd.get("ref")] for nd in element.iter("nd")
                      if nd.get("ref") in nodes]
            if not points:
                continue
            lat = sum(point[0] for point in points) / len(points)
            lon = sum(point[1] for point in points) / len(points)

        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            continue

        name = tags["name"].strip()
        hospitals.setdefault(name, {
            "name": name,
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
        })

    result = _merge_duplicates(list(hospitals.values()))
    result.sort(key=lambda item: item["name"].lower())

    with open(OUTPUT, "w") as file:
        json.dump(result, file, indent=1, ensure_ascii=False)

    print(f"{len(result)} hospitals written to {OUTPUT}")


if __name__ == "__main__":
    main()
