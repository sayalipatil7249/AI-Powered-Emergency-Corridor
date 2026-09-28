"""
Drive many random ambulance trips in the simulation, as training data for
the AI pre-trip estimate (ai/pretrip.py).

Each trip: a random start inside the simulated area to a random hospital,
planned like the dashboard does it, driven in light, normal or heavy
traffic with the full corridor (AI signal timing, rescue lane), exactly
like the live demo. We record the route's description and how long the
trip really took.

Output (resumable): data/pretrip/trips.csv

Usage (from the project root):
    python -m ai.pretrip_experiments --routes 80 --workers 5
"""

import argparse
import csv
import logging
import os
import random
import sys
import time
from multiprocessing import Pool

import sumo

from ai.clearance import clearance_predictor
from ai.eta_model import estimate as estimate_eta
from ai.features import RouteCache
from ai.pretrip import FEATURE_COLUMNS, TRAFFIC_LEVELS
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

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "pretrip")
TRIPS_FILE = os.path.join(OUTPUT_DIR, "trips.csv")
SUMO_BINARY = os.path.join(sumo.SUMO_HOME, "bin", "sumo")
AMBULANCE_ID = "ambulance_01"

# Routes long enough to matter, short enough to fit the area.
MIN_LENGTH, MAX_LENGTH = 1500, 8000
MAX_TRIP_SECONDS = 2400
TRAFFIC_SEEDS = 15

COLUMNS = ["run_id", "route_id", "level", "traffic_seed", "status",
           "trip_seconds", *[c for c in FEATURE_COLUMNS if c != "traffic_level"]]


def sample_routes(count, seed=0):
    """Random planned trips: [{"route_id", "roads", "depart_position",
    "arrival_position", features...}]."""

    from ai.pretrip import route_features
    from simulation.sumo import route_planner

    rng = random.Random(seed)
    net = route_planner._net()
    bounds = route_planner.area()
    hospitals = route_planner.hospitals()
    routes = []

    while len(routes) < count:
        latitude = rng.uniform(bounds["south"], bounds["north"])
        longitude = rng.uniform(bounds["west"], bounds["east"])
        if not route_planner.inside_area(latitude, longitude):
            continue
        hospital = rng.choice(hospitals)
        try:
            plan = route_planner.plan_route(
                latitude, longitude, hospital["latitude"],
                hospital["longitude"], hospital["name"],
            )
        except route_planner.PlanningError:
            continue
        if not MIN_LENGTH <= plan["length_meters"] <= MAX_LENGTH:
            continue

        edges = [net.getEdge(road) for road in plan["roads"]]
        routes.append({
            "route_id": len(routes) + 1,
            "roads": plan["roads"],
            "depart_position": plan["depart_position"],
            "arrival_position": plan["arrival_position"],
            **route_features(
                edges, plan["length_meters"],
                plan["minutes_without_traffic"] * 60, len(plan["signals"]),
            ),
        })
    return routes


def run_one(job):
    """One trip; a crashed simulation is recorded instead of stopping all."""
    route, level = job
    try:
        return _drive(route, level)
    except Exception as error:  # SUMO occasionally closes the connection
        return {
            "run_id": f"r{route['route_id']}_{level}",
            "route_id": route["route_id"],
            "level": level,
            "traffic_seed": route["route_id"] % TRAFFIC_SEEDS + 1,
            "status": f"error: {type(error).__name__}",
            "trip_seconds": "",
            **{c: route[c] for c in FEATURE_COLUMNS if c != "traffic_level"},
        }


def _drive(route, level):
    traffic_seed = route["route_id"] % TRAFFIC_SEEDS + 1
    run_id = f"r{route['route_id']}_{level}"
    logging.basicConfig(level=logging.WARNING)

    simulation = SumoSimulation()
    simulation.start([
        SUMO_BINARY,
        "--net-file", NET_FILE,
        "--route-files", generate_traffic(level, traffic_seed),
        "--additional-files", f"{VTYPES_FILE},{AMBULANCE_VTYPE_FILE}",
        "--seed", str(route["route_id"]),
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

    status, trip_seconds = "timeout", ""
    cache = depart = None
    try:
        while True:
            simulation.step()
            now = simulation.time()

            if depart is None and now >= AMBULANCE_DEPART_TIME:
                ambulance.dispatch(
                    route["roads"], route["depart_position"],
                    route["arrival_position"],
                )
                depart = now

            if depart is None:
                continue
            if not ambulance.is_on_road():
                if cache is not None:
                    status, trip_seconds = "arrived", now - depart
                    break
                if now - depart > 600:
                    status = "not_inserted"
                    break
                continue
            if cache is None:
                cache = RouteCache(ambulance, traffic)
                engine.build_route_signals()
            if now - depart > MAX_TRIP_SECONDS:
                break

            # Same as the dashboard: AI arrival prediction drives the timing.
            estimate = estimate_eta(cache, engine.route_signals)
            upcoming = engine.upcoming_signals()
            engine.apply(
                upcoming, now,
                estimate["next_signal_eta_seconds"]
                if upcoming
                and estimate["next_signal_route_index"] == upcoming[0]["route_index"]
                else None,
            )
    finally:
        simulation.close()

    return {
        "run_id": run_id,
        "route_id": route["route_id"],
        "level": level,
        "traffic_seed": traffic_seed,
        "status": status,
        "trip_seconds": trip_seconds,
        **{c: route[c] for c in FEATURE_COLUMNS if c != "traffic_level"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=int, default=80)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    done = set()
    if os.path.exists(TRIPS_FILE):
        with open(TRIPS_FILE) as file:
            done = {row["run_id"] for row in csv.DictReader(file)}

    routes = sample_routes(args.routes)
    jobs = [
        (route, level) for route in routes for level in TRAFFIC_LEVELS
        if f"r{route['route_id']}_{level}" not in done
    ]
    print(f"{len(routes)} routes, {len(jobs)} trips to drive.", flush=True)

    with Pool(args.workers) as pool, open(TRIPS_FILE, "a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        if not done:
            writer.writeheader()
        started = time.time()
        for count, row in enumerate(pool.imap_unordered(run_one, jobs), start=1):
            writer.writerow(row)
            file.flush()
            print(
                f"[{count}/{len(jobs)}] {row['run_id']}: {row['status']}, "
                f"{row['trip_seconds']} s ({time.time() - started:.0f} s wall)",
                flush=True,
            )


if __name__ == "__main__":
    sys.exit(main())
