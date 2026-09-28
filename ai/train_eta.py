"""
Train the AI ETA models and compare them with the old formula.

Two models (gradient-boosted trees, scikit-learn's LightGBM-style
HistGradientBoostingRegressor):
    hospital     seconds until the ambulance reaches the hospital
    next_signal  seconds until it passes the next traffic signal

Fair test: whole traffic patterns (seeds) are held out, so the model is
scored only on traffic it has never seen.

Outputs in ai/models/:
    eta_hospital.joblib, eta_next_signal.joblib   the trained models
    eta_metrics.json                              all scores
    eta_report.png                                charts for the report

Usage (from the project root, after ai.run_experiments):
    python -m ai.train_eta
"""

import glob
import json
import os

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402

from ai.features import FEATURE_COLUMNS  # noqa: E402
from ai.scenarios import PROJECT_ROOT  # noqa: E402

RUNS_DIR = os.path.join(PROJECT_ROOT, "data", "runs")
MODELS_DIR = os.path.join(PROJECT_ROOT, "ai", "models")

# Traffic seeds kept aside for testing (never used in training).
TEST_SEEDS = {12, 13, 14, 15}

TARGETS = {
    "hospital": ("target_hospital_eta", "formula_eta"),
    "next_signal": ("target_next_signal_eta", "formula_next_signal_eta"),
}


def load_rows():
    files = [
        path
        for path in glob.glob(os.path.join(RUNS_DIR, "*_on.csv"))
    ]
    if not files:
        raise SystemExit("No runs found. Run: python -m ai.run_experiments")

    rows = pd.concat((pd.read_csv(path) for path in files), ignore_index=True)
    print(f"Loaded {len(rows):,} seconds of driving from {len(files)} trips.")
    return rows


def scores(actual, predicted):
    error = predicted - actual
    meaningful = actual > 30  # percentage error is unstable near arrival
    return {
        "mae_seconds": float(np.mean(np.abs(error))),
        "rmse_seconds": float(np.sqrt(np.mean(error ** 2))),
        "mape_percent": (
            float(np.mean(np.abs(error[meaningful]) / actual[meaningful]) * 100)
            if meaningful.any()
            else float("nan")
        ),
        "median_abs_seconds": float(np.median(np.abs(error))),
    }


def train_one(rows, name):
    target_column, formula_column = TARGETS[name]

    data = rows.dropna(subset=[target_column]).copy()
    if name == "next_signal":
        data = data[data[formula_column] >= 0]

    train = data[~data["traffic_seed"].isin(TEST_SEEDS)]
    test = data[data["traffic_seed"].isin(TEST_SEEDS)]

    model = HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.05,
        max_iter=600,
        max_leaf_nodes=31,
        min_samples_leaf=40,
        l2_regularization=0.1,
        random_state=0,
    )
    model.fit(train[FEATURE_COLUMNS], train[target_column])

    actual = test[target_column].to_numpy()
    ai_prediction = model.predict(test[FEATURE_COLUMNS])

    # Simple baseline: distance left at the average ambulance speed
    # seen in training trips.
    average_speed = (
        train.groupby("run_id")
        .apply(lambda trip: trip["distance_left"].iloc[0]
               / max(trip[target_column].iloc[0], 1),
               include_groups=False)
        .mean()
    ) if name == "hospital" else None

    results = {
        "train_rows": len(train),
        "test_rows": len(test),
        "test_trips": int(test["run_id"].nunique()),
        "ai_model": scores(actual, ai_prediction),
        "old_formula": scores(actual, test[formula_column].to_numpy()),
    }

    if average_speed:
        results["average_speed_guess"] = scores(
            actual, test["distance_left"].to_numpy() / average_speed
        )

    # Error at the moment the ambulance departs (what a dispatcher sees).
    first_rows = test.sort_values("time").groupby("run_id").head(1)
    results["at_departure"] = {
        "ai_model": scores(
            first_rows[target_column].to_numpy(),
            model.predict(first_rows[FEATURE_COLUMNS]),
        ),
        "old_formula": scores(
            first_rows[target_column].to_numpy(),
            first_rows[formula_column].to_numpy(),
        ),
    }

    # Which inputs matter most.
    sample = test.sample(min(len(test), 4000), random_state=0)
    importance = permutation_importance(
        model,
        sample[FEATURE_COLUMNS],
        sample[target_column],
        n_repeats=3,
        random_state=0,
        scoring="neg_mean_absolute_error",
    )
    results["top_features"] = [
        {"feature": FEATURE_COLUMNS[i], "mae_increase_seconds": float(
            importance.importances_mean[i])}
        for i in np.argsort(importance.importances_mean)[::-1][:8]
    ]

    joblib.dump(
        {"model": model, "features": FEATURE_COLUMNS},
        os.path.join(MODELS_DIR, f"eta_{name}.joblib"),
    )

    return results, test, ai_prediction


def plot_report(test, prediction, path):
    target = test["target_hospital_eta"].to_numpy()
    formula = test["formula_eta"].to_numpy()
    progress = test["route_progress"].to_numpy()

    figure, (left, right) = plt.subplots(1, 2, figsize=(13, 5))

    limit = max(target.max(), 60)
    left.scatter(target, formula, s=3, alpha=0.25, color="#94a3b8",
                 label="Old formula")
    left.scatter(target, prediction, s=3, alpha=0.25, color="#2563eb",
                 label="AI model")
    left.plot([0, limit], [0, limit], color="#111827", linewidth=1)
    left.set_xlim(0, limit)
    left.set_ylim(0, limit)
    left.set_xlabel("Actual seconds to hospital")
    left.set_ylabel("Predicted seconds to hospital")
    left.set_title("Predicted vs actual (unseen traffic)")
    left.legend(markerscale=4)

    bins = np.linspace(0, 1, 11)
    centres = (bins[:-1] + bins[1:]) / 2 * 100
    groups = np.digitize(progress, bins[1:-1])
    for values, label, color in [
        (formula, "Old formula", "#94a3b8"),
        (prediction, "AI model", "#2563eb"),
    ]:
        mae = [
            np.mean(np.abs(values[groups == g] - target[groups == g]))
            if np.any(groups == g) else np.nan
            for g in range(len(centres))
        ]
        right.plot(centres, mae, marker="o", label=label, color=color)

    right.set_xlabel("Trip progress (%)")
    right.set_ylabel("Average error (seconds)")
    right.set_title("Average ETA error along the trip")
    right.legend()
    right.grid(alpha=0.3)

    figure.tight_layout()
    figure.savefig(path, dpi=130)


def main():
    os.makedirs(MODELS_DIR, exist_ok=True)

    rows = load_rows()
    metrics = {}

    for name in TARGETS:
        print(f"\nTraining '{name}' model...")
        results, test, prediction = train_one(rows, name)
        metrics[name] = results

        ai = results["ai_model"]
        old = results["old_formula"]
        print(
            f"  Tested on {results['test_trips']} unseen trips "
            f"({results['test_rows']:,} seconds)"
        )
        print(
            f"  Old formula: {old['mae_seconds']:6.1f} s average error "
            f"({old['mape_percent']:.0f}%)"
        )
        if "average_speed_guess" in results:
            guess = results["average_speed_guess"]
            print(
                f"  Avg-speed:   {guess['mae_seconds']:6.1f} s average error "
                f"({guess['mape_percent']:.0f}%)"
            )
        print(
            f"  AI model:    {ai['mae_seconds']:6.1f} s average error "
            f"({ai['mape_percent']:.0f}%)"
        )

        if name == "hospital":
            plot_report(test, prediction,
                        os.path.join(MODELS_DIR, "eta_report.png"))

    with open(os.path.join(MODELS_DIR, "eta_metrics.json"), "w") as file:
        json.dump(metrics, file, indent=2)

    print(f"\nSaved models, eta_metrics.json and eta_report.png to {MODELS_DIR}")


if __name__ == "__main__":
    main()
