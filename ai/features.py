"""
What the ambulance "sees" each second, as numbers for the ETA model.

The same code runs while collecting training data (ai/run_experiments.py)
and on the live dashboard (backend simulation service), so the model
always gets identical inputs.

Everything here is something a real deployment could also measure:
ambulance GPS/speed, road speeds and queues (Google traffic, cameras,
loop detectors) and signal states (city signal control centre).
It reads the world only through the corridor connectors
(corridor/interfaces.py): an AmbulanceTracker and a TrafficSource.
"""

AMBULANCE_ID = "ambulance_01"

# How far ahead "nearby" traffic is measured, in metres.
NEAR_AHEAD_METERS = 500

# Slowest speed assumed for a road in the physics estimate (m/s).
# Cars stopped at a red light make SUMO report ~0 m/s for the road,
# which would otherwise give travel times of hours.
CRAWL_SPEED = 1.5

# Feature columns, in the order the model expects them.
FEATURE_COLUMNS = [
    "speed",
    "acceleration",
    "distance_left",
    "route_progress",
    "formula_eta",
    "formula_next_signal_eta",
    "signals_left",
    "next_signal_distance",
    "next_signal_green",
    "next_signal_queue",
    "second_signal_distance",
    "near_halting",
    "near_vehicles",
    "near_speed_ratio",
    "ahead_halting",
    "ahead_vehicles",
    "ahead_speed_ratio",
    "network_vehicles",
]


class RouteCache:
    """Static facts about the ambulance route, read once per trip."""

    def __init__(self, ambulance, traffic):
        self.ambulance = ambulance
        self.traffic = traffic
        self.edges = ambulance.route()
        self.max_speed = ambulance.max_speed()

        self.lengths = []
        self.speed_limits = []

        for edge_id in self.edges:
            lane_id = f"{edge_id}_0"
            self.lengths.append(traffic.lane_length(lane_id))
            self.speed_limits.append(traffic.lane_speed_limit(lane_id))

        self.total_length = self._driving_distance_from_start()

    def _driving_distance_from_start(self):
        distance = self.ambulance.driving_distance_to(
            self.edges[-1],
            self.lengths[-1],
        )
        return distance if distance > 0 else sum(self.lengths)


def route_position(ambulance):
    """(current route index, metres already driven on that road)."""

    route_index = ambulance.route_index()
    on_junction = ambulance.road_id().startswith(":")

    if on_junction:
        # Already left route_edges[route_index]; next road not started.
        return route_index + 1, 0.0

    return route_index, ambulance.lane_position()


def formula_travel_time(cache, from_index, from_position, to_index):
    """
    Physics estimate: sum of live travel times of the roads from
    (from_index, from_position) up to and including road to_index.
    Congested roads count for more because SUMO's edge travel time
    uses the live mean speed on that road.
    """

    total = 0.0

    for index in range(from_index, min(to_index + 1, len(cache.edges))):
        length = cache.lengths[index]
        fastest = length / max(
            0.1, min(cache.max_speed, cache.speed_limits[index])
        )

        travel_time = cache.traffic.road_travel_time(cache.edges[index])

        if travel_time <= 0:
            travel_time = fastest

        slowest = length / CRAWL_SPEED
        edge_time = min(max(travel_time, fastest), max(slowest, fastest))

        if index == from_index:
            edge_time *= max(0.0, 1 - from_position / max(length, 0.1))

        total += edge_time

    return total


def _traffic_on(cache, indices):
    """Halting cars, cars and length-weighted speed ratio on roads."""

    halting = 0
    vehicles = 0
    weighted_ratio = 0.0
    total_length = 0.0

    for index in indices:
        edge_id = cache.edges[index]
        length = cache.lengths[index]

        halting += cache.traffic.road_halting_count(edge_id)
        count = cache.traffic.road_vehicle_count(edge_id)
        vehicles += count

        # Empty road: traffic moves at the limit.
        ratio = 1.0
        if count > 0:
            ratio = min(
                1.0,
                cache.traffic.road_mean_speed(edge_id)
                / max(cache.speed_limits[index], 0.1),
            )

        weighted_ratio += ratio * length
        total_length += length

    speed_ratio = weighted_ratio / total_length if total_length else 1.0

    return halting, vehicles, speed_ratio


def extract_features(cache, route_signals):
    """
    One row of features for the current simulation step.

    route_signals: every signal on the route, in order, each with a
    "route_index" (CorridorEngine.route_signals).
    Returns (features dict, route_index of the next signal or None).
    """

    ambulance = cache.ambulance
    route_index, lane_position = route_position(ambulance)

    distance_left = ambulance.driving_distance_to(
        cache.edges[-1], cache.lengths[-1]
    )
    if distance_left < 0:
        distance_left = 0.0

    # Signals still ahead (same rule as the dashboard).
    signals_ahead = [
        signal
        for signal in route_signals
        if signal["route_index"] >= route_index
    ]
    next_signal = signals_ahead[0] if signals_ahead else None

    # Live signal info for the ambulance's own lane.
    upcoming_tls = ambulance.upcoming_signals()

    next_signal_distance = -1.0
    next_signal_green = 0
    second_signal_distance = -1.0
    next_signal_queue = 0

    if upcoming_tls:
        _, _, distance, state = upcoming_tls[0]
        next_signal_distance = distance
        next_signal_green = 1 if state in "Gg" else 0

        if len(upcoming_tls) > 1:
            second_signal_distance = upcoming_tls[1][2]

    if next_signal is not None:
        next_signal_queue = cache.traffic.road_halting_count(
            cache.edges[next_signal["route_index"]]
        )

    # Roads within NEAR_AHEAD_METERS, and all remaining roads.
    last_index = len(cache.edges) - 1
    ahead_indices = list(range(min(route_index, last_index), last_index + 1))

    near_indices = []
    covered = -lane_position
    for index in ahead_indices:
        near_indices.append(index)
        covered += cache.lengths[index]
        if covered >= NEAR_AHEAD_METERS:
            break

    near_halting, near_vehicles, near_ratio = _traffic_on(cache, near_indices)
    ahead_halting, ahead_vehicles, ahead_ratio = _traffic_on(
        cache, ahead_indices
    )

    formula_eta = formula_travel_time(
        cache, route_index, lane_position, last_index
    )

    formula_next_signal_eta = -1.0
    if next_signal is not None:
        formula_next_signal_eta = formula_travel_time(
            cache, route_index, lane_position, next_signal["route_index"]
        )

    features = {
        "speed": ambulance.speed(),
        "acceleration": ambulance.acceleration(),
        "distance_left": distance_left,
        "route_progress": 1 - distance_left / max(cache.total_length, 1.0),
        "formula_eta": formula_eta,
        "formula_next_signal_eta": formula_next_signal_eta,
        "signals_left": len(signals_ahead),
        "next_signal_distance": next_signal_distance,
        "next_signal_green": next_signal_green,
        "next_signal_queue": next_signal_queue,
        "second_signal_distance": second_signal_distance,
        "near_halting": near_halting,
        "near_vehicles": near_vehicles,
        "near_speed_ratio": near_ratio,
        "ahead_halting": ahead_halting,
        "ahead_vehicles": ahead_vehicles,
        "ahead_speed_ratio": ahead_ratio,
        "network_vehicles": cache.traffic.vehicle_count(),
    }

    next_route_index = next_signal["route_index"] if next_signal else None

    return features, next_route_index
