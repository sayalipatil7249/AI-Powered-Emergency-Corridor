"""
Collect training data for the AI traffic forecast (ai/road_speed.py).

Each run: background traffic (one level and seed) warms up, then every
SAMPLE_EVERY seconds we record every road's state (ai.road_speed.road_state).
Afterwards each sample is paired with the same road HORIZONS seconds
later: the model learns how fast a road will be from how it and its
neighbours look now. The SUMO options match the live app's.

Output (one file per run, resumable):
    data/road_speeds/<level>_s<seed>.npz   inputs X, target y (speed
        share HORIZON s later), sample time t, road index, seed

Usage (from the project root):
    python -m ai.collect_road_speeds --seeds 8 --workers 8
"""

import argparse
import itertools
import os
from multiprocessing import Pool

import numpy as np
import sumo

from ai import road_speed
from ai.scenarios import NET_FILE, PROJECT_ROOT, SUMO_TRAFFIC_OPTIONS, TRAFFIC_LEVELS, generate_traffic
from simulation.sumo.adapters import SumoSimulation, SumoTrafficSource
from simulation.sumo.sumo_bridge import JUNCTION_BLOCKER_SECONDS, apply_city_speed_limits

OUT_DIR = os.path.join(PROJECT_ROOT, "data", "road_speeds")
SUMO_BINARY = os.path.join(sumo.SUMO_HOME, "bin", "sumo")

WARMUP_SECONDS = 600
RECORD_SECONDS = 1800
SAMPLE_EVERY = 30
# Per sample time: every road with cars on it now or at the horizon, plus
# this share of the empty ones (mostly "empty stays empty").
EMPTY_SHARE = 0.03


def run(config):
    level, seed = config
    os.makedirs(OUT_DIR, exist_ok=True)
    out_file = os.path.join(OUT_DIR, f"{level}_s{seed}.npz")
    if os.path.exists(out_file):
        return out_file

    traffic_file = generate_traffic(level, seed)
    simulation = SumoSimulation()
    simulation.start([
        SUMO_BINARY, "--net-file", NET_FILE, "--route-files", traffic_file,
        "--seed", str(seed), *SUMO_TRAFFIC_OPTIONS,
        # As the live app (simulation/sumo/sumo_bridge.py sumo_command).
        "--ignore-junction-blocker", str(JUNCTION_BLOCKER_SECONDS),
        "--collision.action", "warn",
    ])
    apply_city_speed_limits()
    traffic = SumoTrafficSource()
    table = road_speed.road_table()

    states = {}
    try:
        end = WARMUP_SECONDS + RECORD_SECONDS + max(road_speed.HORIZONS)
        while simulation.time() < end:
            simulation.step()
            now = int(simulation.time())
            if now >= WARMUP_SECONDS and now % SAMPLE_EVERY == 0:
                states[now] = road_speed.road_state(traffic.vehicles(), table)
    finally:
        simulation.close()

    rng = np.random.default_rng(seed)
    # History for these inputs: the road's average speed share over this
    # run (the trainer replaces it with the training runs' average).
    history = np.mean([state[:, 2] for state in states.values()], axis=0)
    inputs, targets, times, roads = [], [], [], []
    for now, state in states.items():
        if now > WARMUP_SECONDS + RECORD_SECONDS:
            continue
        for horizon in road_speed.HORIZONS:
            later = states.get(now + horizon)
            if later is None:
                continue
            busy = (state[:, 0] > 0) | (later[:, 0] > 0)
            keep = np.flatnonzero(busy | (rng.random(len(busy)) < EMPTY_SHARE))
            rows = road_speed.features(state, table, level, history, (horizon,), keep)
            inputs.append(rows)
            targets.append(later[keep, 2])
            times.append(np.full(len(keep), now))
            roads.append(keep)
    np.savez_compressed(
        out_file, X=np.vstack(inputs), y=np.concatenate(targets),
        t=np.concatenate(times), road=np.concatenate(roads), seed=seed,
    )
    return out_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=8)
    parser.add_argument("--levels", default=",".join(TRAFFIC_LEVELS))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    configs = list(itertools.product(args.levels.split(","), range(1, args.seeds + 1)))
    with Pool(args.workers) as pool:
        for done, path in enumerate(pool.imap_unordered(run, configs), start=1):
            print(f"[{done}/{len(configs)}] {os.path.basename(path)}", flush=True)


if __name__ == "__main__":
    main()
