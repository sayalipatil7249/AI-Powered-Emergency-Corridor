"""
Load the trained ETA models (ai/train_eta.py) and predict live.

If a model file is missing (not trained yet) the predict functions
return None and the caller falls back to the physics formula.
"""

import os

import warnings

import joblib
import numpy as np

from ai.features import extract_features

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")

_models = {}

# The models were trained on named columns; predicting from a plain
# array (much faster for one row) makes scikit-learn warn every call.
warnings.filterwarnings("ignore", message="X does not have valid feature names")


def _load(name):
    # Only successful loads are cached, so a model trained while the
    # backend is running is picked up without a restart.
    if name not in _models:
        path = os.path.join(MODELS_DIR, f"eta_{name}.joblib")
        if not os.path.exists(path):
            return None
        _models[name] = joblib.load(path)
    return _models[name]


def _predict(name, features):
    bundle = _load(name)
    if bundle is None:
        return None

    row = np.array([[features[column] for column in bundle["features"]]])
    return max(0.0, float(bundle["model"].predict(row)[0]))


def predict_hospital_eta(features):
    """Seconds until the ambulance reaches the hospital, or None."""
    return _predict("hospital", features)


def predict_next_signal_eta(features):
    """Seconds until the ambulance passes the next signal, or None."""
    if features.get("formula_next_signal_eta", -1) < 0:
        return None
    return _predict("next_signal", features)


def estimate(route_cache, route_signals):
    """
    Distance left and ETAs for the dashboard: the AI model's when it is
    trained, otherwise the physics formula. Both are returned so the
    dashboard can show them side by side.
    """

    features, next_route_index = extract_features(route_cache, route_signals)

    ai_eta = predict_hospital_eta(features)
    formula_eta = features["formula_eta"]

    next_signal_eta = predict_next_signal_eta(features)
    if next_signal_eta is None and features["formula_next_signal_eta"] >= 0:
        next_signal_eta = features["formula_next_signal_eta"]

    return {
        "distance_left_meters": round(features["distance_left"], 1),
        "formula_eta_seconds": round(formula_eta, 1),
        "eta_seconds": round(
            ai_eta if ai_eta is not None else formula_eta, 1
        ),
        "eta_source": "ai" if ai_eta is not None else "formula",
        "next_signal_eta_seconds": (
            round(next_signal_eta, 1) if next_signal_eta is not None else None
        ),
        # Which junction next_signal_eta_seconds is for.
        "next_signal_route_index": next_route_index,
    }


def next_signal_seconds(features):
    """Seconds until the ambulance passes the next signal: the AI
    model's prediction, else the formula, else None (no signal ahead)."""

    predicted = predict_next_signal_eta(features)
    if predicted is not None:
        return predicted
    formula = features.get("formula_next_signal_eta", -1)
    return formula if formula >= 0 else None
