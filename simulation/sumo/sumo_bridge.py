"""
SUMO setup for the live demo: file locations, the command that starts
SUMO, Pune speed limits, and SUMO x/y to latitude/longitude conversion.

Reading and controlling the simulation happens in adapters.py.
"""

import os
from functools import lru_cache

import traci
from pyproj import Transformer


def _find_sumo_home():
    """SUMO_HOME, else the pip-installed eclipse-sumo, else Windows default."""

    if os.environ.get("SUMO_HOME"):
        return os.environ["SUMO_HOME"]

    try:
        import sumo
        return sumo.SUMO_HOME
    except ImportError:
        return r"C:\Program Files (x86)\Eclipse\Sumo"


SUMO_HOME = _find_sumo_home()

# Set SUMO_GUI=0 to run SUMO without its window (headless).
USE_SUMO_GUI = os.environ.get("SUMO_GUI", "1") != "0"

# Windows binaries end in .exe; macOS/Linux binaries have no extension.
SUMO_BINARY_EXTENSION = ".exe" if os.name == "nt" else ""

SUMO_BINARY = os.path.join(
    SUMO_HOME,
    "bin",
    ("sumo-gui" if USE_SUMO_GUI else "sumo") + SUMO_BINARY_EXTENSION
)

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

SUMO_NETWORK_DIR = os.path.join(
    PROJECT_ROOT, "simulation", "sumo", "pune_network_v2"
)

# The simulated area. "original" (default): 4.2 x 3.3 km of central
# Pune, on which the AI models were trained; trips are short now that
# patients go to the nearest suitable hospital, so it is big enough and
# lighter to run. "2x": about 5.9 x 4.7 km (area_2x/, built from
# OpenStreetMap on 2026-10-01); set SIM_AREA=2x in .env to use it.
SIM_AREA = os.environ.get("SIM_AREA", "original")

if SIM_AREA == "original":
    NET_FILE = os.path.join(
        SUMO_NETWORK_DIR, "expanded_network", "expanded.net.xml.gz"
    )
    SCENARIO_DIR = os.path.join(SUMO_NETWORK_DIR, "scenarios")
    HOSPITALS_FILE = os.path.join(SUMO_NETWORK_DIR, "hospitals.json")
    POLICE_FILE = os.path.join(SUMO_NETWORK_DIR, "police_stations.json")
else:
    AREA_DIR = os.path.join(SUMO_NETWORK_DIR, "area_2x")
    NET_FILE = os.path.join(AREA_DIR, "area_2x.net.xml.gz")
    SCENARIO_DIR = os.path.join(AREA_DIR, "scenarios")
    HOSPITALS_FILE = os.path.join(AREA_DIR, "hospitals.json")
    POLICE_FILE = os.path.join(AREA_DIR, "police_stations.json")

# Demo traffic (patterns the AI never saw in training), one file per
# level; live traffic picks the level. The ambulance is added at run time
# on the planned route.
TRAFFIC_FILES = {
    level: os.path.join(SCENARIO_DIR, f"demo_{level}_traffic.rou.xml")
    for level in ("light", "normal", "heavy")
}

# The ambulance departs once the roads have filled up (simulated s).
AMBULANCE_DEPART_TIME = 600

# A car standing this long inside a junction no longer blocks others (s).
JUNCTION_BLOCKER_SECONDS = 20

# Pune driving profile for background traffic, and the ambulance type.
VTYPES_FILE = os.path.join(SUMO_NETWORK_DIR, "pune_vtypes.add.xml")
AMBULANCE_VTYPE_FILE = os.path.join(SUMO_NETWORK_DIR, "ambulance_vtype.add.xml")

# The demo trip: Shukrawar Peth to Ruby Hall Clinic, along the fastest
# route (route_planner). The ambulance vehicle type for training runs is
# taken from DEMO_ROUTE_FILE.
DEMO_ROUTE_FILE = os.path.join(
    SUMO_NETWORK_DIR, "expanded_network", "ambulance_hospital.rou.xml"
)
DEMO_START_NAME = "Shukrawar Peth"
DEMO_START = (18.5160848, 73.8538128)  # latitude, longitude
DEMO_HOSPITAL_NAME = "Ruby Hall Clinic"


@lru_cache(maxsize=1)
def demo_plan():
    """The planned demo trip (roads, depart and arrival positions)."""

    from simulation.sumo import route_planner

    hospital = next(
        item for item in route_planner.hospitals()
        if item["name"] == DEMO_HOSPITAL_NAME
    )
    return route_planner.plan_route(
        *DEMO_START,
        hospital["latitude"],
        hospital["longitude"],
        DEMO_HOSPITAL_NAME,
    )


def demo_route_roads():
    """Road ids of the demo route."""
    return list(demo_plan()["roads"])


# OpenStreetMap import gave main roads SUMO's default 100 km/h limit,
# which is unrealistic for central Pune. Indian city roads are
# usually limited to 50 km/h.
CITY_SPEED_LIMIT = 50 / 3.6  # m/s

# The ambulance's top speed; must match maxSpeed in ambulance_vtype.add.xml.
AMBULANCE_MAX_SPEED = 45 / 3.6  # m/s


def sumo_command(traffic_level="normal"):
    """Command line that starts SUMO for the live demo."""

    command = [
        SUMO_BINARY,
        "--net-file", NET_FILE,
        "--route-files", TRAFFIC_FILES[traffic_level],
        "--additional-files", f"{VTYPES_FILE},{AMBULANCE_VTYPE_FILE}",
        "--step-length", "1",
        "--ignore-route-errors",
        "--no-warnings",
        # Drivers re-route around jams, like using a navigation app.
        "--device.rerouting.probability", "0.5",
        "--device.rerouting.period", "60",
        # Gridlock: cars that drove into a junction while their exit was
        # full block everyone, the ambulance included. After this long
        # others squeeze past them, as drivers do (otherwise SUMO waits
        # 5 minutes and removes them). Tested on 8 heavy-traffic trips:
        # the ambulance stood still inside junctions ~60% less.
        "--ignore-junction-blocker", str(JUNCTION_BLOCKER_SECONDS),
        # A collision (e.g. a car waved through a junction by police)
        # only logs a warning; by default SUMO teleports the cars
        # involved, which made an ambulance vanish mid-trip.
        "--collision.action", "warn",
    ]

    # --start and --delay are SUMO-GUI options only.
    if USE_SUMO_GUI:
        command += ["--start", "--delay", "500"]

    return command


def apply_city_speed_limits():
    """Cap every road's speed limit at CITY_SPEED_LIMIT (call once)."""

    for edge_id in traci.edge.getIDList():

        if edge_id.startswith(":"):
            continue

        try:
            current = traci.lane.getMaxSpeed(f"{edge_id}_0")
        except traci.TraCIException:
            continue

        if current > CITY_SPEED_LIMIT:
            traci.edge.setMaxSpeed(edge_id, CITY_SPEED_LIMIT)


# ---------------------------------------------------------
# SUMO coordinate conversion
# ---------------------------------------------------------
#
# The network uses UTM zone 43N (EPSG:32643) shifted by netOffset:
#   SUMO x = projected_x + NET_OFFSET_X
#   SUMO y = projected_y + NET_OFFSET_Y
# ---------------------------------------------------------

NET_OFFSET_X = -373877.28
NET_OFFSET_Y = -2045856.76

utm_to_wgs84 = Transformer.from_crs(
    "EPSG:32643",
    "EPSG:4326",
    always_xy=True,
)


def sumo_to_latlon_many(points):
    """sumo_to_latlon for many (x, y) points at once (much faster)."""

    if not points:
        return []
    xs = [x - NET_OFFSET_X for x, _ in points]
    ys = [y - NET_OFFSET_Y for _, y in points]
    longitudes, latitudes = utm_to_wgs84.transform(xs, ys)
    return [
        {"latitude": round(latitude, 7), "longitude": round(longitude, 7)}
        for latitude, longitude in zip(latitudes, longitudes)
    ]


def sumo_to_latlon(x: float, y: float):
    """Convert SUMO x/y to {"latitude", "longitude"}."""

    longitude, latitude = utm_to_wgs84.transform(
        x - NET_OFFSET_X,
        y - NET_OFFSET_Y,
    )

    return {
        "latitude": round(latitude, 7),
        "longitude": round(longitude, 7),
    }
