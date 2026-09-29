"""
Plans ambulance routes on the SUMO road network (no running simulation
needed): snaps a start point and a hospital to the nearest roads, then
finds the fastest route at Pune city speed limits. Waiting at signals is
ignored on purpose: the corridor turns them green for the ambulance.
"""

import json
import math
import os
import warnings
from functools import lru_cache

import sumolib

from simulation.sumo.sumo_bridge import (
    AMBULANCE_MAX_SPEED,
    CITY_SPEED_LIMIT,
    NET_FILE,
    SUMO_NETWORK_DIR,
    sumo_to_latlon,
)

# Without the optional rtree package sumolib searches roads by brute
# force, which is fast enough for this network; hide its warning.
warnings.filterwarnings("ignore", message=".*rtree.*")

HOSPITALS_FILE = os.path.join(SUMO_NETWORK_DIR, "hospitals.json")

VEHICLE_CLASS = "emergency"

# How far from a clicked / searched point we look for a drivable road (m).
# The highlighted area also covers the river and parks (e.g. Bund Garden),
# where the nearest road for an ambulance can be over 500 m away.
SNAP_RADIUS_METERS = 1500


class PlanningError(ValueError):
    """The trip cannot be planned (outside the area, no road, no route)."""


@lru_cache(maxsize=1)
def _net():
    net = sumolib.net.readNet(NET_FILE)

    # Fastest for the ambulance: road limits (capped at 50 km/h like the
    # live simulation) and never above the ambulance's own top speed.
    for edge in net.getEdges():
        edge._speed = min(edge.getSpeed(), CITY_SPEED_LIMIT, AMBULANCE_MAX_SPEED)

    return net


# The network file's bounding box is much bigger than the part that has
# roads. The simulated area is the outline around the grid cells that
# contain enough road (cell size in metres, road points per cell).
COVERAGE_CELL_METERS = 500
COVERAGE_MIN_POINTS = 5


def _convex_hull(points):
    """Convex hull (counter-clockwise) of (x, y) points."""

    points = sorted(set(points))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def half(sequence):
        hull = []
        for point in sequence:
            while len(hull) >= 2 and cross(hull[-2], hull[-1], point) <= 0:
                hull.pop()
            hull.append(point)
        return hull[:-1]

    return half(points) + half(reversed(points))


@lru_cache(maxsize=1)
def _coverage_outline():
    """Outline of the part of the network that has roads, in SUMO x/y."""

    net = _net()
    xmin, ymin, _, _ = net.getBoundary()
    cell = COVERAGE_CELL_METERS

    counts = {}
    for edge in net.getEdges():
        if edge.getFunction() == "internal" or not edge.allows(VEHICLE_CLASS):
            continue
        for x, y in edge.getShape():
            key = (int((x - xmin) // cell), int((y - ymin) // cell))
            counts[key] = counts.get(key, 0) + 1

    corners = []
    for (i, j), count in counts.items():
        if count >= COVERAGE_MIN_POINTS:
            x, y = xmin + i * cell, ymin + j * cell
            corners += [(x, y), (x + cell, y), (x, y + cell), (x + cell, y + cell)]

    return _convex_hull(corners)


@lru_cache(maxsize=1)
def area():
    """
    The simulated area: {"south", "west", "north", "east"} around it and
    "outline", the area itself as [[latitude, longitude], ...].
    """

    outline = [sumo_to_latlon(x, y) for x, y in _coverage_outline()]
    latitudes = [point["latitude"] for point in outline]
    longitudes = [point["longitude"] for point in outline]

    return {
        "south": min(latitudes),
        "west": min(longitudes),
        "north": max(latitudes),
        "east": max(longitudes),
        "outline": [[point["latitude"], point["longitude"]] for point in outline],
    }


@lru_cache(maxsize=1)
def hospitals():
    """Named hospitals inside the area (scripts/.../extract_hospitals.py)."""
    with open(HOSPITALS_FILE) as file:
        return json.load(file)


def inside_area(latitude, longitude):
    """Is the point inside the simulated area's outline?"""

    x, y = _net().convertLonLat2XY(longitude, latitude)
    outline = _coverage_outline()

    # Inside a counter-clockwise convex polygon: left of every side.
    for (x1, y1), (x2, y2) in zip(outline, outline[1:] + outline[:1]):
        if (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1) < 0:
            return False
    return True


def _nearby_roads(latitude, longitude):
    """Drivable roads near a point, nearest first:
    [(edge, position, distance)]."""

    net = _net()
    x, y = net.convertLonLat2XY(longitude, latitude)

    candidates = []
    for edge, distance in net.getNeighboringEdges(
        x, y, SNAP_RADIUS_METERS, includeJunctions=False
    ):
        if edge.getFunction() == "internal" or not edge.allows(VEHICLE_CLASS):
            continue

        # Position along the road closest to the point.
        position, _ = sumolib.geomhelper.polygonOffsetAndDistanceToPoint(
            (x, y), edge.getShape()
        )
        candidates.append((distance, edge, max(0.0, position)))

    candidates.sort(key=lambda item: item[0])
    return [
        (edge, position, distance)
        for distance, edge, position in candidates[:4]
    ]


def _length_and_time(edges):
    """
    Metres and seconds along the route at the speed limit, including the
    stretch through each junction (15-20% of the distance in central Pune).
    """

    length = 0.0
    seconds = 0.0

    for index, edge in enumerate(edges):
        length += edge.getLength()
        seconds += edge.getLength() / edge.getSpeed()

        # Through the junction: the gap between this road's end and the
        # next road's start (the junction lane runs almost straight).
        if index + 1 < len(edges):
            gap = math.dist(
                edge.getShape()[-1], edges[index + 1].getShape()[0]
            )
            length += gap
            seconds += gap / min(CITY_SPEED_LIMIT, AMBULANCE_MAX_SPEED)

    return length, seconds


def _route_geometry(edges):
    points = []
    for edge in edges:
        for x, y in edge.getShape():
            position = sumo_to_latlon(x, y)
            points.append([position["latitude"], position["longitude"]])
    return points


def roads_geometry(road_ids):
    """[[latitude, longitude], ...] along a list of road ids."""
    net = _net()
    return _route_geometry([net.getEdge(road_id) for road_id in road_ids])


# OpenStreetMap names that do not help anyone find a junction.
_GENERIC_NAMES = {"underpass", "flyover", "service road", "bridge", "subway"}
_NAME_SUFFIXES = {"road", "rd", "marg", "path", "street", "st", "lane", "setu"}


def _name_key(name):
    """"Veer Santaji Ghorpade Marg" and "... Road" are the same street."""
    words = [word for word in name.lower().split() if word not in _NAME_SUFFIXES]
    return " ".join(words)


@lru_cache(maxsize=4096)
def signal_name(signal_id, along_road_id=None):
    """
    A readable name for a signal: the street the ambulance is on and a
    street crossing it ("Bund Garden Road × Mangaldas Road"), or
    "<street> junction" when only one name is known, or None.
    """

    net = _net()
    try:
        connections = net.getTLS(signal_id).getConnections()
    except KeyError:
        return None

    names = []
    along = ""
    if along_road_id:
        try:
            along = net.getEdge(along_road_id).getName()
        except KeyError:
            along = ""
    if along:
        names.append(along)

    for in_lane, out_lane, _ in connections:
        for edge in (in_lane.getEdge(), out_lane.getEdge()):
            name = edge.getName()
            if name and name.lower() not in _GENERIC_NAMES:
                names.append(name)

    distinct = []
    for name in names:
        if name.lower() in _GENERIC_NAMES:
            continue
        if _name_key(name) not in {_name_key(item) for item in distinct}:
            distinct.append(name)

    if len(distinct) >= 2:
        return f"{distinct[0]} × {distinct[1]}"
    if distinct:
        return f"{distinct[0]} junction"
    return None


@lru_cache(maxsize=4096)
def junction_info(road_id):
    """
    The junction a vehicle on this road is heading into (or is inside,
    for a ":<junction>_<n>" road): {"id", "name", "signal", "latitude",
    "longitude"}, or None. name: "A Road × B Road", "A Road junction",
    or None when no street there has a name.
    """

    net = _net()
    try:
        if road_id.startswith(":"):
            node = net.getNode(road_id[1:].rsplit("_", 1)[0])
        else:
            node = net.getEdge(road_id).getToNode()
    except KeyError:
        return None

    distinct = []
    for edge in node.getIncoming() + node.getOutgoing():
        name = edge.getName()
        if not name or name.lower() in _GENERIC_NAMES:
            continue
        if _name_key(name) not in {_name_key(item) for item in distinct}:
            distinct.append(name)

    if len(distinct) >= 2:
        name = f"{distinct[0]} × {distinct[1]}"
    elif distinct:
        name = f"{distinct[0]} junction"
    else:
        name = None

    x, y = node.getCoord()
    return {
        "id": node.getID(),
        "name": name,
        "signal": "traffic_light" in node.getType(),
        **sumo_to_latlon(x, y),
    }


def _route_signals(edges):
    """Signals the route passes through, in order, with positions."""

    signals = []
    for edge, next_edge in zip(edges, edges[1:]):
        connections = edge.getConnections(next_edge)
        signal_id = connections[0].getTLSID() if connections else ""
        if not signal_id:
            continue
        # A big junction run by one signal can be crossed several times
        # in a row; it is one signal for the driver.
        if signals and signals[-1]["signal_id"] == signal_id:
            continue
        x, y = edge.getToNode().getCoord()
        signals.append({
            "number": len(signals) + 1,
            "signal_id": signal_id,
            "name": signal_name(signal_id, edge.getID()),
            **sumo_to_latlon(x, y),
        })
    return signals


def _street_names(edges, limit=4):
    """The main streets along the route, in order, without repeats."""

    names = []
    for edge in edges:
        name = edge.getName()
        if name and name not in names:
            names.append(name)
    return names[:limit]


def _best_path(starts, ends, fastest):
    best = None
    for start_edge, start_position, start_distance in starts:
        for end_edge, end_position, end_distance in ends:
            path, cost = _net().getOptimalPath(
                start_edge,
                end_edge,
                fastest=fastest,
                vClass=VEHICLE_CLASS,
                fromPos=start_position,
                toPos=end_position,
            )
            if path and (best is None or cost < best[1]):
                best = (path, cost, start_position, end_position,
                        start_distance, end_distance)
    return best


def plan_route(start_latitude, start_longitude, end_latitude, end_longitude,
               hospital_name="Hospital"):
    """
    Fastest ambulance route between two points.

    Returns {"roads", "geometry", "length_meters", "minutes_without_traffic",
    "signals", "streets", "depart_position", "arrival_position",
    "hospital_name", "shorter_alternative", "signalless_stretches",
    "police_along_route"} or raises PlanningError.
    """

    for label, latitude, longitude in (
        ("The start point", start_latitude, start_longitude),
        (hospital_name, end_latitude, end_longitude),
    ):
        if not inside_area(latitude, longitude):
            raise PlanningError(
                f"{label} is outside the simulated area of central Pune. "
                "Pick a point inside the highlighted area on the map."
            )

    starts = _nearby_roads(start_latitude, start_longitude)
    if not starts:
        raise PlanningError(
            "No simulated road within 1.5 km of the start point. "
            "Pick a point on or next to a main road."
        )

    ends = _nearby_roads(end_latitude, end_longitude)
    if not ends:
        raise PlanningError(f"No drivable road near {hospital_name}.")

    fastest = _best_path(starts, ends, fastest=True)
    if fastest is None:
        raise PlanningError(
            "No route found between these points for an ambulance."
        )

    (edges, _, depart_position, arrival_position,
     start_distance, end_distance) = fastest
    length, seconds = _length_and_time(edges)

    result = {
        "roads": [edge.getID() for edge in edges],
        "geometry": _route_geometry(edges),
        "length_meters": round(length),
        "minutes_without_traffic": round(seconds / 60, 1),
        "signals": _route_signals(edges),
        "streets": _street_names(edges),
        "depart_position": round(depart_position, 1),
        "arrival_position": round(arrival_position, 1),
        "hospital_name": hospital_name,
        "start_snap_meters": round(start_distance),
        "hospital_snap_meters": round(end_distance),
        "shorter_alternative": None,
    }

    # AI estimate of the real trip time per traffic level (ai/pretrip.py);
    # None until that model is trained.
    from ai.pretrip import estimate_minutes, route_features
    result["estimated_minutes"] = estimate_minutes(route_features(
        edges, result["length_meters"],
        result["minutes_without_traffic"] * 60, len(result["signals"]),
    ))

    # Is there a shorter (but slower) route? Worth telling the user.
    shortest = _best_path(starts, ends, fastest=False)
    if shortest and shortest[0] != edges:
        shortest_length, shortest_seconds = _length_and_time(shortest[0])
        # Only when it really is slower, in the same terms as shown.
        if shortest_length < length - 50 and shortest_seconds > seconds + 6:
            result["shorter_alternative"] = {
                "length_meters": round(shortest_length),
                "minutes_without_traffic": round(shortest_seconds / 60, 1),
            }

    # Stretches without signals, and the police who can clear them.
    stretches = signalless_stretches(edges)
    result["police_along_route"] = police_cover(edges, stretches)
    result["signalless_stretches"] = stretches

    return result


def junction_roads(roads):
    """
    For each road of a route, the junction road ("box") leading from it
    to the next road, or None: [box id or None, ...]. Cars stopped in
    these boxes are the sign of a deadlock.
    """

    net = _net()
    boxes = []
    for road, next_road in zip(roads, roads[1:]):
        box = None
        try:
            connections = net.getEdge(road).getConnections(net.getEdge(next_road))
            via = connections[0].getViaLaneID() if connections else ""
            if via:
                box = via.rsplit("_", 1)[0]
        except KeyError:
            pass
        boxes.append(box)
    boxes.append(None)
    return boxes


POLICE_FILE = os.path.join(SUMO_NETWORK_DIR, "police_stations.json")


@lru_cache(maxsize=1)
def police_stations():
    """Police stations inside the area (scripts/route_building/
    extract_police.py), each with the nearest road a vehicle can leave
    from: [{"name", "name_local", "kind", "latitude", "longitude", "road",
    "road_name"}]. kind: "Police station" or "Police chowki" (a small post)."""

    if not os.path.exists(POLICE_FILE):
        return []
    with open(POLICE_FILE, encoding="utf-8") as file:
        stations = json.load(file)

    result = []
    for station in stations:
        roads = _nearby_roads(station["latitude"], station["longitude"])
        if roads:
            result.append({
                **station,
                "kind": (
                    "Police chowki" if "chowki" in station["name"].lower()
                    else "Police station"
                ),
                "road": roads[0][0].getID(),
                # The nearest named street, for "on <street>".
                "road_name": next(
                    (edge.getName() for edge, _, _ in roads if edge.getName()),
                    None,
                ),
            })
    return result


def road_name(road_id):
    """Street name of a road, or None."""
    try:
        return _net().getEdge(road_id).getName() or None
    except KeyError:
        return None


def estimate_route_seconds(roads, traffic_level):
    """
    Realistic ambulance time for a route (road ids) with the AI trip-time
    model (ai/pretrip.py): seconds, or None if the model is not trained.
    Used to compare the current route with a detour fairly (live travel
    times alone make detours through side streets look too good).
    """

    from ai.pretrip import TRAFFIC_LEVELS, estimate_minutes, route_features

    net = _net()
    try:
        edges = [net.getEdge(road) for road in roads if not road.startswith(":")]
    except KeyError:
        return None
    if not edges:
        return None

    length, seconds = _length_and_time(edges)
    minutes = estimate_minutes(route_features(
        edges, length, seconds, len(_route_signals(edges)),
    ))
    if minutes is None:
        return None
    return minutes[TRAFFIC_LEVELS[traffic_level]] * 60


# ---------------------------------------------------------
# Police cover for the signal-less stretches of a route
# ---------------------------------------------------------
#
# The corridor engine clears queues at signals by turning them green.
# Between signals (and at junctions without a signal) nothing can, so
# the police there are told when that part of the route jams
# (corridor/police_watch.py).

# Stretches shorter than this are left out (a queue there belongs to
# the junction before or after it).
MIN_STRETCH_METERS = 100

# Stations tried per stretch (nearest in a straight line first), and the
# longest police drive worth asking for.
POLICE_CANDIDATES = 3
MAX_POLICE_DRIVE_SECONDS = 10 * 60


def _signalled(node):
    return node.getType().startswith("traffic_light")


def signalless_stretches(edges):
    """
    Parts of a route where no signal controls the traffic: runs of roads
    whose end is not a signal. [{"start_index", "end_index",
    "length_meters", "name", "latitude", "longitude", "geometry"}] in
    route order (indices into edges).
    """

    runs, run = [], []
    for index, edge in enumerate(edges):
        if _signalled(edge.getToNode()):
            if run:
                runs.append(run)
            run = []
        else:
            run.append(index)
    if run:
        runs.append(run)

    stretches = []
    for run in runs:
        run_edges = [edges[index] for index in run]
        length = sum(edge.getLength() for edge in run_edges)
        if length < MIN_STRETCH_METERS:
            continue
        middle = run_edges[len(run_edges) // 2].getShape()
        x, y = middle[len(middle) // 2]
        stretches.append({
            "start_index": run[0],
            "end_index": run[-1],
            "length_meters": round(length),
            "name": next(
                (edge.getName() for edge in run_edges if edge.getName()),
                "an unnamed road",
            ),
            **sumo_to_latlon(x, y),
            "geometry": _route_geometry(run_edges),
        })
    return stretches


@lru_cache(maxsize=4096)
def _police_drive_seconds(from_road, to_road):
    net = _net()
    path, seconds = net.getOptimalPath(
        net.getEdge(from_road), net.getEdge(to_road),
        fastest=True, vClass=VEHICLE_CLASS,
    )
    return seconds if path else None


def police_cover(edges, stretches):
    """
    Which stations can reach each stretch first, by road (a station
    across the river can be close in a straight line but far by road).
    Adds "number" and "stations" ([{"name", "road", "drive_seconds"}],
    fastest first) to every stretch. Returns the first-choice stations
    in route order: [{"name", "latitude", "longitude", "covers":
    [{"stretch", "road", "drive_seconds"}]}].
    """

    net = _net()
    stations = police_stations()
    points = {
        station["name"]: net.convertLonLat2XY(
            station["longitude"], station["latitude"]
        )
        for station in stations
    }

    along_route = {}
    for number, stretch in enumerate(stretches, 1):
        stretch["number"] = number
        target = edges[stretch["start_index"]]
        start = target.getShape()[0]

        nearest = sorted(
            stations,
            key=lambda station: math.dist(start, points[station["name"]]),
        )[:POLICE_CANDIDATES]

        options = []
        for station in nearest:
            seconds = _police_drive_seconds(station["road"], target.getID())
            if seconds is not None and seconds <= MAX_POLICE_DRIVE_SECONDS:
                options.append({
                    "name": station["name"],
                    "road": station["road"],
                    "drive_seconds": round(seconds),
                })
        options.sort(key=lambda option: option["drive_seconds"])
        stretch["stations"] = options

        if options:
            first = next(s for s in stations if s["name"] == options[0]["name"])
            entry = along_route.setdefault(first["name"], {
                "name": first["name"],
                "latitude": first["latitude"],
                "longitude": first["longitude"],
                "covers": [],
            })
            entry["covers"].append({
                "stretch": number,
                "road": stretch["name"],
                "drive_seconds": options[0]["drive_seconds"],
            })

    return list(along_route.values())


def route_police_plan(road_ids):
    """signalless_stretches + police_cover for a route given as road ids
    (e.g. after a re-route): (stretches, police_along_route)."""

    net = _net()
    edges = [net.getEdge(road_id) for road_id in road_ids]
    stretches = signalless_stretches(edges)
    return stretches, police_cover(edges, stretches)
