"""
Builds the pieces of the live state sent to the dashboard.
Uses only the connectors in corridor/interfaces.py.
"""

from ai.features import formula_travel_time, route_position
from corridor.engine import CorridorEngine

_LIGHT_NAMES = (("Gg", "GREEN"), ("Yy", "YELLOW"), ("Rr", "RED"))


def ambulance_snapshot(ambulance):
    """Position, speed and route position of the ambulance."""

    return {
        "vehicle_id": ambulance.vehicle_id,
        **ambulance.position(),
        "speed": ambulance.speed(),
        "heading": ambulance.heading(),
        "road_id": ambulance.road_id(),
        "route_index": ambulance.route_index(),
    }


def route_geometry(ambulance, traffic):
    """The ambulance route as [[latitude, longitude], ...]."""

    points = []

    for road_id in ambulance.route():
        for x, y in traffic.lane_shape(f"{road_id}_0"):
            position = traffic.to_latlon(x, y)
            points.append([position["latitude"], position["longitude"]])

    return points


def _colour(light_state):
    if not light_state:
        return "UNKNOWN"
    for letters, name in _LIGHT_NAMES:
        if light_state in letters:
            return name
    return "UNKNOWN"


def signal_states(signals, upcoming):
    """
    Every signal with its colour. For signals in the corridor, the
    colour of the ambulance's own movement; for the others, a
    junction-level summary (green if any movement is green).
    """

    corridor_links = {}
    for junction in upcoming:
        corridor_links.setdefault(junction["signal_id"], junction["tls_index"])

    result = []

    for signal_id in signals.signal_ids():
        light_states = signals.light_states(signal_id)

        if signal_id in corridor_links:
            link_index = corridor_links[signal_id]
            state = (
                _colour(light_states[link_index])
                if 0 <= link_index < len(light_states)
                else "UNKNOWN"
            )
        else:
            state = next(
                (
                    name
                    for letters, name in _LIGHT_NAMES
                    if any(light in light_states for light in letters)
                ),
                "UNKNOWN",
            )

        position = signals.position(signal_id) or {}

        result.append({
            "signal_id": signal_id,
            "phase": signals.phase(signal_id),
            "program": signals.program(signal_id),
            "raw_state": light_states,
            "state": state,
            "latitude": position.get("latitude"),
            "longitude": position.get("longitude"),
        })

    return result


# A road is "jammed" / "slow" if traffic moves below this share of the
# speed limit, or this many cars are stopped on it.
JAMMED_SPEED_SHARE = 0.3
JAMMED_STOPPED_CARS = 5
SLOW_SPEED_SHARE = 0.6
SLOW_STOPPED_CARS = 2


def route_traffic(route_cache, traffic, max_roads=None):
    """
    Traffic on every road still ahead of the ambulance, like the
    green / yellow / red colours on a navigation map.

    route_cache: ai.features.RouteCache for this trip.
    """

    route_index, lane_position = route_position(route_cache.ambulance)
    last_index = len(route_cache.edges) - 1

    roads = []
    starts_in = -lane_position

    for index in range(min(route_index, last_index), last_index + 1):
        road_id = route_cache.edges[index]
        length = route_cache.lengths[index]
        vehicles = traffic.road_vehicle_count(road_id)
        stopped = traffic.road_halting_count(road_id)

        speed_share = 1.0
        if vehicles > 0:
            speed_share = min(
                1.0,
                traffic.road_mean_speed(road_id)
                / max(route_cache.speed_limits[index], 0.1),
            )

        if speed_share < JAMMED_SPEED_SHARE or stopped >= JAMMED_STOPPED_CARS:
            level = "jammed"
        elif speed_share < SLOW_SPEED_SHARE or stopped >= SLOW_STOPPED_CARS:
            level = "slow"
        else:
            level = "free"

        roads.append({
            "route_index": index,
            "road_id": road_id,
            "starts_in_meters": round(max(starts_in, 0.0)),
            "length_meters": round(length),
            "vehicles": vehicles,
            "stopped_vehicles": stopped,
            "speed_percent_of_limit": round(speed_share * 100),
            "level": level,
        })

        starts_in += length

        if max_roads and len(roads) >= max_roads:
            break

    return roads


def route_traffic_segments(route_cache, traffic, shapes):
    """
    The route ahead coloured by traffic, for the map: [{"level",
    "coordinates": [[latitude, longitude], ...]}], neighbouring roads
    with the same level joined into one segment.

    shapes: dict reused between calls (road id -> coordinates).
    """

    segments = []

    for road in route_traffic(route_cache, traffic):
        road_id = road["road_id"]

        if road_id not in shapes:
            shapes[road_id] = [
                [point["latitude"], point["longitude"]]
                for point in (
                    traffic.to_latlon(x, y)
                    for x, y in traffic.lane_shape(f"{road_id}_0")
                )
            ]

        if segments and segments[-1]["level"] == road["level"]:
            segments[-1]["coordinates"] += shapes[road_id]
        else:
            segments.append({
                "level": road["level"],
                "coordinates": list(shapes[road_id]),
            })

    return segments


def upcoming_signal_details(engine, route_cache):
    """
    The junctions ahead with what an operator or agent needs:
    route signal number, distance, travel-time estimate, the light for
    the ambulance's lane, the queue waiting there and its priority.
    """

    numbers = {
        (signal["signal_id"], signal["route_index"]): signal["number"]
        for signal in engine.route_signals
    }
    names = {
        (signal["signal_id"], signal["route_index"]): signal.get("name")
        for signal in engine.route_signals
    }

    route_index, lane_position = route_position(route_cache.ambulance)
    upcoming = engine.upcoming_signals()

    details = []

    for index, junction in enumerate(upcoming):
        key = (junction["signal_id"], junction["route_index"])

        if index == 0:
            timing = engine.next_timing or {}
            stage = engine.stage(key)
            priority = (
                "automatic: switches green in about "
                f"{timing.get('switch_in_seconds', 0):.0f} s"
                if stage == "normal"
                else f"automatic: {stage.replace('_', ' ')} for the ambulance"
            )
        elif key in engine.priority_requests:
            priority = "requested early green"
        else:
            priority = None

        details.append({
            "signal_number": numbers.get(key),
            "signal_name": names.get(key),
            "signal_id": junction["signal_id"],
            "distance_meters": round(junction["distance"]),
            "estimated_seconds_to_reach": round(
                formula_travel_time(
                    route_cache, route_index, lane_position,
                    junction["route_index"],
                ),
                1,
            ),
            "light_for_ambulance": _colour(junction["sumo_state"] or ""),
            "cars_queued": engine.traffic.road_halting_count(
                route_cache.edges[junction["route_index"]]
            ),
            "corridor_role": CorridorEngine.role(index),
            "priority": priority,
        })

    return details


def corridor_entries(upcoming, signals, ambulance_speed, engine=None):
    """
    The upcoming junctions with their corridor role (J1, J2, ...) and,
    with the engine, where each is in switching for the ambulance:
    "stage" normal / yellow / all_red / green, and for the junction ahead
    "switch_in_seconds" until it starts switching.
    """

    entries = []
    timing = engine.next_timing if engine else None

    for index, junction in enumerate(upcoming):
        entry = {
            "junction": f"J{index + 1}",
            "signal_id": junction["signal_id"],
            "route_index": junction["route_index"],
            "tls_index": junction["tls_index"],
            "state": CorridorEngine.role(index),
            "eta_seconds": (
                round(junction["distance"] / ambulance_speed, 1)
                if ambulance_speed > 0
                else None
            ),
            "distance_meters": junction["distance"],
        }

        if engine is not None:
            key = (junction["signal_id"], junction["route_index"])
            entry["stage"] = engine.stage(key)
            if index == 0 and timing:
                entry["switch_in_seconds"] = timing["switch_in_seconds"]
                entry["predicted_arrival_seconds"] = timing["seconds_to_arrival"]
                entry["queued_cars"] = timing["queued_cars"]

        position = signals.position(junction["signal_id"])
        if position:
            entry.update(position)

        entries.append(entry)

    return entries
