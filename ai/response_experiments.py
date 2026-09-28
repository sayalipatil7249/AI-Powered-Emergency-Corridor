"""
Does the deadlock response (corridor/response.py) help? Drives the same
random trips twice, with the same traffic: response off, and response on
(AI deadlock predictor + re-route or police). Records trip time, seconds
standing still and what the response decided.

Output: data/response/trips.csv, summary printed at the end.

Usage (from the project root, after ai.train_deadlock):
    python -m ai.response_experiments --routes 30 --workers 5
"""

import argparse
import csv
import logging
import os
import sys
from multiprocessing import Pool

import pandas as pd

from ai import deadlock
from ai.clearance import clearance_predictor
from ai.deadlock import route_info
from ai.deadlock_experiments import IGNORE_FIRST_SECONDS
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
from corridor.response import DeadlockResponse
from simulation.sumo import route_planner
from simulation.sumo.adapters import (
    SumoAmbulanceTracker,
    SumoResponder,
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

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "response")
TRIPS_FILE = os.path.join(OUTPUT_DIR, "trips.csv")
LEVELS = ["normal", "heavy"]
COLUMNS = ["route_id", "level", "response", "status", "trip_seconds",
           "stopped_seconds", "decisions", "police_arrival_seconds"]


def run_one(job):
    route, level, response_on = job
    try:
        return _drive(route, level, response_on)
    except Exception as error:
        return {"route_id": route["route_id"], "level": level,
                "response": int(response_on), "status": f"error: {error}"}


def _drive(route, level, response_on):
    logging.basicConfig(level=logging.WARNING)
    simulation = SumoSimulation()
    simulation.start([
        SUMO_BINARY,
        "--net-file", NET_FILE,
        "--route-files", generate_traffic(level, route["route_id"] % TRAFFIC_SEEDS + 1),
        "--additional-files", f"{VTYPES_FILE},{AMBULANCE_VTYPE_FILE}",
        "--seed", str(route["route_id"] + 2000),
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
    level_index = TRAFFIC_LEVELS.index(level)
    # Both cases run the same checks; only "on" acts on them.
    response = DeadlockResponse(
        ambulance, traffic, SumoResponder(), route_planner.police_stations(),
        route_ahead=lambda info: deadlock.route_ahead(info, traffic, ambulance, level_index),
        predict=deadlock.predict, traffic_level=level_index,
        road_name=route_planner.road_name, act=response_on,
        route_seconds=lambda roads: route_planner.estimate_route_seconds(
            roads, level_index
        ),
    )

    cache = depart = None
    status, elapsed, stopped = "timeout", 0, 0
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
                if cache is not None:
                    status = "arrived"
                    break
                if now - depart > 600:
                    status = "not_inserted"
                    break
                continue
            if cache is None:
                cache = RouteCache(ambulance, traffic)
                engine.build_route_signals()
                if response:
                    response.set_route(route_info(ambulance.route(), traffic))
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

            if response and response.step(now) == "rerouted":
                engine.reset()
                engine.route_signals = []
                engine.build_route_signals()
                cache = RouteCache(ambulance, traffic)
                response.set_route(route_info(ambulance.route(), traffic))

            if elapsed >= IGNORE_FIRST_SECONDS and ambulance.speed() < deadlock.STUCK_SPEED:
                stopped += 1
    finally:
        if response:
            response.close()
        simulation.close()

    decisions = []
    police_arrival = ""
    if response_on:
        decisions = [e[:120] for e in response.events]
        if response.police and "arrived_at" in response.police:
            police_arrival = round(response.police["arrived_at"] - response.police["sent_at"])
    return {
        "route_id": route["route_id"], "level": level,
        "response": int(response_on), "status": status,
        "trip_seconds": elapsed, "stopped_seconds": stopped,
        "decisions": " | ".join(decisions),
        "police_arrival_seconds": police_arrival,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--routes", type=int, default=30)
    parser.add_argument("--workers", type=int, default=5)
    args = parser.parse_args()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    routes = sample_routes(args.routes, seed=21)
    jobs = [(route, level, on) for route in routes for level in LEVELS
            for on in (False, True)]
    print(f"{len(jobs)} trips to drive.", flush=True)

    rows = []
    with Pool(args.workers) as pool:
        for count, row in enumerate(pool.imap_unordered(run_one, jobs), start=1):
            rows.append(row)
            print(f"[{count}/{len(jobs)}] {row}", flush=True)

    with open(TRIPS_FILE, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    trips = pd.DataFrame(rows)
    trips = trips[trips["status"] == "arrived"]
    paired = trips.pivot_table(
        index=["route_id", "level"], columns="response",
        values=["trip_seconds", "stopped_seconds"],
    ).dropna()
    paired.columns = [f"{name}_{'on' if on else 'off'}" for name, on in paired.columns]
    table = paired.groupby("level").mean().round(1)
    table["trips"] = paired.groupby("level").size()
    print("\nAverage per trip, response off vs on (same routes and traffic):")
    print(table.to_string())
    helped = trips[trips["response"] == 1]["decisions"].str.contains("Alert sent|Re-routed").sum()
    print(f"\nTrips where the response acted: {helped} of "
          f"{(trips['response'] == 1).sum()}")


if __name__ == "__main__":
    sys.exit(main())
