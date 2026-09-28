"""
The supervisor agent as a LangGraph state machine:

    observe ──► assess ──► think ──► wait ──► observe ...
       │           │                  ▲
       │           └── nothing new ───┘
       └── ambulance arrived: final summary (think) ──► END

observe and assess are plain code (fast, free): they read the situation
through the MCP tools and decide whether anything needs Claude. Claude
(think) is only called for a trigger: a queue or jam ahead, the start
of the trip, a periodic status update, or the arrival.
"""

import asyncio
import logging
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent.mcp_link import ToolFailed

logger = logging.getLogger(__name__)

# A signal (not the next one) with at least this many cars queued, within
# this distance, is worth an early-green decision.
QUEUE_TRIGGER_CARS = 3
TRIGGER_DISTANCE_METERS = 1000

# A jammed road starting within this distance ahead, with at least this
# many vehicles stopped on it, is worth a look. (Fewer stopped vehicles
# is usually just one car braking, or the ambulance itself.)
JAM_TRIGGER_METERS = 800
JAM_TRIGGER_STOPPED = 3

# Don't reconsider the same signal / jam more often than this (sim s).
RECONSIDER_AFTER_SECONDS = 45

# Status update on the dashboard about this often (sim s). The
# dashboard already shows ETA, distance and speed, so rarely.
STATUS_EVERY_SECONDS = 120

# How much of the road ahead goes into the snapshot for Claude.
SNAPSHOT_TRAFFIC_METERS = 1500


class AgentState(TypedDict, total=False):
    phase: str                 # "waiting", "driving", "arrived", "stopped"
    time: float                # simulated time of the last observation
    snapshot: dict             # what Claude sees
    trigger: str               # why Claude is being called, or None
    considered: dict           # "signal 5" / "jam <road>" -> sim time
    last_status_time: float
    trip_started: bool
    summary_done: bool
    decisions: int


def build_graph(tools, brain, interval_seconds):

    async def observe(state):
        try:
            status = await tools.call("get_simulation_status")
        except Exception as error:
            logger.warning("Cannot reach the MCP server: %s", error)
            return {"phase": "stopped"}

        ambulance_status = status.get("ambulance", "")

        if status.get("simulation") in ("stopped", "error"):
            phase = "arrived" if ambulance_status.startswith("arrived") else "stopped"
            return {"phase": phase, "snapshot": state.get("snapshot")}

        if ambulance_status.startswith("arrived"):
            ambulance = await tools.call("get_ambulance_state")
            return {"phase": "arrived", "snapshot": {"ambulance": ambulance}}

        if not ambulance_status.startswith("driving"):
            return {"phase": "waiting"}

        try:
            ambulance = await tools.call("get_ambulance_state")
            signals = await tools.call("get_upcoming_signals")
            traffic = await tools.call("get_route_traffic", {"max_roads": 40})
        except ToolFailed:
            # The ambulance arrived between two calls.
            return {"phase": "waiting"}

        not_free = [
            road for road in traffic["roads"]
            if road["level"] != "free"
            and road["starts_in_meters"] <= SNAPSHOT_TRAFFIC_METERS
        ]

        snapshot = {
            "simulated_time": status.get("simulated_time_seconds"),
            "ambulance": {
                key: ambulance.get(key)
                for key in (
                    "speed_kmh", "distance_left_meters", "eta_seconds",
                    "trip_time_seconds", "stops_so_far",
                    "signals_passed", "signals_total",
                )
            },
            "signals_ahead": [
                {key: value for key, value in signal.items() if key != "signal_id"}
                for signal in signals["signals"]
            ],
            "traffic_ahead": {
                "roads_by_level": traffic["summary"],
                f"slow_or_jammed_within_{SNAPSHOT_TRAFFIC_METERS}_m": [
                    {
                        key: road[key]
                        for key in (
                            "route_index", "starts_in_meters",
                            "length_meters", "level",
                            "stopped_vehicles", "speed_percent_of_limit",
                        )
                    }
                    for road in not_free
                ],
            },
        }

        return {
            "phase": "driving",
            "time": status.get("simulated_time_seconds") or 0.0,
            "snapshot": snapshot,
        }

    def assess(state):
        """Plain rules: does anything need Claude right now?"""

        now = state["time"]
        considered = dict(state.get("considered", {}))
        snapshot = state["snapshot"]

        def fresh(key):
            last = considered.get(key)
            return last is None or now - last >= RECONSIDER_AFTER_SECONDS

        if not state.get("trip_started"):
            return {
                "trigger": "The ambulance has just set off. Post a short "
                           "note on the situation ahead.",
                "trip_started": True,
                "last_status_time": now,
            }

        # A queue at a signal further ahead.
        for signal in snapshot["signals_ahead"][1:]:
            key = f"signal {signal['signal_number']}"
            if (
                signal["distance_meters"] <= TRIGGER_DISTANCE_METERS
                and signal["cars_queued"] >= QUEUE_TRIGGER_CARS
                and not signal["priority"]
                and fresh(key)
            ):
                considered[key] = now
                return {
                    "trigger": (
                        f"{signal['cars_queued']} cars are queued at signal "
                        f"{signal['signal_number']}, "
                        f"{signal['distance_meters']} m ahead. Decide whether "
                        "an early green there would help the ambulance."
                    ),
                    "considered": considered,
                }

        # A jam on the road ahead.
        jams = snapshot["traffic_ahead"][
            f"slow_or_jammed_within_{SNAPSHOT_TRAFFIC_METERS}_m"
        ]
        for road in jams:
            key = f"jam on road {road['route_index']}"
            if (
                road["level"] == "jammed"
                and road["stopped_vehicles"] >= JAM_TRIGGER_STOPPED
                and road["starts_in_meters"] <= JAM_TRIGGER_METERS
                and fresh(key)
            ):
                considered[key] = now
                return {
                    "trigger": (
                        f"Traffic is jammed {road['starts_in_meters']} m ahead "
                        f"({road['stopped_vehicles']} vehicles stopped). "
                        "Decide whether an early green at a signal ahead "
                        "would clear it in time."
                    ),
                    "considered": considered,
                }

        if now - state.get("last_status_time", now) >= STATUS_EVERY_SECONDS:
            return {
                "trigger": "Periodic status update: post a short note on "
                           "what is coming up next on the route. The "
                           "dashboard already shows ETA, distance and "
                           "speed, so don't just repeat those numbers.",
                "last_status_time": now,
            }

        return {"trigger": None}

    async def think(state):
        trigger = state.get("trigger")

        if state.get("phase") == "arrived":
            trigger = (
                "The ambulance has reached the hospital. Post a one or two "
                "sentence summary of the trip (use get_corridor_events)."
            )

        logger.info("Trigger: %s", trigger)
        used = await brain.think(trigger, state.get("snapshot") or {})

        update = {"decisions": state.get("decisions", 0) + 1}
        if state.get("phase") == "arrived":
            update["summary_done"] = True
        if not used:
            logger.info("  (no tools used)")
        return update

    async def wait(state):
        await asyncio.sleep(interval_seconds)
        return {}

    def after_observe(state):
        phase = state.get("phase")
        if phase == "stopped":
            return "end"
        if phase == "arrived":
            return "end" if state.get("summary_done") else "think"
        if phase == "waiting":
            return "wait"
        return "assess"

    def after_assess(state):
        return "think" if state.get("trigger") else "wait"

    def after_think(state):
        return "end" if state.get("summary_done") else "wait"

    graph = StateGraph(AgentState)
    graph.add_node("observe", observe)
    graph.add_node("assess", assess)
    graph.add_node("think", think)
    graph.add_node("wait", wait)

    graph.add_edge(START, "observe")
    graph.add_conditional_edges(
        "observe", after_observe,
        {"assess": "assess", "think": "think", "wait": "wait", "end": END},
    )
    graph.add_conditional_edges(
        "assess", after_assess, {"think": "think", "wait": "wait"}
    )
    graph.add_conditional_edges(
        "think", after_think, {"wait": "wait", "end": END}
    )
    graph.add_edge("wait", "observe")

    return graph.compile()
