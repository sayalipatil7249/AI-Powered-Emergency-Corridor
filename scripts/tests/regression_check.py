"""
Regression check for refactors: runs a few fixed simulations and
fingerprints the results (trip time, stops, and a hash of every
recorded second). SUMO is deterministic for a given seed, so after a
refactor that should not change behaviour the fingerprints must match.

Usage (from the project root):
    python -m scripts.tests.regression_check --save baseline.json
    ... refactor ...
    python -m scripts.tests.regression_check --compare baseline.json
"""

import argparse
import hashlib
import json
import os
import tempfile
from multiprocessing import Pool

import ai.run_experiments as runner

# (traffic level, traffic seed, ambulance departure, corridor mode: a key
# of ai.run_experiments.TIMINGS - "on" = AI timing, "off" = no corridor)
CONFIGS = [
    ("light", 1, 600, "on"),
    ("normal", 2, 600, "off"),
    ("heavy", 3, 900, "on"),
    ("heavy", 5, 1200, "on"),
]


def fingerprint(config):
    runner.RUNS_DIR = tempfile.mkdtemp(prefix="regression_")
    summary = runner.run_one(config)

    csv_path = os.path.join(
        runner.RUNS_DIR, f"{summary['run_id']}.csv"
    )
    with open(csv_path, "rb") as file:
        digest = hashlib.sha256(file.read()).hexdigest()

    return summary["run_id"], {
        "status": summary["status"],
        "trip_time": summary["trip_time"],
        "stops": summary["stops"],
        "waiting_time": summary["waiting_time"],
        "rows_sha256": digest,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--save")
    group.add_argument("--compare")
    args = parser.parse_args()

    with Pool(len(CONFIGS)) as pool:
        results = dict(pool.map(fingerprint, CONFIGS))

    if args.save:
        with open(args.save, "w") as file:
            json.dump(results, file, indent=2)
        for run_id, result in results.items():
            print(run_id, result["status"], result["trip_time"])
        print(f"Saved baseline to {args.save}")
        return

    with open(args.compare) as file:
        baseline = json.load(file)

    all_match = True
    for run_id, expected in baseline.items():
        actual = results[run_id]
        sim_match = all(
            actual[key] == expected[key]
            for key in ("status", "trip_time", "stops", "waiting_time")
        )
        rows_match = actual["rows_sha256"] == expected["rows_sha256"]
        all_match &= sim_match and rows_match
        print(
            f"{run_id}: simulation {'SAME' if sim_match else 'DIFFERENT'}, "
            f"recorded data {'SAME' if rows_match else 'DIFFERENT'} "
            f"(trip {expected['trip_time']} -> {actual['trip_time']})"
        )

    print("\nALL IDENTICAL" if all_match else "\nDIFFERENCES FOUND")


if __name__ == "__main__":
    main()
