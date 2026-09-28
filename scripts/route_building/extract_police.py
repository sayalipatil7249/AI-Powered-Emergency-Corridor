"""
Extract named police stations and chowkis (posts) inside the simulated
area from the OpenStreetMap data, for the deadlock response (police help
clear traffic for a stuck ambulance).

Output: simulation/sumo/pune_network_v2/police_stations.json

Usage (from the project root):
    python -m scripts.route_building.extract_police
"""

import gzip
import json
import re
import xml.etree.ElementTree as ET

from simulation.sumo import route_planner

OSM_FILE = "simulation/sumo/pune_network_v2/expanded_osm/osm_bbox.osm.xml.gz"
OUTPUT = "simulation/sumo/pune_network_v2/police_stations.json"


def _english(name):
    """"Somwar Peth Police Station सोमवार पेठ ..." -> the English part."""
    return re.split(r"[^\x00-\x7F]", name)[0].strip()


def _local(name, tags):
    """The Marathi name: the name:mr tag, else the non-English part of
    the name, or None."""
    if tags.get("name:mr"):
        return tags["name:mr"].strip()
    match = re.search(r"[^\x00-\x7F].*", name)
    return match.group(0).strip() if match else None


def main():
    nodes = {}
    stations = []

    with gzip.open(OSM_FILE) as file:
        for _, element in ET.iterparse(file, events=("end",)):
            tags = {tag.get("k"): tag.get("v") for tag in element.findall("tag")}

            if element.tag == "node":
                position = (float(element.get("lat")), float(element.get("lon")))
                nodes[element.get("id")] = position
                if tags.get("amenity") == "police" and tags.get("name"):
                    stations.append((tags["name"], tags, position))
            elif element.tag == "way":
                if tags.get("amenity") == "police" and tags.get("name"):
                    points = [
                        nodes[node.get("ref")] for node in element.findall("nd")
                        if node.get("ref") in nodes
                    ]
                    if points:
                        stations.append((tags["name"], tags, (
                            sum(p[0] for p in points) / len(points),
                            sum(p[1] for p in points) / len(points),
                        )))
                element.clear()

    result = [
        {
            "name": _english(name),
            "name_local": _local(name, tags),
            "latitude": round(latitude, 6),
            "longitude": round(longitude, 6),
        }
        for name, tags, (latitude, longitude) in stations
        if route_planner.inside_area(latitude, longitude)
    ]
    result.sort(key=lambda item: item["name"])

    with open(OUTPUT, "w", encoding="utf-8") as file:
        json.dump(result, file, indent=2, ensure_ascii=False)
    print(f"Saved {len(result)} police stations to {OUTPUT}")


if __name__ == "__main__":
    main()
