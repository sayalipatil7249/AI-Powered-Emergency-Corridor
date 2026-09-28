"""
Measure how long junctions really need to clear for an ambulance, as
training data for the AI clearance model (ai/clearance.py).

In ordinary city traffic (no ambulance), junctions are picked at random
and switched exactly like the corridor does it (yellow, all red, then
green for one movement). We record what the junction looked like at
that moment and how long it took until every vehicle then waiting on
that movement's lanes had driven off. The signal then returns to normal
and is left alone for a while.

Output (resumable, one CSV per simulation):
    data/clearance/<level>_seed<seed>.csv

Usage (from the project root):
    python -m ai.clearance_experiments --seeds 15 --workers 8
"""

import argparse
import csv
import itertools
import logging
import os
import random
import sys
import time
from multiprocessing import Pool

import sumo

from ai.clearance import (
    FEATURE_COLUMNS,
    MAX_CLEARANCE_SECONDS,
    clearance_features,
)
from ai.scenarios import (
    NET_FILE,
    PROJECT_ROOT,
    SUMO_TRAFFIC_OPTIONS,
    TRAFFIC_LEVELS,
    WARMUP_SECONDS,
    generate_traffic,
)
from corridor.engine import ALL_RED_SECONDS, YELLOW_SECONDS, transition_states
from simulation.sumo.adapters import (
    SumoSignalController,
    SumoSimulation,
    SumoTrafficSource,
)
from simulation.sumo.sumo_bridge import apply_city_speed_limits

OUTPUT_DIR = os.path.join(PROJECT_ROOT, "data", "clearance")
SUMO_BINARY = os.path.join(sumo.SUMO_HOME, "bin", "sumo")

# Simulated seconds per run, junctions switched at the same time, and
# how long a junction is left alone after a test.
END_SECONDS = 2700
CONCURRENT_TESTS = 15
COOLDOWN_SECONDS = 120

# Share of tests on movements with at least one vehicle waiting or
# driving (the interesting cases); the rest are picked at random.
BUSY_SHARE = 0.75

META_COLUMNS = ["level", "traffic_seed", "time", "signal_id", "tls_index"]
TARGET_COLUMNS = ["clearance_seconds", "censored"]


def _movements(signals, signal_id):
    """Link indices of a signal that belong to a real road movement and
    have a green phase in the normal program."""

    result = []
    for tls_index, group in enumerate(signals.controlled_links(signal_id)):
        link = next((item for item in group if item), None)
        if link is None or link[0].startswith(":"):
            continue
        if signals.find_green_phase(signal_id, tls_index) is None:
            continue
        result.append(tls_index)
    return result


def _start_test(traffic, signals, signal_id, tls_index, now):
    features, lanes = clearance_features(traffic, signals, signal_id, tls_index)
    if features is None or not lanes:
        return None

    target = signals.find_green_phase(signal_id, tls_index)
    current = signals.light_states(signal_id)

    tracked = set()
    for lane in lanes:
        tracked.update(traffic.lane_vehicle_ids(lane))

    test = {
        "signal_id": signal_id,
        "tls_index": tls_index,
        "started": now,
        "target": target,
        "lanes": lanes,
        "tracked": tracked,
        "features": features,
        "stage": "green",
    }

    if features["green_now"]:
        signals.hold_phase(signal_id, target)
    else:
        test["yellow"], test["red"] = transition_states(
            current, signals.phase_state(signal_id, target)
        )
        test["stage"] = "yellow"
        signals.set_light_states(signal_id, test["yellow"])

    return test


def _advance(traffic, signals, test, now):
    """Move the switch on; return the clearance time once known."""

    elapsed = now - test["started"]

    if test["stage"] == "yellow" and elapsed >= YELLOW_SECONDS:
        test["stage"] = "all_red"
        signals.set_light_states(test["signal_id"], test["red"])
    if test["stage"] == "all_red" and elapsed >= YELLOW_SECONDS + ALL_RED_SECONDS:
        test["stage"] = "green"
    if test["stage"] != "green":
        # Not ready before the ambulance's light is green.
        return None
    signals.hold_phase(test["signal_id"], test["target"])

    present = set()
    for lane in test["lanes"]:
        present.update(traffic.lane_vehicle_ids(lane))

    if not test["tracked"] & present:
        return elapsed, 0
    if elapsed >= MAX_CLEARANCE_SECONDS:
        return MAX_CLEARANCE_SECONDS, 1
    return None


def run_one(config):
    level, seed = config
    output_file = os.path.join(OUTPUT_DIR, f"{level}_seed{seed}.csv")
    wall_start = time.time()

    logging.basicConfig(level=logging.WARNING)

    simulation = SumoSimulation()
    simulation.start([
        SUMO_BINARY,
        "--net-file", NET_FILE,
        "--route-files", generate_traffic(level, seed),
        "--seed", str(seed),
        *SUMO_TRAFFIC_OPTIONS,
    ])
    apply_city_speed_limits()

    traffic = SumoTrafficSource()
    signals = SumoSignalController()
    rng = random.Random(f"{level}-{seed}")

    movements = {
        signal_id: _movements(signals, signal_id)
        for signal_id in signals.signal_ids()
    }
    movements = {key: value for key, value in movements.items() if value}

    active = {}
    available_at = {}
    rows = []

    try:
        while simulation.time() < END_SECONDS:
            simulation.step()
            now = simulation.time()

            if now < WARMUP_SECONDS:
                continue

            for signal_id, test in list(active.items()):
                result = _advance(traffic, signals, test, now)
                if result is None:
                    continue

                clearance, censored = result
                signals.restore_normal(signal_id, test["target"])
                del active[signal_id]
                available_at[signal_id] = now + COOLDOWN_SECONDS

                rows.append({
                    "level": level,
                    "traffic_seed": seed,
                    "time": test["started"],
                    "signal_id": signal_id,
                    "tls_index": test["tls_index"],
                    **test["features"],
                    "clearance_seconds": clearance,
                    "censored": censored,
                })

            if len(active) >= CONCURRENT_TESTS:
                continue

            free = [
                signal_id for signal_id in movements
                if signal_id not in active
                and available_at.get(signal_id, 0) <= now
            ]
            if not free:
                continue

            signal_id = rng.choice(free)
            choices = movements[signal_id]

            if rng.random() < BUSY_SHARE:
                busy = [
                    tls_index for tls_index in choices
                    if (clearance_features(
                        traffic, signals, signal_id, tls_index
                    )[0] or {}).get("vehicles_on_movement", 0) > 0
                ]
                choices = busy or choices

            test = _start_test(
                traffic, signals, signal_id, rng.choice(choices), now
            )
            if test is not None:
                active[signal_id] = test
            else:
                available_at[signal_id] = now + COOLDOWN_SECONDS

    finally:
        simulation.close()

    with open(output_file, "w", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=META_COLUMNS + FEATURE_COLUMNS + TARGET_COLUMNS
        )
        writer.writeheader()
        writer.writerows(rows)

    return level, seed, len(rows), round(time.time() - wall_start, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=15)
    parser.add_argument("--levels", default=",".join(TRAFFIC_LEVELS))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    configs = [
        config
        for config in itertools.product(
            args.levels.split(","), range(1, args.seeds + 1)
        )
        if not os.path.exists(
            os.path.join(OUTPUT_DIR, f"{config[0]}_seed{config[1]}.csv")
        )
    ]
    print(f"{len(configs)} simulations to do.", flush=True)

    with Pool(args.workers) as pool:
        for done, (level, seed, count, wall) in enumerate(
            pool.imap_unordered(run_one, configs), start=1
        ):
            print(
                f"[{done}/{len(configs)}] {level} seed {seed}: "
                f"{count} junction switches, {wall} s wall",
                flush=True,
            )


if __name__ == "__main__":
    sys.exit(main())
