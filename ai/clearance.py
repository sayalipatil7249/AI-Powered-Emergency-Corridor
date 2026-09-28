"""
The AI clearance model: how long a junction needs to get ready for the
ambulance.

When a signal switches for the ambulance, cross traffic first sees
yellow and all red, then the ambulance's direction turns green and the
cars queued in front of the ambulance have to drive off. How long that
takes depends on more than the queue length: the number of lanes, whether
the road beyond the junction has room (a jam there blocks the queue), and
whether the light is already green. The model learns it from thousands
of junction switches in the simulation (ai/clearance_experiments.py,
ai/train_clearance.py).

"Clearance time" = seconds from the moment a signal starts switching
until every vehicle that was on the ambulance's approach lanes has left
them. The corridor engine switches a signal when the ambulance's
predicted arrival (ai/eta_model.py) is within this time.

Uses only the connectors in corridor/interfaces.py.
"""

import os
import warnings

import joblib
import numpy as np

# Predicting from a plain array (fast for one row) instead of named
# columns makes scikit-learn warn every call.
warnings.filterwarnings("ignore", message="X does not have valid feature names")

MODEL_FILE = os.path.join(os.path.dirname(__file__), "models", "clearance.joblib")

# Clearance times are measured up to this long (a queue that has not
# cleared by then is recorded as this value).
MAX_CLEARANCE_SECONDS = 90

# Road space per vehicle in a queue (length + gap), metres.
QUEUE_SPACE_PER_VEHICLE = 7.0

FEATURE_COLUMNS = [
    "queued_on_movement",
    "vehicles_on_movement",
    "longest_lane_queue",
    "movement_lanes",
    "approach_lanes",
    "approach_halting",
    "approach_vehicles",
    "approach_mean_speed",
    "approach_length",
    "downstream_halting",
    "downstream_vehicles",
    "downstream_mean_speed",
    "downstream_length",
    "downstream_free_space",
    "green_now",
]


def _road_of(lane_id):
    return lane_id.rsplit("_", 1)[0]


def movement(signals, signal_id, tls_index):
    """
    The movement through a junction that link tls_index belongs to:
    (approach road, downstream road, approach lanes of that movement).
    """

    links = signals.controlled_links(signal_id)
    link = next((item for item in links[tls_index] if item), None)
    if link is None:
        return None

    approach = _road_of(link[0])
    downstream = _road_of(link[1])

    lanes = sorted({
        item[0]
        for group in links
        for item in group
        if item
        and _road_of(item[0]) == approach
        and _road_of(item[1]) == downstream
    })

    return approach, downstream, lanes


def clearance_features(traffic, signals, signal_id, tls_index):
    """
    What the junction looks like right now, for the ambulance's movement
    tls_index: ({feature: value}, approach lanes) or (None, []).
    """

    found = movement(signals, signal_id, tls_index)
    if found is None:
        return None, []

    approach, downstream, lanes = found

    queues = [traffic.lane_halting_count(lane) for lane in lanes]
    vehicles = sum(len(traffic.lane_vehicle_ids(lane)) for lane in lanes)

    downstream_lanes = traffic.road_lane_count(downstream)
    downstream_length = traffic.lane_length(f"{downstream}_0")
    downstream_vehicles = traffic.road_vehicle_count(downstream)

    light_states = signals.light_states(signal_id)

    features = {
        "queued_on_movement": sum(queues),
        "vehicles_on_movement": vehicles,
        "longest_lane_queue": max(queues, default=0),
        "movement_lanes": len(lanes),
        "approach_lanes": traffic.road_lane_count(approach),
        "approach_halting": traffic.road_halting_count(approach),
        "approach_vehicles": traffic.road_vehicle_count(approach),
        "approach_mean_speed": traffic.road_mean_speed(approach),
        "approach_length": traffic.lane_length(f"{approach}_0"),
        "downstream_halting": traffic.road_halting_count(downstream),
        "downstream_vehicles": downstream_vehicles,
        "downstream_mean_speed": traffic.road_mean_speed(downstream),
        "downstream_length": downstream_length,
        "downstream_free_space": (
            downstream_length * downstream_lanes / QUEUE_SPACE_PER_VEHICLE
            - downstream_vehicles
        ),
        "green_now": int(
            tls_index < len(light_states) and light_states[tls_index] in "Gg"
        ),
    }

    return features, lanes


_model = {}


def _load():
    # Cache only a successful load, so a model trained while the backend
    # runs is picked up without a restart.
    if "bundle" not in _model:
        if not os.path.exists(MODEL_FILE):
            return None
        _model["bundle"] = joblib.load(MODEL_FILE)
    return _model["bundle"]


def predict_clearance(features):
    """Predicted clearance time (s), or None if the model is not trained."""

    bundle = _load()
    if bundle is None or features is None:
        return None

    row = np.array([[features[column] for column in bundle["features"]]])
    prediction = float(bundle["model"].predict(row)[0])
    return min(MAX_CLEARANCE_SECONDS, max(0.0, prediction))


def clearance_predictor(traffic, signals):
    """
    A function (signal_id, tls_index) -> predicted clearance seconds or
    None, for CorridorEngine(clearance_predictor=...).
    """

    def predict(signal_id, tls_index):
        features, _ = clearance_features(traffic, signals, signal_id, tls_index)
        return predict_clearance(features)

    return predict
