"""
Train the AI pre-trip estimate (ai/pretrip.py) and compare it with the
planner's old "without traffic" formula.

Fair test: whole routes are held out (the model never saw them).
Baselines:
    empty-road formula       distance at top speed (the old planner line)
    formula x average factor the formula scaled by the average slow-down
                             of each traffic level

Outputs in ai/models/: pretrip.joblib, pretrip_metrics.json

Usage (from the project root, after ai.pretrip_experiments):
    python -m ai.train_pretrip
"""

import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.model_selection import GroupKFold, cross_val_predict

from ai.pretrip import FEATURE_COLUMNS, MODEL_FILE, TRAFFIC_LEVELS
from ai.pretrip_experiments import TRIPS_FILE

METRICS_FILE = os.path.join(os.path.dirname(MODEL_FILE), "pretrip_metrics.json")

# Every fifth route is kept aside for testing.
TEST_EVERY = 5


def main():
    trips = pd.read_csv(TRIPS_FILE)
    trips = trips[trips["status"] == "arrived"].copy()
    trips["traffic_level"] = trips["level"].map(TRAFFIC_LEVELS.index)
    print(f"Loaded {len(trips)} finished trips on {trips['route_id'].nunique()} routes.")

    test_mask = trips["route_id"] % TEST_EVERY == 0
    train, test = trips[~test_mask], trips[test_mask]
    target = "trip_seconds"

    candidates = {
        "gradient boosting": HistGradientBoostingRegressor(
            loss="absolute_error", learning_rate=0.05, max_iter=300,
            max_leaf_nodes=15, min_samples_leaf=10, random_state=0,
        ),
        "linear": LinearRegression(),
    }

    # Pick the model type by cross-validation on the training routes only.
    folds = GroupKFold(n_splits=5)
    scores = {}
    for name, model in candidates.items():
        predicted = cross_val_predict(
            model, train[FEATURE_COLUMNS], train[target],
            groups=train["route_id"], cv=folds,
        )
        scores[name] = float(np.abs(predicted - train[target]).mean())
    best = min(scores, key=scores.get)
    model = candidates[best].fit(train[FEATURE_COLUMNS], train[target])

    actual = test[target].to_numpy()
    predicted = np.maximum(model.predict(test[FEATURE_COLUMNS]), test["free_flow_seconds"])
    formula = test["free_flow_seconds"].to_numpy()
    factors = (train[target] / train["free_flow_seconds"]).groupby(train["level"]).mean()
    scaled = (test["free_flow_seconds"] * test["level"].map(factors)).to_numpy()

    def minutes_error(values):
        return round(float(np.abs(values - actual).mean()) / 60, 2)

    metrics = {
        "trips": {"train": len(train), "test": len(test)},
        "model": best,
        "cross_validation_error_seconds": {k: round(v, 1) for k, v in scores.items()},
        "average_error_minutes": {
            "ai_model": minutes_error(predicted),
            "formula_x_average_factor": minutes_error(scaled),
            "empty_road_formula": minutes_error(formula),
        },
        "average_trip_minutes_by_level": {
            level: round(float(group[target].mean()) / 60, 1)
            for level, group in trips.groupby("level")
        },
        "slow_down_vs_empty_road": {k: round(float(v), 2) for k, v in factors.items()},
    }

    joblib.dump({"model": model, "features": FEATURE_COLUMNS}, MODEL_FILE)
    with open(METRICS_FILE, "w") as file:
        json.dump(metrics, file, indent=2)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
