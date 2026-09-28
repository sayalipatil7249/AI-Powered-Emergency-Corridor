"""
Compare ambulance trips with and without the emergency corridor, and
the corridor's ways of timing the switch, using the same traffic
(data/runs/summary.csv from ai.run_experiments --corridor all):

    immediate  the next signal turns green as soon as it is next (old)
    rule       fixed rule: yellow + all red + 2 s per queued car + margin
    ai         AI: predicted arrival vs predicted clearance time

For each: how much faster the ambulance is, and how long cross traffic
at the route's signals stood still (vehicle-seconds), compared with no
corridor.

Usage (from the project root):
    python -m ai.compare_corridor
"""

import os

import pandas as pd

from ai.scenarios import PROJECT_ROOT

SUMMARY_FILE = os.path.join(PROJECT_ROOT, "data", "runs", "summary.csv")

TIMING_ORDER = ["immediate", "rule", "ai"]
LEVEL_ORDER = ["light", "normal", "heavy"]


def main():
    summary = pd.read_csv(SUMMARY_FILE)
    summary = summary[summary["status"] == "arrived"]

    key = ["level", "traffic_seed", "depart"]
    off = summary[summary["timing"] == "off"].set_index(key)

    rows = []
    for timing in TIMING_ORDER:
        runs = summary[summary["timing"] == timing].set_index(key)
        paired = runs.join(off, rsuffix="_off", how="inner")
        if paired.empty:
            continue

        paired = paired.reset_index()
        for level, group in [("all", paired)] + list(paired.groupby("level")):
            rows.append({
                "timing": timing,
                "level": level,
                "trips": len(group),
                "trip_s_off": group["trip_time_off"].mean(),
                "trip_s": group["trip_time"].mean(),
                "percent_faster": (
                    (group["trip_time_off"] - group["trip_time"]).mean()
                    / group["trip_time_off"].mean() * 100
                ),
                "stops_off": group["stops_off"].mean(),
                "stops": group["stops"].mean(),
                "cross_stopped_off": group["cross_traffic_stopped_seconds_off"].mean(),
                "cross_stopped": group["cross_traffic_stopped_seconds"].mean(),
            })

    table = pd.DataFrame(rows)
    if table.empty:
        raise SystemExit("No paired trips. Run: python -m ai.run_experiments --corridor all")

    table["cross_traffic_change_percent"] = (
        (table["cross_stopped"] - table["cross_stopped_off"])
        / table["cross_stopped_off"] * 100
    )
    table["level"] = pd.Categorical(
        table["level"], ["all"] + LEVEL_ORDER, ordered=True
    )
    table["timing"] = pd.Categorical(table["timing"], TIMING_ORDER, ordered=True)
    table = table.sort_values(["level", "timing"])

    print(
        "Ambulance trip time and cross traffic stopped at the route's signals\n"
        "(cross_stopped = vehicle-seconds standing still during the trip)\n"
    )
    print(table.round(1).to_string(index=False))


if __name__ == "__main__":
    main()
