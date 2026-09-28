"""
The AI pre-trip estimate: how long an ambulance will take for a planned
route, before it sets off.

The planner's "without traffic" time assumes empty roads at top speed,
which never happens in Pune. This model learned from real simulated trips
on many random routes (ai/pretrip_experiments.py, ai/train_pretrip.py):
the route itself (length, signals, junctions, turns, road sizes) plus
the traffic level decide how long the trip really takes, with the
corridor (AI signal timing) and the rescue lane working.

The dashboard uses the live traffic level from TomTom.
"""

import math
import os
import warnings

import joblib
import numpy as np

MODEL_FILE = os.path.join(os.path.dirname(__file__), "models", "pretrip.joblib")

TRAFFIC_LEVELS = ["light", "normal", "heavy"]

FEATURE_COLUMNS = [
    "length_meters",
    "free_flow_seconds",
    "signals",
    "junctions",
    "turns",
    "multi_lane_share",
    "traffic_level",
]

# A change of direction bigger than this at a junction counts as a turn.
TURN_DEGREES = 45

warnings.filterwarnings("ignore", message="X does not have valid feature names")


def _heading(shape):
    (x1, y1), (x2, y2) = shape[-2], shape[-1]
    return math.degrees(math.atan2(x2 - x1, y2 - y1))


def route_features(edges, length_meters, free_flow_seconds, signal_count):
    """What the model knows about a route (sumolib edges, in order)."""

    turns = 0
    for edge, next_edge in zip(edges, edges[1:]):
        if len(edge.getShape()) < 2 or len(next_edge.getShape()) < 2:
            continue
        change = abs(_heading(edge.getShape()) - _heading(next_edge.getShape()[:2]))
        if min(change, 360 - change) > TURN_DEGREES:
            turns += 1

    multi_lane = sum(edge.getLength() for edge in edges if edge.getLaneNumber() >= 2)
    total = sum(edge.getLength() for edge in edges) or 1.0

    return {
        "length_meters": length_meters,
        "free_flow_seconds": free_flow_seconds,
        "signals": signal_count,
        # Every place where the route passes from one road to the next
        # through a real junction (not just a road split in two).
        "junctions": sum(
            1 for edge in edges[:-1] if len(edge.getToNode().getIncoming()) > 1
        ),
        "turns": turns,
        "multi_lane_share": multi_lane / total,
    }


_model = {}


def _load():
    if "bundle" not in _model:
        if not os.path.exists(MODEL_FILE):
            return None
        _model["bundle"] = joblib.load(MODEL_FILE)
    return _model["bundle"]


def estimate_minutes(features):
    """
    {"light": min, "normal": min, "heavy": min} for a route, or None if
    the model is not trained yet.
    """

    bundle = _load()
    if bundle is None:
        return None

    rows = np.array([
        [
            {**features, "traffic_level": level}[column]
            for column in bundle["features"]
        ]
        for level in range(len(TRAFFIC_LEVELS))
    ])
    seconds = bundle["model"].predict(rows)
    return {
        # Plain floats: numpy numbers cannot be sent as JSON.
        name: round(float(max(value, features["free_flow_seconds"])) / 60, 1)
        for name, value in zip(TRAFFIC_LEVELS, seconds)
    }
