"""
Traffic scenarios for the Pune network.

Generates realistic background traffic with SUMO's randomTrips tool at
three levels, plus an ambulance file that departs after the roads have
filled up (warm-up), so the ambulance meets real congestion.

Usage (from the project root):
    python -m ai.scenarios --level normal --seed 1
"""

import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import sumo

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

NETWORK_DIR = os.path.join(
    PROJECT_ROOT, "simulation", "sumo", "pune_network_v2"
)
NET_FILE = os.path.join(NETWORK_DIR, "expanded_network", "expanded.net.xml.gz")
AMBULANCE_TEMPLATE = os.path.join(
    NETWORK_DIR, "expanded_network", "ambulance_hospital.rou.xml"
)

SCENARIO_DIR = os.path.join(PROJECT_ROOT, "data", "scenarios")

# Vehicles inserted per hour per km of lane. Calibrated so a normal car
# on the ambulance route takes roughly 10-15 min (light), 12-18 min
# (normal) and 15-25 min (heavy) for the 5.2 km trip. Confirm these
# against real Google Maps travel times for the route.
TRAFFIC_LEVELS = {
    "light": 14,
    "normal": 24,
    "heavy": 34,
}

# Trips start and end mostly on main roads (trunk, primary, secondary),
# where real city traffic concentrates. Factors per OSM road type.
EDGE_TYPE_WEIGHTS = os.path.join(NETWORK_DIR, "edge_type_weights.txt")

# Pune driving profile (speed factor, gaps), loaded before the traffic.
VTYPES_FILE = os.path.join(NETWORK_DIR, "pune_vtypes.add.xml")

# SUMO options shared by every scenario run:
# drivers re-route around jams like drivers using a navigation app.
SUMO_TRAFFIC_OPTIONS = [
    "--additional-files", VTYPES_FILE,
    "--ignore-route-errors",
    "--no-step-log",
    "--no-warnings",
    "--device.rerouting.probability", "0.5",
    "--device.rerouting.period", "60",
]

# Seconds of traffic before the ambulance departs, so roads are busy.
WARMUP_SECONDS = 600

# Traffic keeps being inserted until this time.
TRAFFIC_END_SECONDS = 2700


def generate_traffic(level, seed, output_dir=SCENARIO_DIR):
    """Create a background-traffic route file. Returns its path."""

    os.makedirs(output_dir, exist_ok=True)

    trips_file = os.path.join(output_dir, f"{level}_seed{seed}.trips.xml")
    route_file = os.path.join(output_dir, f"{level}_seed{seed}.rou.xml")

    if os.path.exists(route_file):
        return route_file

    random_trips = os.path.join(sumo.SUMO_HOME, "tools", "randomTrips.py")

    command = [
        sys.executable,
        random_trips,
        "--net-file", NET_FILE,
        "--output-trip-file", trips_file,
        "--route-file", route_file,
        "--begin", "0",
        "--end", str(TRAFFIC_END_SECONDS),
        "--insertion-density", str(TRAFFIC_LEVELS[level]),
        "--random-depart",
        "--seed", str(seed),
        "--fringe-factor", "5",
        "--min-distance", "300",
        "--prefix", "veh",
        "--trip-attributes", 'departLane="best" departSpeed="max"',
        "--remove-loops",
        "--validate",
        # Busier on main roads, like real cities: weight start/end roads
        # by number of lanes and by road type.
        "--lanes",
        "--edge-type-file", EDGE_TYPE_WEIGHTS,
    ]

    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    use_pune_profile(route_file)
    return route_file


def use_pune_profile(route_file):
    """
    Make every car in a traffic file use the Pune driving profile.

    Files made with randomTrips' --vehicle-class define their own car
    type (SUMO defaults: cars drive at ~100 % of the limit), which hides
    DEFAULT_VEHTYPE from pune_vtypes.add.xml. Remove that type so the
    cars fall back to DEFAULT_VEHTYPE. Returns True if the file changed.
    """

    with open(route_file, encoding="utf-8") as file:
        text = file.read()

    cleaned = re.sub(r'\s*<vType id="veh_passenger"[^>]*/>', "", text)
    cleaned = cleaned.replace(' type="veh_passenger"', "")

    if cleaned == text:
        return False

    with open(route_file, "w", encoding="utf-8") as file:
        file.write(cleaned)
    return True


def generate_ambulance(depart_time, output_dir=SCENARIO_DIR):
    """
    Copy the fixed demo ambulance route, departing at depart_time.
    Returns its path.
    """

    os.makedirs(output_dir, exist_ok=True)

    output_file = os.path.join(
        output_dir, f"ambulance_depart{int(depart_time)}.rou.xml"
    )

    if os.path.exists(output_file):
        return output_file

    from simulation.sumo.sumo_bridge import demo_plan

    plan = demo_plan()
    tree = ET.parse(AMBULANCE_TEMPLATE)

    # The fastest demo route, as in the live dashboard.
    for route in tree.getroot().iter("route"):
        route.set("edges", " ".join(plan["roads"]))

    for vehicle in tree.getroot().iter("vehicle"):
        vehicle.set("depart", str(depart_time))
        vehicle.set("departPos", str(plan["depart_position"]))
        vehicle.set("arrivalPos", str(plan["arrival_position"]))

    tree.write(output_file, encoding="UTF-8", xml_declaration=True)

    return output_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", choices=TRAFFIC_LEVELS, default="normal")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    print(generate_traffic(args.level, args.seed))
    print(generate_ambulance(WARMUP_SECONDS))


if __name__ == "__main__":
    main()
