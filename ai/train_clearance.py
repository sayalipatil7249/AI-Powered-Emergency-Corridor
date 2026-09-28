"""
Train the AI clearance model (ai/clearance.py) and compare it with the
fixed rule it replaces.

The corridor switches a signal when the ambulance is predicted to arrive
within the junction's "lead time". Too short: the ambulance runs into
cars that have not driven off yet. Too long: cross traffic sits at red
for nothing. The rule (yellow + all red + 2 s + 2 s per queued car +
5 s margin) only looks at the queue; the model also sees lanes, the road
beyond the junction and the current light.

The model predicts a high percentile (QUANTILE) of the clearance time
rather than the average, because arriving too early costs a few seconds
of cross-traffic red, while arriving too late stops the ambulance.

Fair test: whole traffic patterns (seeds) are held out.

Outputs in ai/models/:
    clearance.joblib          the trained model
    clearance_metrics.json    scores: model vs rule

Usage (from the project root, after ai.clearance_experiments):
    python -m ai.train_clearance
"""

import glob
import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from ai.clearance import FEATURE_COLUMNS, MODEL_FILE
from ai.scenarios import PROJECT_ROOT
from corridor.engine import (
    AI_SAFETY_MARGIN_SECONDS,
    ALL_RED_SECONDS,
    QUEUE_HEADWAY_SECONDS,
    QUEUE_START_SECONDS,
    SAFETY_MARGIN_SECONDS,
    YELLOW_SECONDS,
)

DATA_DIR = os.path.join(PROJECT_ROOT, "data", "clearance")
METRICS_FILE = os.path.join(os.path.dirname(MODEL_FILE), "clearance_metrics.json")

TEST_SEEDS = {12, 13, 14, 15}
QUANTILE = 0.85


def rule_lead(data):
    """Lead time of the fixed rule (corridor/engine.green_lead_seconds,
    without its 45 s cap)."""
    return (
        YELLOW_SECONDS + ALL_RED_SECONDS + SAFETY_MARGIN_SECONDS
        + QUEUE_START_SECONDS
        + QUEUE_HEADWAY_SECONDS * data["queued_on_movement"]
    )


def score(lead, actual):
    """How well a lead time fits the real clearance times."""

    on_time = lead >= actual
    return {
        "ready_in_time_percent": round(100 * on_time.mean(), 1),
        # When ready too late: seconds the ambulance meets the queue.
        "average_seconds_late_when_late": round(
            float((actual - lead)[~on_time].mean()) if (~on_time).any() else 0.0, 1
        ),
        # When ready early: extra seconds of red for cross traffic.
        "average_extra_red_seconds": round(
            float((lead - actual)[on_time].mean()) if on_time.any() else 0.0, 1
        ),
        "average_lead_seconds": round(float(lead.mean()), 1),
    }


def main():
    files = glob.glob(os.path.join(DATA_DIR, "*.csv"))
    if not files:
        raise SystemExit("No data. Run: python -m ai.clearance_experiments")

    data = pd.concat((pd.read_csv(path) for path in files), ignore_index=True)
    print(f"Loaded {len(data):,} junction switches from {len(files)} simulations.")

    train = data[~data["traffic_seed"].isin(TEST_SEEDS)]
    test = data[data["traffic_seed"].isin(TEST_SEEDS)]

    def fit(loss, **extra):
        model = HistGradientBoostingRegressor(
            loss=loss,
            learning_rate=0.05,
            max_iter=500,
            max_leaf_nodes=31,
            min_samples_leaf=40,
            l2_regularization=1.0,
            random_state=0,
            **extra,
        )
        model.fit(train[FEATURE_COLUMNS], train["clearance_seconds"])
        return model

    typical = fit("absolute_error")
    cautious = fit("quantile", quantile=QUANTILE)

    actual = test["clearance_seconds"].to_numpy()
    typical_prediction = typical.predict(test[FEATURE_COLUMNS])
    cautious_prediction = np.clip(cautious.predict(test[FEATURE_COLUMNS]), 0, None)

    # The rule's own clearance estimate (its lead without the margin).
    rule_estimate = (rule_lead(test) - SAFETY_MARGIN_SECONDS).to_numpy()

    ai_lead = cautious_prediction + AI_SAFETY_MARGIN_SECONDS
    rules = rule_lead(test).to_numpy()

    busy = test["queued_on_movement"].to_numpy() > 0

    metrics = {
        "junction_switches": {"train": len(train), "test": len(test)},
        "test_seeds": sorted(TEST_SEEDS),
        "quantile": QUANTILE,
        "clearance_error_seconds": {
            "ai_model": round(float(np.abs(typical_prediction - actual).mean()), 1),
            "rule": round(float(np.abs(rule_estimate - actual).mean()), 1),
        },
        "all_junctions": {
            "ai_model": score(ai_lead, actual),
            "rule": score(rules, actual),
        },
        "junctions_with_a_queue": {
            "count": int(busy.sum()),
            "ai_model": score(ai_lead[busy], actual[busy]),
            "rule": score(rules[busy], actual[busy]),
        },
    }

    joblib.dump(
        {"model": cautious, "features": FEATURE_COLUMNS, "quantile": QUANTILE},
        MODEL_FILE,
    )
    with open(METRICS_FILE, "w") as file:
        json.dump(metrics, file, indent=2)

    print(json.dumps(metrics, indent=2))
    print(f"\nSaved {MODEL_FILE} and {METRICS_FILE}")


if __name__ == "__main__":
    main()
