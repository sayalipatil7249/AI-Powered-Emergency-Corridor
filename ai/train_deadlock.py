"""
Train the AI deadlock predictor (ai/deadlock.py).

Two models (gradient-boosted trees) on the same route-ahead features:
    classifier  will the ambulance stand still >= STUCK_SECONDS in the
                next HORIZON_SECONDS? (probability)
    regressor   how many seconds will it stand still?

Fair test: whole routes held out. Compared with a simple rule ("jam or
blocked junctions close ahead") on precision (how often an alert is
right) and recall (how many real deadlocks are caught).

Outputs in ai/models/: deadlock.joblib, deadlock_metrics.json

Usage (from the project root, after ai.deadlock_experiments):
    python -m ai.train_deadlock
"""

import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import precision_score, recall_score, roc_auc_score

from ai.deadlock import FEATURE_COLUMNS, MODEL_FILE, STUCK_SECONDS
from ai.deadlock_experiments import SAMPLES_FILE
from corridor.response import ALERT_PROBABILITY

METRICS_FILE = os.path.join(os.path.dirname(MODEL_FILE), "deadlock_metrics.json")
TEST_EVERY = 5


def main():
    data = pd.read_csv(SAMPLES_FILE)
    data["stuck"] = (data["stopped_seconds_ahead"] >= STUCK_SECONDS).astype(int)
    test_mask = data["route_id"] % TEST_EVERY == 0
    train, test = data[~test_mask], data[test_mask]
    print(f"{len(data):,} samples from {data['run_id'].nunique()} trips; "
          f"{data['stuck'].mean():.0%} are 'stuck ahead'.")

    classifier = HistGradientBoostingClassifier(
        learning_rate=0.05, max_iter=400, max_leaf_nodes=31,
        min_samples_leaf=40, l2_regularization=1.0, random_state=0,
    ).fit(train[FEATURE_COLUMNS], train["stuck"])
    regressor = HistGradientBoostingRegressor(
        loss="absolute_error", learning_rate=0.05, max_iter=400,
        max_leaf_nodes=31, min_samples_leaf=40, random_state=0,
    ).fit(train[FEATURE_COLUMNS], train["stopped_seconds_ahead"])

    probability = classifier.predict_proba(test[FEATURE_COLUMNS])[:, 1]
    alert = probability >= ALERT_PROBABILITY
    seconds = regressor.predict(test[FEATURE_COLUMNS])

    # Simple rule for comparison: a jam within 300 m or 2+ blocked boxes.
    rule = ((test["jam_distance"] <= 300) & (test["jam_length"] > 0)) | (
        test["boxes_blocked"] >= 2
    )

    actual = test["stuck"]
    metrics = {
        "samples": {"train": len(train), "test": len(test)},
        "stuck_share": round(float(data["stuck"].mean()), 3),
        "alert_probability": ALERT_PROBABILITY,
        "ai_model": {
            "precision": round(precision_score(actual, alert, zero_division=0), 3),
            "recall": round(recall_score(actual, alert, zero_division=0), 3),
            "auc": round(roc_auc_score(actual, probability), 3),
        },
        "simple_rule": {
            "precision": round(precision_score(actual, rule, zero_division=0), 3),
            "recall": round(recall_score(actual, rule, zero_division=0), 3),
        },
        "stuck_seconds_error": {
            "ai_model": round(float(np.abs(seconds - test["stopped_seconds_ahead"]).mean()), 1),
            "always_average": round(float(np.abs(
                train["stopped_seconds_ahead"].mean() - test["stopped_seconds_ahead"]
            ).mean()), 1),
        },
    }

    joblib.dump(
        {"classifier": classifier, "regressor": regressor, "features": FEATURE_COLUMNS},
        MODEL_FILE,
    )
    with open(METRICS_FILE, "w") as file:
        json.dump(metrics, file, indent=2)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
