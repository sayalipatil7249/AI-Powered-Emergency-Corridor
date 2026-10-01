"""
Run many headless ambulance trips and record training data.

Each run: background traffic warms up, the ambulance departs, the
emergency corridor (the same logic the dashboard uses) controls the
signals, and every second we record what the ambulance "sees"
(ai/features.py) plus, after it arrives, how long it really took.

Output (resumable, one CSV per run):
    data/runs/<run_id>.csv      one row per second of the trip
    data/runs/summary.csv       one row per trip

Usage (from the project root):
    python -m ai.run_experiments --seeds 15 --workers 8
"""

import argparse
import csv
import itertools
import logging
import os
import sys
import time
from multiprocessing import Pool

from ai.clearance import clearance_predictor
from ai.eta_model import next_signal_seconds
from ai.features import AMBULANCE_ID, FEATURE_COLUMNS, RouteCache, extract_features
from corridor.engine import CorridorEngine
from simulation.sumo.adapters import (
    SumoAmbulanceTracker,
    SumoSignalController,
    SumoSimulation,
    SumoTrafficSource,
)
from simulation.sumo.sumo_bridge import SUMO_HOME, apply_city_speed_limits
from ai.scenarios import (
    NET_FILE,
    PROJECT_ROOT,
    SUMO_TRAFFIC_OPTIONS,
    TRAFFIC_LEVELS,
    generate_ambulance,
    generate_traffic,
)

RUNS_DIR = os.path.join(PROJECT_ROOT, "data", "runs")
SUMMARY_FILE = os.path.join(RUNS_DIR, "summary.csv")

SUMO_BINARY = os.path.join(SUMO_HOME, "bin", "sumo")

# Give up on a trip that takes longer than this (seconds).
MAX_TRIP_SECONDS = 2400

META_COLUMNS = [
    "run_id",
    "level",
    "traffic_seed",
    "depart",
    "corridor",
    "time",
    "trip_elapsed",
    "next_signal_route_index",
]
TARGET_COLUMNS = ["target_hospital_eta", "target_next_signal_eta"]

SUMMARY_COLUMNS = [
    "run_id",
    "level",
    "traffic_seed",
    "depart",
    "corridor",
    "timing",
    "status",
    "trip_time",
    "waiting_time",
    "stops",
    "teleports",
    "formula_eta_at_start",
    "cross_traffic_stopped_seconds",
    "wall_seconds",
]

# How the corridor times the switch (corridor/engine.py), by run id
# suffix: "off" = no corridor.
TIMINGS = {"off": None, "on": "ai", "rule": "rule", "immediate": "immediate"}


def run_id_for(level, traffic_seed, depart, corridor):
    """corridor: a key of TIMINGS ("on" = AI timing, "off", ...)."""
    return f"{level}_s{traffic_seed}_d{depart}_{corridor}"


def run_one(config):
    """Run one trip. Returns its summary row (dict)."""

    level, traffic_seed, depart, corridor_mode = config
    run_id = run_id_for(level, traffic_seed, depart, corridor_mode)
    timing = TIMINGS[corridor_mode]
    corridor = timing is not None
    output_file = os.path.join(RUNS_DIR, f"{run_id}.csv")

    wall_start = time.time()

    traffic_file = generate_traffic(level, traffic_seed)
    ambulance_file = generate_ambulance(depart)

    # Only warnings from the corridor engine in worker processes.
    logging.basicConfig(level=logging.WARNING)

    simulation = SumoSimulation()
    simulation.start([
        SUMO_BINARY,
        "--net-file", NET_FILE,
        "--route-files", f"{traffic_file},{ambulance_file}",
        "--seed", str(traffic_seed * 1000 + depart),
        *SUMO_TRAFFIC_OPTIONS,
    ])
    apply_city_speed_limits()

    ambulance = SumoAmbulanceTracker(AMBULANCE_ID)
    traffic = SumoTrafficSource()
    signals = SumoSignalController()
    engine = CorridorEngine(
        ambulance, traffic, signals,
        clearance_predictor=clearance_predictor(traffic, signals),
        timing=timing or "ai",
    )

    rows = []
    pass_times = {}
    cache = None
    arrival_time = None
    status = "timeout"
    stops = 0
    was_moving = False
    waiting_time = 0.0
    teleports = 0

    # Cross traffic: vehicles stopped on the other roads into the
    # route's signals, summed over every second of the trip.
    cross_roads = []
    cross_traffic_stopped = 0

    try:
        while True:
            simulation.step()
            now = simulation.time()
            teleports += simulation.teleports_started()

            if AMBULANCE_ID in simulation.arrived_ids():
                arrival_time = now
                status = "arrived"
                break

            if not ambulance.is_on_road():
                if cache is not None:
                    # Left the network without arriving (teleported).
                    status = "lost"
                    break
                if now > depart + 600:
                    status = "not_inserted"
                    break
                continue

            if cache is None:
                cache = RouteCache(ambulance, traffic)
                engine.build_route_signals()
                cross_roads = _cross_roads(engine, signals, cache.edges)

            cross_traffic_stopped += sum(
                traffic.road_halting_count(road) for road in cross_roads
            )

            if now - depart > MAX_TRIP_SECONDS:
                break

            features, next_route_index = extract_features(
                cache, engine.route_signals
            )

            if corridor:
                # Same timing as the dashboard: the AI's predicted
                # arrival at the junction ahead decides when it switches.
                upcoming = engine.upcoming_signals()
                engine.apply(
                    upcoming,
                    now,
                    next_signal_seconds(features)
                    if upcoming
                    and upcoming[0]["route_index"] == next_route_index
                    else None,
                )

            # When did the ambulance pass each signal?
            current_index = ambulance.route_index()
            if ambulance.road_id().startswith(":"):
                current_index += 1
            for signal in engine.route_signals:
                if (
                    signal["route_index"] < current_index
                    and signal["route_index"] not in pass_times
                ):
                    pass_times[signal["route_index"]] = now

            moving = features["speed"] > 0.5
            if was_moving and not moving:
                stops += 1
            was_moving = moving
            waiting_time = ambulance.waiting_time()

            rows.append({
                "run_id": run_id,
                "level": level,
                "traffic_seed": traffic_seed,
                "depart": depart,
                "corridor": int(corridor),
                "time": now,
                "trip_elapsed": now - depart,
                "next_signal_route_index": next_route_index,
                **features,
            })

    finally:
        simulation.close()

    formula_eta_at_start = rows[0]["formula_eta"] if rows else None

    if status == "arrived":
        for row in rows:
            row["target_hospital_eta"] = arrival_time - row["time"]

            signal_index = row["next_signal_route_index"]
            passed_at = pass_times.get(signal_index)
            row["target_next_signal_eta"] = (
                passed_at - row["time"] if passed_at is not None else ""
            )

        with open(output_file, "w", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=META_COLUMNS + FEATURE_COLUMNS + TARGET_COLUMNS,
            )
            writer.writeheader()
            writer.writerows(rows)

    return {
        "run_id": run_id,
        "level": level,
        "traffic_seed": traffic_seed,
        "depart": depart,
        "corridor": int(corridor),
        "timing": timing or "off",
        "status": status,
        "trip_time": (arrival_time - depart) if arrival_time else "",
        "waiting_time": waiting_time,
        "stops": stops,
        "teleports": teleports,
        "formula_eta_at_start": formula_eta_at_start,
        "cross_traffic_stopped_seconds": cross_traffic_stopped,
        "wall_seconds": round(time.time() - wall_start, 1),
    }


def _cross_roads(engine, signals, route):
    """Roads into the route's signals other than the ambulance's own."""

    own = {route[item["route_index"]] for item in engine.route_signals}
    roads = set()
    for signal_id in {item["signal_id"] for item in engine.route_signals}:
        for group in signals.controlled_links(signal_id):
            for link in group:
                if link and not link[0].startswith(":"):
                    road = link[0].rsplit("_", 1)[0]
                    if road not in own:
                        roads.add(road)
    return sorted(roads)


def _prepare_traffic(args):
    level, seed = args
    generate_traffic(level, seed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=15,
                        help="traffic seeds per level")
    parser.add_argument("--departs", default="600,900,1200",
                        help="ambulance departure times (s)")
    parser.add_argument("--levels", default=",".join(TRAFFIC_LEVELS))
    parser.add_argument(
        "--corridor", choices=["on", "off", "both", "all"], default="on",
        help='"on" = AI timing; "both" = on + off; "all" also runs the '
             'fixed-rule and immediate (old) timings for comparison',
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    os.makedirs(RUNS_DIR, exist_ok=True)

    levels = args.levels.split(",")
    seeds = range(1, args.seeds + 1)
    departs = [int(value) for value in args.departs.split(",")]
    corridors = {
        "on": ["on"],
        "off": ["off"],
        "both": ["on", "off"],
        "all": ["on", "off", "rule", "immediate"],
    }[args.corridor]

    configs = [
        config
        for config in itertools.product(levels, seeds, departs, corridors)
        if not os.path.exists(
            os.path.join(RUNS_DIR, f"{run_id_for(*config)}.csv")
        )
    ]

    print(f"{len(configs)} runs to do.", flush=True)

    with Pool(args.workers) as pool:
        print("Generating traffic scenarios...", flush=True)
        pool.map(_prepare_traffic, list(itertools.product(levels, seeds)))

        write_header = not os.path.exists(SUMMARY_FILE)

        with open(SUMMARY_FILE, "a", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=SUMMARY_COLUMNS)
            if write_header:
                writer.writeheader()

            for done, summary in enumerate(
                pool.imap_unordered(run_one, configs), start=1
            ):
                writer.writerow(summary)
                file.flush()
                print(
                    f"[{done}/{len(configs)}] {summary['run_id']}: "
                    f"{summary['status']}, trip {summary['trip_time']} s, "
                    f"{summary['wall_seconds']} s wall",
                    flush=True,
                )


if __name__ == "__main__":
    sys.exit(main())
