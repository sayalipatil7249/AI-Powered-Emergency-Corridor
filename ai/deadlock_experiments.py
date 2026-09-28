"""
Training data for the AI deadlock predictor (ai/deadlock.py).

Drives random ambulance trips (normal and heavy traffic, with the full
corridor) and every SAMPLE_EVERY seconds records what the route ahead
looks like. After the trip we know how long the ambulance really stood
still in the following minutes: that is what the model learns to predict.

Output (resumable): data/deadlock/samples.csv

Usage (from the project root):
    python -m ai.deadlock_experiments --routes 80 --workers 5
"""

import argparse
import csv
import logging
import os
import sys
import time
from multiprocessing import Pool

from ai.clearance import clearance_predictor
from ai.deadlock import (
    FEATURE_COLUMNS,
    HORIZON_SECONDS,
    STUCK_SPEED,
    route_ahead,
    route_info,
)
from ai.eta_model import estimate as estimate_eta
from ai.features import RouteCache
from ai.pretrip import TRAFFIC_LEVELS
from ai.pretrip_experiments import (
    AMBULANCE_ID,
    MAX_TRIP_SECONDS,
    SUMO_BINARY,
    TRAFFIC_SEEDS,
    sample_routes,
)
from ai.scenarios import NET_FILE, PROJECT_ROOT, generate_traffic
from corridor.engine import CorridorEngine
from simulation.sumo.adapters import (
    SumoAmbulanceTracker,
    SumoSignalController,
    SumoSimulation,
    SumoTrafficSource,
)
from simulation.sumo.sumo_bridge import (
    AMBULANCE_DEPART_TIME,
    AMBULANCE_VTYPE_FILE,
    VTYPES_FILE,
    apply_city_speed_limits,
)

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "deadlock")
SAMPLES_FILE = os.path.join(OUTPUT_DIR, "samples.csv")

SAMPLE_EVERY = 5
# Pulling away at the start is not "stuck".
IGNORE_FIRST_SECONDS = 15
LEVELS = ["normal", "heavy"]

COLUMNS = ["run_id", "route_id", "level", "trip_time", *FEATURE_COLUMNS,
           "stopped_seconds_ahead"]


def run_one(job):
    route, level = job
    try:
        return _drive(route, level)
    except Exception as error:  # SUMO occasionally closes the connection
        logging.warning("r%s %s failed: %s", route["route_id"], level, error)
        return []


def _drive(route, level):
    run_id = f"r{route['route_id']}_{level}"
    logging.basicConfig(level=logging.WARNING)

    simulation = SumoSimulation()
    simulation.start([
        SUMO_BINARY,
        "--net-file", NET_FILE,
        "--route-files", generate_traffic(level, route["route_id"] % TRAFFIC_SEEDS + 1),
        "--additional-files", f"{VTYPES_FILE},{AMBULANCE_VTYPE_FILE}",
        "--seed", str(route["route_id"] + 1000),
        "--ignore-route-errors", "--no-step-log", "--no-warnings",
        "--device.rerouting.probability", "0.5",
        "--device.rerouting.period", "60",
    ])
    apply_city_speed_limits()

    ambulance = SumoAmbulanceTracker(AMBULANCE_ID)
    traffic = SumoTrafficSource()
    signals = SumoSignalController()
    engine = CorridorEngine(
        ambulance, traffic, signals,
        clearance_predictor=clearance_predictor(traffic, signals),
    )

    info = cache = depart = None
    speeds = []   # (trip second, speed) every second
    samples = []  # (trip second, features)
    try:
        while True:
            simulation.step()
            now = simulation.time()
            if depart is None and now >= AMBULANCE_DEPART_TIME:
                ambulance.dispatch(route["roads"], route["depart_position"],
                                   route["arrival_position"])
                depart = now
            if depart is None:
                continue
            if not ambulance.is_on_road():
                if cache is not None or now - depart > 600:
                    break
                continue
            if cache is None:
                cache = RouteCache(ambulance, traffic)
                engine.build_route_signals()
                info = route_info(ambulance.route(), traffic)
            elapsed = now - depart
            if elapsed > MAX_TRIP_SECONDS:
                break

            estimate = estimate_eta(cache, engine.route_signals)
            upcoming = engine.upcoming_signals()
            engine.apply(
                upcoming, now,
                estimate["next_signal_eta_seconds"]
                if upcoming
                and estimate["next_signal_route_index"] == upcoming[0]["route_index"]
                else None,
            )

            speeds.append((elapsed, ambulance.speed()))
            if int(elapsed) % SAMPLE_EVERY == 0:
                features, _ = route_ahead(
                    info, traffic, ambulance, TRAFFIC_LEVELS.index(level)
                )
                samples.append((elapsed, features))
    finally:
        simulation.close()

    trip_time = speeds[-1][0] if speeds else 0
    rows = []
    for elapsed, features in samples:
        # Only samples with a full horizon ahead (or up to arrival).
        stopped = sum(
            1 for second, speed in speeds
            if elapsed <= second < elapsed + HORIZON_SECONDS
            and second >= IGNORE_FIRST_SECONDS and speed < STUCK_SPEED
        )
        rows.append({
            "run_id": run_id, "route_id": route["route_id"], "level": level,
            "trip_time": trip_time, **features,
            "stopped_seconds_ahead": stopped,
        })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=int, default=80)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    done = set()
    if os.path.exists(SAMPLES_FILE):
        with open(SAMPLES_FILE) as file:
            done = {row["run_id"] for row in csv.DictReader(file)}

    # Different routes from the pre-trip data (seed 7).
    routes = sample_routes(args.routes, seed=7)
    jobs = [(route, level) for route in routes for level in LEVELS
            if f"r{route['route_id']}_{level}" not in done]
    print(f"{len(jobs)} trips to drive.", flush=True)

    with Pool(args.workers) as pool, open(SAMPLES_FILE, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        if not done:
            writer.writeheader()
        started = time.time()
        for count, rows in enumerate(pool.imap_unordered(run_one, jobs), start=1):
            writer.writerows(rows)
            file.flush()
            print(f"[{count}/{len(jobs)}] {len(rows)} samples "
                  f"({time.time() - started:.0f} s wall)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
