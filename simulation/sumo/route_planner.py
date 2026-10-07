"""
Plans ambulance routes on the SUMO road network (no running simulation
needed): snaps a start point and a hospital to the nearest roads, then
finds the fastest route at Pune city speed limits. Waiting at signals is
ignored on purpose: the corridor turns them green for the ambulance.
"""

import json
import math
import os
import threading
import warnings
from functools import lru_cache

import sumolib

from simulation.sumo.sumo_bridge import (
    AMBULANCE_MAX_SPEED,
    CITY_SPEED_LIMIT,
    HOSPITALS_FILE,
    NET_FILE,
    POLICE_FILE,
    sumo_to_latlon,
)

# Without the optional rtree package sumolib searches roads by brute
# force, which is fast enough for this network; hide its warning.
warnings.filterwarnings("ignore", message=".*rtree.*")


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


# sumolib's road search uses the rtree library when it is installed,
# which crashes Python (segmentation fault) when two threads search at
# once - and the API answers requests on several threads. One at a time.
_SEARCH_LOCK = threading.Lock()

# Roads offered around a point, nearest first.
NEARBY_ROADS = 4


@lru_cache(maxsize=1)
def _reachable_roads():
    """Ids of the roads an ambulance can reach from the rest of the city.
    The OpenStreetMap data has a few pockets (e.g. campus or one-way
    roads) it can drive out of but not into."""

    net = _net()
    roads = [
        edge for edge in net.getEdges()
        if edge.getFunction() != "internal" and edge.allows(VEHICLE_CLASS)
    ]
    # Start from a main road: most lanes, then fastest.
    hub = max(roads, key=lambda edge: (edge.getLaneNumber(), edge.getSpeed()))
    return {edge.getID() for edge in net.getReachable(hub, vclass=VEHICLE_CLASS)}


@lru_cache(maxsize=1)
def _round_trip_roads():
    """Ids of the roads an ambulance can both reach and leave again for
    the rest of the city. At the edges of the simulated map some roads
    only lead off the map, or only come in and end there."""

    net = _net()
    hub = net.getEdge(next(iter(_hub_road())))
    leading_back = net.getReachable(hub, vclass=VEHICLE_CLASS, useIncoming=True)
    return _reachable_roads() & {edge.getID() for edge in leading_back}


@lru_cache(maxsize=1)
def _hub_road():
    """{id} of the main road the reachability checks start from."""
    roads = [
        edge for edge in _net().getEdges()
        if edge.getFunction() != "internal" and edge.allows(VEHICLE_CLASS)
    ]
    return {max(roads, key=lambda edge: (edge.getLaneNumber(), edge.getSpeed())).getID()}


def _road_candidates(latitude, longitude, radius=SNAP_RADIUS_METERS):
    """Every drivable road within radius of a point, nearest first:
    [(distance, edge, position)]."""

    net = _net()
    x, y = net.convertLonLat2XY(longitude, latitude)

    with _SEARCH_LOCK:
        neighbours = net.getNeighboringEdges(x, y, radius, includeJunctions=False)

    candidates = []
    for edge, distance in neighbours:
        if edge.getFunction() == "internal" or not edge.allows(VEHICLE_CLASS):
            continue

        # Position along the road closest to the point.
        position, _ = sumolib.geomhelper.polygonOffsetAndDistanceToPoint(
            (x, y), edge.getShape()
        )
        candidates.append((distance, edge, max(0.0, position)))

    candidates.sort(key=lambda item: item[0])
    return candidates


def _nearby_roads(latitude, longitude):
    """Drivable roads near a point, nearest first:
    [(edge, position, distance)]."""

    candidates = _road_candidates(latitude, longitude)
    nearest = candidates[:NEARBY_ROADS]
    reachable = _reachable_roads()
    if not any(edge.getID() in reachable for _, edge, _ in nearest):
        # None of the nearest roads can be reached from the city (a
        # pocket): also offer the nearest that can, after them; the
        # ambulance stops there when it has to drive here.
        nearest += [
            item for item in candidates[NEARBY_ROADS:] if item[1].getID() in reachable
        ][:NEARBY_ROADS]
    return [
        (edge, position, distance)
        for distance, edge, position in nearest
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
    """The best path between the nearest roads; the extra roads
    _nearby_roads offers around a pocket only when there is none."""
    best = _search_paths(starts[:NEARBY_ROADS], ends[:NEARBY_ROADS], fastest)
    if best is None and (len(starts) > NEARBY_ROADS or len(ends) > NEARBY_ROADS):
        best = _search_paths(starts, ends, fastest)
    return best


def _search_paths(starts, ends, fastest):
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

    return _plan_between(starts, ends, hospital_name)


def _plan_between(starts, ends, hospital_name):
    """plan_route's result for the fastest route from one of starts to
    one of ends ([(edge, position, distance)])."""

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


# ---------------------------------------------------------
# A second ambulance crossing the first one's path (demo)
# ---------------------------------------------------------

# An ambulance's travel time estimate for the crossing route: the time
# at the speed limit, times this for traffic (its own trip will vary;
# the junction referee handles whatever actually happens).
CROSSING_TRAFFIC_FACTOR = 1.3

# Side roads counted as "crossing": this far from the first ambulance's
# direction (degrees).
CROSSING_MIN_ANGLE = 50
CROSSING_MAX_ANGLE = 130


def _heading(edge, at_end=True):
    """Direction of a road (degrees) where it enters / leaves a junction."""
    shape = edge.getShape()
    (x1, y1), (x2, y2) = (shape[-2], shape[-1]) if at_end else (shape[0], shape[1])
    return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 360


def _turn(heading_from, heading_to):
    """Angle between two directions, 0-180."""
    difference = abs(heading_from - heading_to) % 360
    return min(difference, 360 - difference)


def _drivable(edge):
    return edge.getFunction() != "internal" and edge.allows(VEHICLE_CLASS)


def _seconds(edge):
    return edge.getLength() / edge.getSpeed() * CROSSING_TRAFFIC_FACTOR


def _approach(entry, seconds):
    """Roads leading straight-ish into `entry`, about `seconds` of
    driving long (entry included, in driving order)."""

    path = [entry]
    total = _seconds(entry)
    while total < seconds and len(path) < 80:
        current = path[0]
        options = [
            edge for edge in current.getIncoming()
            if _drivable(edge)
            and edge not in path
            and edge.getFromNode() != current.getToNode()  # no U-turn
        ]
        if not options:
            break
        straightest = min(
            options,
            key=lambda edge: _turn(_heading(edge), _heading(current, at_end=False)),
        )
        path.insert(0, straightest)
        total += _seconds(straightest)
    return path, total


def _to_nearest_hospital(first_edge):
    """Fastest path from a road to the nearest hospital:
    (edges, arrival position, hospital) or None."""

    best = None
    for hospital in hospitals():
        for end_edge, end_position, _ in _nearby_roads(
            hospital["latitude"], hospital["longitude"]
        )[:2]:
            path, cost = _net().getOptimalPath(
                first_edge, end_edge, fastest=True, vClass=VEHICLE_CLASS,
                toPos=end_position,
            )
            if path and (best is None or cost < best[0]):
                best = (cost, list(path), end_position, hospital)
    return best[1:] if best else None


def crossing_trip(signal_id, in_road, seconds_to_signal, skip_entries=()):
    """
    Demo trip for a second ambulance: it reaches the signal `signal_id`
    (which the first ambulance crosses coming from `in_road`) from a side
    road about `seconds_to_signal` from now, crosses the first
    ambulance's path there and continues to the nearest hospital.

    Returns a trip dict like the planner's (roads, depart_position,
    arrival_position, start/hospital names and points, stretches without
    signals, police cover) or None when no crossing road fits.
    skip_entries: side roads already used by other crossing ambulances.
    """

    net = _net()
    first_in = net.getEdge(in_road)
    node = first_in.getToNode()
    first_heading = _heading(first_in)

    side_roads = []
    for edge in node.getIncoming():
        if edge == first_in or not _drivable(edge) or edge.getID() in skip_entries:
            continue
        angle = _turn(_heading(edge), first_heading)
        if CROSSING_MIN_ANGLE <= angle <= CROSSING_MAX_ANGLE:
            side_roads.append((abs(angle - 90), edge))

    for _, entry in sorted(side_roads, key=lambda item: item[0]):
        # Straight on through the junction, through the same signal.
        exits = [
            (edge, connections)
            for edge, connections in entry.getOutgoing().items()
            if _drivable(edge) and edge.getToNode() != entry.getFromNode()
            and any(c.getTLSID() == signal_id for c in connections)
        ]
        if not exits:
            continue
        exit_edge, _ = min(
            exits,
            key=lambda item: _turn(_heading(entry), _heading(item[0], at_end=False)),
        )

        approach, _ = _approach(entry, seconds_to_signal)
        onward = _to_nearest_hospital(exit_edge)
        if onward is None:
            continue
        rest, arrival_position, hospital = onward

        edges = approach + rest
        stretches = signalless_stretches(edges)
        start_x, start_y = edges[0].getShape()[0]
        start = sumo_to_latlon(start_x, start_y)
        start_name = next(
            (edge.getName() for edge in approach if edge.getName()),
            "a side road",
        )
        return {
            "roads": [edge.getID() for edge in edges],
            "depart_position": 0.0,
            "arrival_position": round(arrival_position, 1),
            "start_name": start_name,
            "hospital_name": hospital["name"],
            "start_point": [start["latitude"], start["longitude"]],
            "hospital_point": [hospital["latitude"], hospital["longitude"]],
            "stretches": stretches,
            "police_along_route": police_cover(edges, stretches),
            "crosses": signal_name(signal_id, entry.getID()),
            "crossing_entry": entry.getID(),
        }

    return None


def crossing_trip_for_route(road_ids, skip_entries=(), min_seconds=45, max_seconds=420):
    """
    Before departure: a crossing trip (crossing_trip) for another
    ambulance that sets off together with the one on `road_ids`, meeting
    it at one of its signals min_seconds..max_seconds after the start.
    skip_entries: side roads already used by other crossing ambulances
    (a second one can come from the other side of the same signal).
    Returns the trip or None.
    """

    net = _net()
    edges = [net.getEdge(road_id) for road_id in road_ids]
    seconds = 0.0
    for edge, next_edge in zip(edges, edges[1:]):
        seconds += _seconds(edge)
        connections = edge.getConnections(next_edge)
        signal_id = connections[0].getTLSID() if connections else ""
        if not signal_id:
            continue
        if seconds > max_seconds:
            break
        if seconds >= min_seconds:
            trip = crossing_trip(signal_id, edge.getID(), seconds, skip_entries)
            if trip is not None:
                return trip
    return None


# Conflict-aware routing uses the same immutable road network as the preview.
def corridor_profile(trip, offset=0.0):
    """Travel and signal arrival estimates, including partial endpoint roads."""
    edges = [_net().getEdge(road) for road in trip['roads']]
    seconds = offset
    visits = []
    for index, edge in enumerate(edges):
        start = float(trip.get('depart_position') or 0) if index == 0 else 0
        end = (trip.get('arrival_position') if index == len(edges) - 1 else None)
        end = edge.getLength() if end is None else float(end)
        seconds += max(0, end - start) / edge.getSpeed() * CROSSING_TRAFFIC_FACTOR
        if index == len(edges) - 1:
            continue
        next_edge = edges[index + 1]
        connections = edge.getConnections(next_edge)
        signal_id = connections[0].getTLSID() if connections else ''
        if signal_id and (not visits or visits[-1]['signal_id'] != signal_id):
            x, y = edge.getToNode().getCoord()
            visits.append({
                'signal_id': signal_id, 'name': signal_name(signal_id, edge.getID()),
                'seconds': seconds, 'movement': (edge.getID(), next_edge.getID()),
                **sumo_to_latlon(x, y),
            })
    return {'roads': trip['roads'], 'seconds': seconds - offset, 'visits': visits}


def _avoiding_signals(trip, avoided):
    """Dijkstra over permitted movements, without mutating SUMO's network."""
    import heapq
    net = _net()
    start, end = trip['roads'][0], trip['roads'][-1]
    if start == end:
        return None  # retain the original, including any necessary loop
    queue = [(0.0, start)]
    costs, previous = {start: 0.0}, {}
    while queue:
        cost, road = heapq.heappop(queue)
        if cost != costs[road]:
            continue
        if road == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return list(reversed(path))
        edge = net.getEdge(road)
        for outgoing, connections in edge.getAllowedOutgoing(VEHICLE_CLASS).items():
            if not _drivable(outgoing) or any(c.getTLSID() in avoided for c in connections):
                continue
            following = outgoing.getID()
            candidate = cost + _seconds(outgoing)
            if candidate < costs.get(following, float('inf')):
                costs[following] = candidate
                previous[following] = road
                heapq.heappush(queue, (candidate, following))
    return None


def avoiding_roads(start, end, blocked):
    """Shortest legal emergency route around blocked roads after ``start``.

    The ambulance cannot leave its current edge, so ``start`` is always
    retained. Return None when the destination or every path is blocked.
    """
    import heapq

    if end in blocked and end != start:
        return None
    net = _net()
    try:
        net.getEdge(start)
        net.getEdge(end)
    except KeyError:
        return None
    queue = [(0.0, start)]
    costs, previous = {start: 0.0}, {}
    while queue:
        cost, road = heapq.heappop(queue)
        if cost != costs[road]:
            continue
        if road == end:
            path = [end]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return list(reversed(path))
        edge = net.getEdge(road)
        for outgoing in edge.getAllowedOutgoing(VEHICLE_CLASS):
            following = outgoing.getID()
            if following in blocked or not _drivable(outgoing):
                continue
            candidate = cost + _seconds(outgoing)
            if candidate < costs.get(following, float('inf')):
                costs[following] = candidate
                previous[following] = road
                heapq.heappush(queue, (candidate, following))
    return None


def coordinate_trip(trip, condition, existing=(), label='Ambulance', offset=0.0):
    """Choose a route against already accepted corridors; preserve endpoints.

    Bounded search: original, fastest, and detours avoiding up to six shared
    signals individually or together. No claim of global fleet optimality.
    """
    from corridor.routing import select_route
    base = corridor_profile(trip, offset)
    shared = sorted({v['signal_id'] for r in existing for v in r['visits']} &
                    {v['signal_id'] for v in base['visits']})[:6]
    paths = [trip['roads']]
    # A journey via the patient keeps its route: a detour around a shared
    # signal could skip the pickup. It can still get a planned give-way.
    if shared and not trip.get('pickup'):
        for avoided in [set(), *({s} for s in shared), set(shared)]:
            roads = _avoiding_signals(trip, avoided)
            if roads and roads not in paths:
                paths.append(roads)
    candidates = [corridor_profile({**trip, 'roads': roads}, offset) for roads in paths]
    selected, decision = select_route(candidates, existing, condition, label)
    result = {**trip, 'roads': selected['roads'], 'routing_decision': decision}
    if decision['changed']:
        result['stretches'], result['police_along_route'] = route_police_plan(selected['roads'])
        result['crosses'] = None  # the generated crossing may have been avoided
    names = ', '.join(dict.fromkeys(r['label'] for r in existing
                     if any(v['signal_id'] in shared for v in r['visits'])))
    locations = ', '.join(dict.fromkeys(v['name'] or v['signal_id'] for v in base['visits']
                         if v['signal_id'] in shared))
    if decision['changed']:
        decision['explanation'] = f"Route changed to avoid {names} at {locations}."
    elif decision['estimated_wait_seconds']:
        decision['explanation'] = (
            f"Route kept. Expect ~{decision['estimated_wait_seconds']:.0f} s give-way for {names}."
        )
    else:
        decision['explanation'] = 'Route kept. No delay expected.'
    return result, selected


def corridor_preview(trips, profiles):
    """All accepted routes and shared signal markers, ready before departure."""
    from corridor.routing import schedule
    scheduled = schedule(profiles)
    shared = {}
    for visit in scheduled['visits']:
        entry = shared.setdefault(visit['signal_id'], {
            key: visit[key] for key in ('signal_id', 'name', 'latitude', 'longitude')
        })
        entry.setdefault('visits', []).append(visit)
    return {
        'ambulances': [
            {'number': i, 'label': profile['label'], 'geometry': roads_geometry(trip['roads']),
             'start_name': trip['start_name'], 'hospital_name': trip['hospital_name'],
             'routing_decision': trip['routing_decision']}
            for i, (trip, profile) in enumerate(zip(trips, profiles), 1)
        ],
        'shared_junctions': [s for s in shared.values()
                             if len({v['ambulance'] for v in s['visits']}) > 1],
    }


# ---------------------------------------------------------
# Place search by the start of each word (the search box)
# ---------------------------------------------------------

def _words(text):
    return [word for word in text.lower().replace(",", " ").replace(".", " ").split() if word]


@lru_cache(maxsize=1)
def _named_places():
    """Streets (middle of their longest stretch), hospitals and police
    stations in the area: [(name, words, latitude, longitude, kind)]."""

    longest = {}
    for edge in _net().getEdges():
        name = edge.getName()
        if not name or edge.getFunction() == "internal" or not edge.allows(VEHICLE_CLASS):
            continue
        if name not in longest or edge.getLength() > longest[name].getLength():
            longest[name] = edge

    places = []
    for name, edge in longest.items():
        shape = edge.getShape()
        x, y = shape[len(shape) // 2]
        point = sumo_to_latlon(x, y)
        if inside_area(point["latitude"], point["longitude"]):
            places.append((name, _words(name), point["latitude"], point["longitude"], "street"))
    for item in hospitals():
        places.append((item["name"], _words(item["name"]), item["latitude"], item["longitude"], "hospital"))
    for item in police_stations():
        places.append((item["name"], _words(item["name"]), item["latitude"], item["longitude"], "police"))
    return places


def search_places(query, limit=6):
    """Places whose words start with every typed word, so "bund gard"
    finds "Bund Garden Road": [{"name", "latitude", "longitude"}]."""

    typed = _words(query)
    if not typed:
        return []
    matches = []
    for name, words, latitude, longitude, kind in _named_places():
        if all(any(word.startswith(part) for word in words) for part in typed):
            # Names starting with the first typed word first, then shorter.
            rank = (not words[0].startswith(typed[0]), kind != "street", len(name))
            matches.append((rank, {"name": name, "latitude": latitude, "longitude": longitude}))
    matches.sort(key=lambda item: item[0])
    return [place for _, place in matches[:limit]]


# A hospital or police station this close to a clicked point is "there".
PLACE_AT_METERS = 120


def place_at(latitude, longitude):
    """What is at a point, for a click on the map: {"name", "kind"
    ("hospital", "police" or "street"), "street", "latitude",
    "longitude", "inside_area"}. name is None when nothing is near."""

    def meters(item):
        return math.hypot(
            (item["latitude"] - latitude) * 111_320,
            (item["longitude"] - longitude) * 111_320 * math.cos(math.radians(latitude)),
        )

    street = next(
        (edge.getName() for edge, _, distance in _nearby_roads(latitude, longitude)
         if edge.getName() and distance <= 200),
        None,
    )
    place = {
        "name": street, "kind": "street", "street": street,
        "latitude": latitude, "longitude": longitude,
        "inside_area": inside_area(latitude, longitude),
    }
    for kind, items in (("hospital", hospitals()), ("police", police_stations())):
        near = min(items, key=meters, default=None)
        if near is not None and meters(near) <= PLACE_AT_METERS:
            return {**place, "name": near["name"], "kind": kind}
    return place


def drive_seconds(start_latitude, start_longitude, end_latitude, end_longitude):
    """Fastest ambulance driving time between two points at the speed
    limits (no traffic), or None when there is no route."""

    starts = _nearby_roads(start_latitude, start_longitude)
    ends = _nearby_roads(end_latitude, end_longitude)
    if not starts or not ends:
        return None
    best = _best_path(starts, ends, fastest=True)
    return best[1] if best else None


def drive_estimate(start_latitude, start_longitude, end_latitude, end_longitude,
                   traffic_level=None):
    """Ambulance driving time between two points (s): the AI trip-time
    estimate for this traffic level (index into ai.pretrip.TRAFFIC_LEVELS)
    when available, else the time at the speed limits; None if no route."""

    starts = _nearby_roads(start_latitude, start_longitude)
    ends = _nearby_roads(end_latitude, end_longitude)
    if not starts or not ends:
        return None
    best = _best_path(starts, ends, fastest=True)
    if best is None:
        return None
    if traffic_level is not None:
        estimate = estimate_route_seconds([edge.getID() for edge in best[0]], traffic_level)
        if estimate is not None:
            return estimate
    return best[1]


def hospital_road(latitude, longitude):
    """(road id, position) where an ambulance stops for a hospital, or None."""
    ends = _nearby_roads(latitude, longitude)
    if not ends:
        return None
    edge, position, _ = ends[0]
    return edge.getID(), round(position, 1)


# ---------------------------------------------------------
# The whole journey: ambulance base -> patient -> hospital
# ---------------------------------------------------------


def _pickup_roads(latitude, longitude):
    """Roads the ambulance may stop on for the patient: the nearest ones,
    and when none of them can be both reached and left again (e.g. at
    the edge of the map), also the nearest ones that can, within
    PICKUP_REACH_METERS (the crew carries the patient there)."""

    pickups = _nearby_roads(latitude, longitude)
    usable = _round_trip_roads()
    if any(edge.getID() in usable for edge, _, _ in pickups[:NEARBY_ROADS]):
        return pickups
    seen = {edge.getID() for edge, _, _ in pickups}
    extra = [
        (edge, position, distance)
        for distance, edge, position in _road_candidates(latitude, longitude, PICKUP_REACH_METERS)
        if edge.getID() in usable and edge.getID() not in seen
    ][:NEARBY_ROADS]
    return pickups + extra

# Time at the scene to assess and load the patient (s).
ON_SCENE_SECONDS = 180

# How far the crew may carry the patient to a road the ambulance can both
# reach and leave, when the nearest roads can't be used (map edges).
PICKUP_REACH_METERS = 300

# Carrying the patient on a stretcher to where the ambulance stops (m/s):
# choosing where to stop, a stop 150 m away costs ~2.5 min, so the
# ambulance stops next to the patient unless that is much slower to drive.
STRETCHER_SPEED = 1.0

# Ambulances are stationed at these hospital types (corridor/hospital_care.py).
BASE_TYPES = ("major", "general")

# Bases compared by driving time (the nearest ones in a straight line).
BASE_CANDIDATES = 4


def nearest_base(latitude, longitude):
    """The hospital the nearest ambulance leaves from, by driving time:
    {"name", "latitude", "longitude", "drive_seconds"} or None."""

    from corridor.hospital_care import hospital_type

    bases = [item for item in hospitals() if hospital_type(item["name"]) in BASE_TYPES]
    bases.sort(key=lambda item: math.hypot(
        item["latitude"] - latitude,
        (item["longitude"] - longitude) * math.cos(math.radians(latitude)),
    ))
    best = None
    for item in bases[:BASE_CANDIDATES]:
        seconds = drive_seconds(item["latitude"], item["longitude"], latitude, longitude)
        if seconds is not None and (best is None or seconds < best["drive_seconds"]):
            best = {**item, "drive_seconds": seconds}
    return best


def plan_journey(pickup_latitude, pickup_longitude, hospital_latitude,
                 hospital_longitude, hospital_name="Hospital", pickup_name="Patient",
                 base=None):
    """
    The ambulance's whole trip: from the nearest base (a hospital) to the
    patient, ON_SCENE_SECONDS there, then to the chosen hospital. The
    corridor covers both legs. Returns plan_route's fields for the whole
    trip plus "base", "pickup" ({"name", "latitude", "longitude",
    "road", "position", "route_index", "seconds"}), and
    "minutes_to_patient" / "minutes_to_hospital", or raises PlanningError.
    base: where the ambulance leaves from ({"name", "latitude",
    "longitude"}, e.g. a 108 station); default the nearest base hospital.
    """

    if base is None:
        base = nearest_base(pickup_latitude, pickup_longitude)
    if base is None:
        raise PlanningError("No ambulance base (hospital) can reach the patient.")

    for label, latitude, longitude in (
        ("The patient's location", pickup_latitude, pickup_longitude),
        (hospital_name, hospital_latitude, hospital_longitude),
    ):
        if not inside_area(latitude, longitude):
            raise PlanningError(f"{label} is outside the simulated area of central Pune.")
    starts = _nearby_roads(base["latitude"], base["longitude"])
    pickups = _pickup_roads(pickup_latitude, pickup_longitude)
    ends = _nearby_roads(hospital_latitude, hospital_longitude)
    if not starts or not pickups or not ends:
        raise PlanningError("No drivable road near the base, the patient or the hospital.")

    # The road the ambulance stops on for the patient: one of the roads
    # next to the patient from which it can also drive on to the
    # hospital (on some, e.g. a one-way street, it can't); the fastest
    # whole trip wins.
    best = None
    for pickup in pickups:
        try:
            to_patient = _plan_between(starts, [pickup], pickup_name)
            to_hospital = _plan_between(
                [(pickup[0], to_patient["arrival_position"], 0.0)], ends, hospital_name
            )
        except PlanningError:
            continue
        # The ambulance can't stop for the patient behind where it starts.
        if (len(to_patient["roads"]) == 1
                and to_patient["arrival_position"] <= to_patient["depart_position"] + 5):
            continue
        carry_minutes = pickup[2] / STRETCHER_SPEED / 60
        total = (
            to_patient["minutes_without_traffic"] + to_hospital["minutes_without_traffic"]
            + carry_minutes
        )
        if best is None or total < best[0]:
            best = (total, to_patient, to_hospital)
    if best is None:
        usable = next(
            (round(distance) for distance, edge, _ in _road_candidates(pickup_latitude, pickup_longitude)
             if edge.getID() in _round_trip_roads()),
            None,
        )
        if usable is None or usable > PICKUP_REACH_METERS:
            where = f"is {usable} m away" if usable is not None else "is more than 1.5 km away"
            raise PlanningError(
                "The patient is too far from a road an ambulance can reach and leave "
                f"(e.g. at the edge of the simulated map): the nearest one {where}. "
                "Move the pin onto a main road, a little further inside the area."
            )
        raise PlanningError("No route from the ambulance via the patient to the hospital.")
    _, to_patient, to_hospital = best

    first, second = to_patient["roads"], to_hospital["roads"]
    # The pickup is on the last road of the first leg; when the second leg
    # starts on that road, the ambulance simply carries on along it.
    roads = first + (second[1:] if second[0] == first[-1] else second)
    pickup_index = len(first) - 1

    net = _net()
    edges = [net.getEdge(road) for road in roads]
    for edge, following in zip(edges, edges[1:]):
        if not edge.getConnections(following):
            raise PlanningError("Could not join the routes to and from the patient.")

    length, seconds = _length_and_time(edges)
    stretches = signalless_stretches(edges)
    return {
        **to_hospital,
        "roads": roads,
        "geometry": _route_geometry(edges),
        "length_meters": round(length),
        "minutes_without_traffic": round(seconds / 60, 1),
        "signals": _route_signals(edges),
        "streets": _street_names(edges),
        "depart_position": to_patient["depart_position"],
        "arrival_position": to_hospital["arrival_position"],
        "signalless_stretches": stretches,
        "police_along_route": police_cover(edges, stretches),
        "base": {key: base[key] for key in ("name", "latitude", "longitude")},
        "pickup": {
            "name": pickup_name,
            "latitude": pickup_latitude,
            "longitude": pickup_longitude,
            "road": first[-1],
            "position": to_patient["arrival_position"],
            "route_index": pickup_index,
            "seconds": ON_SCENE_SECONDS,
        },
        "minutes_to_patient": to_patient["minutes_without_traffic"],
        "minutes_to_hospital": to_hospital["minutes_without_traffic"],
        "shorter_alternative": None,
    }
