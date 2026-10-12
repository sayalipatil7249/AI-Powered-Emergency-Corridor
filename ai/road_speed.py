"""
AI traffic forecast for route planning: how fast each road will be in a
few minutes, like a navigation app predicting traffic at the moment you
reach a road (not just how it is now).

For every road it predicts the speed share (average speed of the cars on
it / its speed limit; an empty road = 1.0) HORIZONS seconds ahead, from:

    the road now        cars, stopped cars, their speed, how full it is
    the roads into it   cars coming, queues building up behind
    the roads out of it a queue spilling back into this road
    the road itself     length, lanes, speed limit, main road or not,
                        a signal at its end
    history             how fast this road usually is at this traffic
                        level (learned from the training runs)
    traffic level       light / normal / heavy (live TomTom)

The same code builds the inputs while collecting training data
(ai/collect_road_speeds.py) and live in the app (road_conditions), so
the model (ai/train_road_speed.py) always gets identical inputs.
Everything here could be measured on real roads too (GPS speeds,
cameras, the signal control centre).
"""

import os
from functools import lru_cache

import numpy as np

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_FILE = os.path.join(MODELS_DIR, "road_speed.joblib")

# Seconds ahead the model forecasts (the planner interpolates between).
HORIZONS = (60, 120, 240, 480)

# Traffic levels, as ai.pretrip.TRAFFIC_LEVELS.
LEVEL_INDEX = {"light": 0, "normal": 1, "heavy": 2}

# A car slower than this (m/s) is standing still; one stopped car takes
# this much road (m).
STOPPED_SPEED = 0.5
VEHICLE_SPACE_METERS = 7.5
MAIN_ROAD_TYPES = ("trunk", "primary", "secondary")

FEATURE_COLUMNS = [
    "length", "lanes", "limit", "main_road", "signal_end",
    "vehicles", "stopped", "speed_share", "density", "stopped_share",
    "in_vehicles", "in_stopped_share", "in_density",
    "out_stopped_share", "out_speed_share",
    "history_share", "level", "horizon",
]


@lru_cache(maxsize=1)
def road_table(net_file=None):
    """The roads the forecast covers, and what never changes about them:
    {"ids", "index", "static" (array), "incoming", "outgoing" (lists of
    road indices)}."""
    from simulation.sumo.route_planner import VEHICLE_CLASS, _net

    net = _net()
    edges = [
        edge for edge in net.getEdges()
        if edge.getFunction() != "internal" and edge.allows(VEHICLE_CLASS)
    ]
    ids = [edge.getID() for edge in edges]
    index = {road: number for number, road in enumerate(ids)}
    static = np.array([
        [
            edge.getLength(),
            edge.getLaneNumber(),
            edge.getSpeed(),
            float(any(kind in (edge.getType() or "") for kind in MAIN_ROAD_TYPES)),
            float(edge.getToNode().getType() == "traffic_light"),
        ]
        for edge in edges
    ])
    incoming = [
        [index[other.getID()] for other in edge.getIncoming() if other.getID() in index]
        for edge in edges
    ]
    outgoing = [
        [index[other.getID()] for other in edge.getOutgoing() if other.getID() in index]
        for edge in edges
    ]
    return {"ids": ids, "index": index, "static": static,
            "incoming": incoming, "outgoing": outgoing}


def road_state(vehicles, table):
    """The roads now, from [{"road_id", "speed"}] (ambulances excluded):
    array per road [vehicles, stopped, speed_share, density,
    stopped_share]."""
    count = len(table["ids"])
    cars = np.zeros(count)
    stopped = np.zeros(count)
    speed_sum = np.zeros(count)
    index = table["index"]
    for vehicle in vehicles:
        number = index.get(vehicle.get("road_id"))
        if number is None:
            continue
        cars[number] += 1
        speed_sum[number] += vehicle["speed"]
        if vehicle["speed"] < STOPPED_SPEED:
            stopped[number] += 1

    static = table["static"]
    length, lanes, limit = static[:, 0], static[:, 1], static[:, 2]
    room = np.maximum(length * lanes, 1.0) / VEHICLE_SPACE_METERS
    speed_share = np.where(
        cars > 0, np.minimum(1.0, speed_sum / np.maximum(cars, 1) / np.maximum(limit, 1.0)), 1.0
    )
    return np.column_stack([
        cars, stopped, speed_share,
        np.minimum(3.0, cars / room), np.minimum(3.0, stopped / room),
    ])


def features(state, table, level, history, horizons=HORIZONS, roads=None):
    """Model inputs: one row per road (all, or the indices in roads) and
    horizon, in FEATURE_COLUMNS order. history: array of usual speed
    shares per road (history_shares)."""
    if roads is None:
        roads = np.arange(len(table["ids"]))
    rows = []
    for number in roads:
        incoming, outgoing = table["incoming"][number], table["outgoing"][number]
        if incoming:
            upstream = state[incoming]
            in_cars = upstream[:, 0].sum()
            in_stopped = upstream[:, 4].max()
            in_density = upstream[:, 3].mean()
        else:
            in_cars = in_stopped = in_density = 0.0
        if outgoing:
            downstream = state[outgoing]
            out_stopped = downstream[:, 4].max()
            out_speed = downstream[:, 2].mean()
        else:
            out_stopped, out_speed = 0.0, 1.0
        base = [
            *table["static"][number], *state[number],
            in_cars, in_stopped, in_density, out_stopped, out_speed,
            history[number], LEVEL_INDEX.get(level, 1),
        ]
        for horizon in horizons:
            rows.append(base + [horizon])
    return np.array(rows)


# -------------------------------------------------------------
# Live use
# -------------------------------------------------------------

_model = None


def load():
    """{"model", "history": {level: array}} or None when not trained."""
    global _model
    if _model is None and os.path.exists(MODEL_FILE):
        import joblib
        _model = joblib.load(MODEL_FILE)
    return _model


def forecast(vehicles, level):
    """Predicted speed share per road id at each horizon:
    {road id: [share at HORIZONS[0], ...]}, or None without a model."""
    model = load()
    if model is None:
        return None
    table = road_table()
    history = model["history"].get(level, model["history"]["normal"])
    state = road_state(vehicles, table)
    rows = features(state, table, level, history)
    shares = np.clip(model["model"].predict(rows), 0.02, 1.0).reshape(-1, len(HORIZONS))
    return {road: list(shares[number]) for number, road in enumerate(table["ids"])}
