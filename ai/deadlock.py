"""
The AI deadlock predictor: will the ambulance get stuck on the route
ahead, and where?

Looks at the route up to LOOKAHEAD_METERS ahead of the ambulance: how
full of stopped cars each stretch is, how slowly traffic moves, and
whether cars are stuck inside junctions (the sign of a deadlock: they
cannot drive off because the road beyond is full). A model trained on
simulated trips (ai/deadlock_experiments.py, ai/train_deadlock.py)
predicts how many seconds the ambulance will stand still in the next
few minutes. corridor/response.py uses it to send police or re-route
the ambulance before it reaches the jam.

Uses only the connectors in corridor/interfaces.py.
"""

import os
import warnings

import joblib
import numpy as np

MODEL_FILE = os.path.join(os.path.dirname(__file__), "models", "deadlock.joblib")

LOOKAHEAD_METERS = 1500

# Road space per stopped vehicle (length + gap), metres.
VEHICLE_SPACE_METERS = 7.5

# A road counts as jammed when stopped cars fill this share of it.
JAMMED_SHARE = 0.5

# What the model predicts: seconds the ambulance will be (nearly) stopped
# in the next HORIZON_SECONDS; "stuck" means at least STUCK_SECONDS.
HORIZON_SECONDS = 180
STUCK_SPEED = 1.5  # m/s
STUCK_SECONDS = 45

# Distance bands ahead of the ambulance (metres).
BANDS = [(0, 300), (300, 800), (800, LOOKAHEAD_METERS)]

FEATURE_COLUMNS = [
    "ambulance_speed",
    *[f"{name}_{start}_{end}" for start, end in BANDS
      for name in ("stopped_share", "speed_ratio")],
    "boxes_blocked",
    "stopped_in_boxes",
    "jam_distance",
    "jam_length",
    "traffic_level",
]

warnings.filterwarnings("ignore", message="X does not have valid feature names")


def route_ahead(route_info, traffic, ambulance, traffic_level):
    """
    What the route ahead looks like now.

    route_info: {"roads", "lengths", "lanes", "limits", "boxes"} for the
    ambulance's route (boxes[i] = the junction road between road i and
    i+1, or None). Returns (features, jam) where jam = {"start_index",
    "end_index", "distance", "length"} of the first jammed stretch
    ahead, or None.
    """

    index = ambulance.route_index()
    road_id = ambulance.road_id()
    position = ambulance.lane_position() if not road_id.startswith(":") else 0.0

    bands = [{"stopped": 0.0, "length": 0.0, "weighted": 0.0} for _ in BANDS]
    boxes_blocked = stopped_in_boxes = 0
    jam = None
    distance = -position

    for i in range(index, len(route_info["roads"])):
        if distance > LOOKAHEAD_METERS:
            break

        road = route_info["roads"][i]
        length = route_info["lengths"][i]
        lanes = max(1, route_info["lanes"][i])
        halting = traffic.road_halting_count(road)
        stopped_share = min(1.0, halting * VEHICLE_SPACE_METERS / lanes / max(length, 1))

        ratio = 1.0
        if traffic.road_vehicle_count(road) > 0:
            ratio = min(1.0, traffic.road_mean_speed(road) / max(route_info["limits"][i], 0.1))

        middle = distance + length / 2
        for band, (start, end) in zip(bands, BANDS):
            if start <= middle < end:
                band["stopped"] += stopped_share * length
                band["weighted"] += ratio * length
                band["length"] += length

        box = route_info["boxes"][i]
        if box and distance + length >= 0:
            in_box = traffic.road_halting_count(box)
            stopped_in_boxes += in_box
            boxes_blocked += int(in_box > 0)

        if stopped_share >= JAMMED_SHARE and distance + length > 0:
            if jam is None:
                jam = {"start_index": i, "end_index": i,
                       "distance": max(0.0, distance), "length": 0.0}
            if jam["end_index"] >= i - 1:
                jam["end_index"] = i
                jam["length"] += length

        distance += length

    features = {
        "ambulance_speed": ambulance.speed(),
        "boxes_blocked": boxes_blocked,
        "stopped_in_boxes": stopped_in_boxes,
        "jam_distance": jam["distance"] if jam else LOOKAHEAD_METERS,
        "jam_length": jam["length"] if jam else 0.0,
        "traffic_level": traffic_level,
    }
    for band, (start, end) in zip(bands, BANDS):
        features[f"stopped_share_{start}_{end}"] = (
            band["stopped"] / band["length"] if band["length"] else 0.0
        )
        features[f"speed_ratio_{start}_{end}"] = (
            band["weighted"] / band["length"] if band["length"] else 1.0
        )

    return features, jam


def route_info(roads, traffic):
    """Static facts about a route that route_ahead() needs."""

    from simulation.sumo.route_planner import junction_roads

    return {
        "roads": roads,
        "lengths": [traffic.lane_length(f"{road}_0") for road in roads],
        "lanes": [traffic.road_lane_count(road) for road in roads],
        "limits": [traffic.lane_speed_limit(f"{road}_0") for road in roads],
        "boxes": junction_roads(roads),
    }


_model = {}


def _load():
    if "bundle" not in _model:
        if not os.path.exists(MODEL_FILE):
            return None
        _model["bundle"] = joblib.load(MODEL_FILE)
    return _model["bundle"]


def predict(features):
    """(probability of getting stuck, expected seconds stopped) in the
    next HORIZON_SECONDS, or None if the model is not trained yet."""

    bundle = _load()
    if bundle is None:
        return None
    row = np.array([[features[column] for column in bundle["features"]]])
    probability = float(bundle["classifier"].predict_proba(row)[0][1])
    seconds = float(max(0.0, bundle["regressor"].predict(row)[0]))
    return probability, seconds
