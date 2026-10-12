"""
Train the AI traffic forecast (ai/road_speed.py) on the data from
ai/collect_road_speeds.py, and check it on traffic it has not seen.

The model (gradient boosting, like the other models here) predicts a
road's speed share HORIZONS seconds ahead. It is compared on the
held-out seeds with two simple guesses:

    now         the road stays as it is now (what live-speed routing
                assumes)
    usual       the road is as fast as it usually is at this level

Errors are in seconds of travel time on each road (what the route
planner adds up), on roads with traffic. The "history" input is the
road's average speed share in the training runs, per traffic level.

Output: ai/models/road_speed.joblib, ai/models/road_speed_metrics.json

Usage (from the project root):
    python -m ai.train_road_speed
"""

import glob
import json
import os

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor

from ai import road_speed
from ai.collect_road_speeds import OUT_DIR

TEST_SEEDS = {7, 8}
MAX_TRAIN_ROWS = 1_500_000
METRICS_FILE = os.path.join(road_speed.MODELS_DIR, "road_speed_metrics.json")
LEVELS = {index: level for level, index in road_speed.LEVEL_INDEX.items()}
SLOWEST_SPEED = 1.0  # m/s, as simulation/sumo/road_conditions.py

COLUMN = {name: number for number, name in enumerate(road_speed.FEATURE_COLUMNS)}


def load():
    parts = {"X": [], "y": [], "road": [], "seed": []}
    for path in sorted(glob.glob(os.path.join(OUT_DIR, "*.npz"))):
        data = np.load(path)
        parts["X"].append(data["X"].astype(np.float32))
        parts["y"].append(data["y"].astype(np.float32))
        parts["road"].append(data["road"])
        parts["seed"].append(np.full(len(data["y"]), int(data["seed"])))
    return {key: np.concatenate(value) for key, value in parts.items()}


def seconds(X, share):
    """Travel time of each row's road at a speed share (s)."""
    limit = X[:, COLUMN["limit"]]
    return X[:, COLUMN["length"]] / np.clip(share * limit, SLOWEST_SPEED, limit)


def main():
    data = load()
    X, y, road, seed = data["X"], data["y"], data["road"], data["seed"]
    test = np.isin(seed, list(TEST_SEEDS))
    level = X[:, COLUMN["level"]].astype(int)
    roads = len(road_speed.road_table()["ids"])

    # History: each road's average speed share per level, training runs only.
    history = {}
    for number, name in LEVELS.items():
        rows = (~test) & (level == number)
        total = np.bincount(road[rows], weights=y[rows], minlength=roads)
        count = np.bincount(road[rows], minlength=roads)
        usual = float(y[rows].mean()) if rows.any() else 1.0
        history[name] = np.where(count >= 5, total / np.maximum(count, 1), np.maximum(usual, 0.9))
    X[:, COLUMN["history_share"]] = [history[LEVELS[l]][r] for l, r in zip(level, road)]

    train = np.flatnonzero(~test)
    if len(train) > MAX_TRAIN_ROWS:
        train = np.random.default_rng(0).choice(train, MAX_TRAIN_ROWS, replace=False)
    model = HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.1, max_leaf_nodes=63, random_state=0,
    )
    model.fit(X[train], y[train])

    # Check on the held-out seeds, roads with cars now or later.
    rows = np.flatnonzero(test & ((X[:, COLUMN["vehicles"]] > 0) | (y < 1.0)))
    Xt, yt = X[rows], y[rows]
    actual = seconds(Xt, yt)
    guesses = {
        "ai_model": np.clip(model.predict(Xt), 0.02, 1.0),
        "now": Xt[:, COLUMN["speed_share"]],
        "usual": Xt[:, COLUMN["history_share"]],
    }
    metrics = {"rows": {"train": int(len(train)), "test": int(len(rows))},
               "test_seeds": sorted(TEST_SEEDS), "by_horizon": {}}
    for horizon in road_speed.HORIZONS:
        mask = Xt[:, COLUMN["horizon"]] == horizon
        metrics["by_horizon"][str(horizon)] = {
            name: round(float(np.abs(seconds(Xt[mask], guess[mask]) - actual[mask]).mean()), 2)
            for name, guess in guesses.items()
        }
    metrics["all_horizons_seconds_error"] = {
        name: round(float(np.abs(seconds(Xt, guess) - actual).mean()), 2)
        for name, guess in guesses.items()
    }

    os.makedirs(road_speed.MODELS_DIR, exist_ok=True)
    joblib.dump({"model": model, "history": history,
                 "features": road_speed.FEATURE_COLUMNS}, road_speed.MODEL_FILE)
    with open(METRICS_FILE, "w") as file:
        json.dump(metrics, file, indent=2)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
