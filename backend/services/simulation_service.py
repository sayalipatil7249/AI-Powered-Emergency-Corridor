"""
Runs the live simulation for the dashboard in a background thread.

Each step it advances the simulation, lets the corridor engine control
the signals ahead of the ambulance, estimates the ETA and caches a
snapshot that the WebSocket sends to the dashboard.

The world is reached only through the corridor connectors
(corridor/interfaces.py); the SUMO versions are created in
_create_connectors(). To run on real signals, replace that method.

Other threads (the MCP server's tools) must not touch the simulation
directly: SUMO can only be driven from one thread. They pass a function
to run_in_simulation(), which the loop runs between two steps.
"""

import logging
import os
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass

from ai.clearance import clearance_predictor
from ai.features import formula_travel_time, route_position
from ai.pretrip import TRAFFIC_LEVELS
from corridor import feed, priority
from corridor import dispatch, hospital_care
from corridor.hospital_care import HospitalBoard
from corridor.police_board import PoliceBoard
from corridor.referee import JunctionReferee
from backend.services import admin_service, priority_log
from backend.services.ambulance_run import AmbulanceRun
from backend.services.police_notifier import police_notifier
from simulation import live_traffic
from simulation.sumo import route_planner
from simulation.sumo.adapters import (
    SumoAmbulanceTracker,
    SumoIncidents,
    SumoResponder,
    SumoSignalController,
    SumoSimulation,
    SumoTrafficSource,
)
from simulation.sumo.sumo_bridge import (
    AMBULANCE_DEPART_TIME,
    DEMO_HOSPITAL_NAME,
    DEMO_START,
    DEMO_START_NAME,
    apply_city_speed_limits,
    demo_plan,
    sumo_command,
)

logger = logging.getLogger(__name__)

# Ambulances on the road at once (the first one plus those added).
MAX_AMBULANCES = 10

# Demo "crossing ambulance": it is sent towards a signal the first
# ambulance reaches in this many seconds (enough time to see it coming).
CROSSING_MIN_SECONDS = 45
CROSSING_MAX_SECONDS = 240

# Keep showing the finished trip this long (simulated seconds),
# then stop the simulation automatically.
STOP_AFTER_ARRIVAL_SECONDS = 30

# A returning 108 ambulance off the road this long without arriving is
# put back at its station (s).
UNIT_MISSING_SECONDS = 300

# Playback speed while the ambulance drives: simulated seconds per real
# second (1 = real time). Warm-up always runs at full speed.
DEFAULT_PLAYBACK_SPEED = 2
PLAYBACK_SPEEDS = (1, 2, 5, 10)

# How often live traffic (TomTom) is refreshed while running, real seconds.
LIVE_REFRESH_SECONDS = 600


@dataclass
class LiveContext:
    """What a function passed to run_in_simulation() can use. ambulance,
    engine, route_cache, response and police_watch are the first
    ambulance's; runs holds every ambulance (AmbulanceRun)."""

    now: float
    phase: str  # "warming_up", "driving" or "arrived"
    ambulance: object
    traffic: object
    signals: object
    engine: object  # corridor.engine.CorridorEngine
    route_cache: object  # ai.features.RouteCache, None before departure
    response: object = None  # corridor/response.py, None before departure
    police_watch: object = None  # corridor/police_watch.py, None before departure
    incidents: object = None     # simulated accidents (SumoIncidents)
    runs: list = None            # every ambulance (AmbulanceRun), first one first
    referee: object = None       # corridor/referee.py


class SimulationNotRunning(RuntimeError):
    pass


def demo_trip():
    """The demo trip: Shukrawar Peth to Ruby Hall Clinic (fastest route)."""
    plan = demo_plan()
    return {
        "roads": list(plan["roads"]),
        "depart_position": plan["depart_position"],
        "arrival_position": plan["arrival_position"],
        "start_name": DEMO_START_NAME,
        "hospital_name": DEMO_HOSPITAL_NAME,
        "start_point": list(DEMO_START),
        "stretches": plan["signalless_stretches"],
        "police_along_route": plan["police_along_route"],
        "hospital_point": next(
            [item["latitude"], item["longitude"]]
            for item in route_planner.hospitals()
            if item["name"] == DEMO_HOSPITAL_NAME
        ),
    }


def _state(status, **values):
    return {
        "status": status,
        "simulation_time": 0,
        "ambulance": None,
        "vehicles": [],
        "signals": [],
        "corridor": [],
        "route": [],
        "route_signals": [],
        "route_traffic": [],
        "response": None,
        "police_watch": None,
        "police_board": None,
        "incidents": [],
        "ambulances": [],
        "ambulance_details": {},
        "referee": None,
        "units": [],
        **values,
    }


class SimulationService:

    def __init__(self):
        self.running = False
        self.thread = None
        self.latest_state = _state("stopped")
        self._requests = queue.Queue()

        # Messages from the AI agent, shown on the dashboard.
        self.agent_feed = deque(maxlen=30)

        # Where the first ambulance goes, and its patient's condition
        # (set by start()).
        self.trip = demo_trip()
        self.condition = priority.DEFAULT_CONDITION
        self.extra = []  # booked ambulances [(trip, condition)]

        # Every ambulance of the current run (AmbulanceRun), first one
        # first; only changed inside the simulation thread.
        self.runs = []
        self._fleet = None  # what a new AmbulanceRun needs (set by _drive)
        self._run_id = None

        # Priority settings of this run, newest last (also saved in the
        # priority_changes table).
        self.priority_changes = deque(maxlen=100)

        # What each hospital can take in this run (cath lab, ICU beds,
        # trauma team...; corridor/hospital_care.py): each ambulance
        # holds what its patient needs.
        self.hospital_board = HospitalBoard()

        # Where each 108 ambulance is and what it is doing
        # (corridor/dispatch.py), during a run.
        self.units = dispatch.UnitBoard()
        self._return_trips = 0

        self.playback_speed = DEFAULT_PLAYBACK_SPEED

        # Live traffic (digital twin), when a TomTom key is configured.
        self.live_info = None
        self._pending_snapshot = None
        self._road_cache = {}
        self._live_preview_tried = 0.0

    # -------------------------------------------------------------
    # Controls used by the API
    # -------------------------------------------------------------

    def start(self, trip=None, condition=None, extra=()):
        """
        Start the simulation in a background thread. trip: {"roads",
        "depart_position", "arrival_position", "start_name",
        "hospital_name"} from the route planner; default: the demo trip.
        condition: the patient's condition set by the dispatcher
        (corridor/priority.py); default DEFAULT_CONDITION.
        extra: more booked ambulances [(trip, condition)], setting off
        together with the first one.
        """

        condition = priority.check_condition(condition or priority.DEFAULT_CONDITION)
        extra = [
            (extra_trip, priority.check_condition(extra_condition or priority.DEFAULT_CONDITION))
            for extra_trip, extra_condition in extra
        ]
        if 1 + len(extra) > MAX_AMBULANCES:
            raise ValueError(f"At most {MAX_AMBULANCES} ambulances at once in this demo.")

        if self.running:
            return {"status": "already_running"}

        # The previous run is still closing its simulation connection.
        if self.thread is not None and self.thread.is_alive():
            return {"status": "stopping"}

        self.trip = trip or demo_trip()
        self.condition = condition
        self.extra = extra
        self.runs = []
        self._fleet = None
        self.priority_changes.clear()
        self.hospital_board.reset()
        self.units.reset(dispatch.units(route_planner.hospitals()))
        self.live_info = None
        self._pending_snapshot = None
        self._road_cache = {}
        self.latest_state = _state("starting")
        self.agent_feed.clear()
        police_notifier.new_trip()
        self.running = True

        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

        return {"status": "starting"}

    def stop(self):
        """Ask the simulation to stop after the current step."""

        if not self.running:
            return {"status": "already_stopped"}

        self.running = False
        return {"status": "stopping"}

    def set_playback_speed(self, speed):
        """How many simulated seconds pass per real second (1 to 10)."""

        if speed not in PLAYBACK_SPEEDS:
            raise ValueError(f"Playback speed must be one of {PLAYBACK_SPEEDS}.")
        self.playback_speed = speed
        return {"playback_speed": speed}

    def get_state(self):
        """Latest snapshot for the dashboard."""

        # Show live traffic as soon as the dashboard opens (at most one
        # attempt per refresh period; TomTom answers come from a cache).
        if (
            self.live_info is None
            and not self.running
            and live_traffic.available()
            and time.time() - self._live_preview_tried >= LIVE_REFRESH_SECONDS
        ):
            self._live_preview_tried = time.time()
            threading.Thread(target=self._load_live_preview, daemon=True).start()

        return {
            **self.latest_state,
            "agent_feed": list(self.agent_feed),
            "trip": {
                "start_name": self.trip["start_name"],
                "hospital_name": self.trip["hospital_name"],
                "start_point": self.trip.get("start_point"),
                "hospital_point": self.trip.get("hospital_point"),
                "pickup_point": (
                    [self.trip["pickup"]["latitude"], self.trip["pickup"]["longitude"]]
                    if self.trip.get("pickup") else None
                ),
                "base_name": (self.trip.get("base") or {}).get("name"),
                "police_along_route": self.trip.get("police_along_route", []),
            },
            "live_traffic": self.live_info,
            "playback_speed": self.playback_speed,
            "priority_changes": list(self.priority_changes),
            "max_ambulances": MAX_AMBULANCES,
            "units": self.unit_summary(),
        }

    # -------------------------------------------------------------
    # Several ambulances and their patients' priority
    # -------------------------------------------------------------

    def add_ambulance(self, trip, condition=None):
        """
        Send another ambulance while the simulation runs. trip: like
        start()'s. During warm-up it sets off with the first ambulance,
        otherwise at once.
        """

        condition = priority.check_condition(condition or priority.DEFAULT_CONDITION)

        def act(context):
            if len(context.runs) >= MAX_AMBULANCES:
                raise ValueError(
                    f"At most {MAX_AMBULANCES} ambulances at once in this demo."
                )
            if context.phase == "arrived":
                raise ValueError(
                    "The trip has finished. Start a new simulation first."
                )
            dispatch_at = max(AMBULANCE_DEPART_TIME, context.now + 1)
            profiles = []
            for active in context.runs:
                if active.phase == "arrived":
                    continue
                remaining = dict(active.trip)
                offset = max(0, active.dispatch_at - dispatch_at)
                if active.phase == "driving":
                    index, position = route_position(active.ambulance)
                    remaining['roads'] = active.ambulance.route()[index:]
                    remaining['depart_position'] = position
                profile = route_planner.corridor_profile(remaining, offset)
                profiles.append({**profile, 'label': active.label,
                                 'weight': priority.weight(active.condition, active._stood_still())})
            coordinated, _ = route_planner.coordinate_trip(
                trip, condition, profiles, f"Ambulance {len(context.runs) + 1}"
            )
            run = self._new_run(coordinated, condition, dispatch_at, context.now)
            return run.summary()

        added = self.run_in_simulation(act)
        changed = (added.get("routing_decision") or {}).get("changed")
        self.add_agent_message(
            f"{added['label']} sent"
            + (f" ({added['level_name']})" if added.get("level_name") else "")
            + f": {trip['start_name']} → "
            f"{trip['hospital_name']}." + (" Took another road to avoid other ambulances." if changed else ""),
            kind="priority",
        )
        return added

    def add_crossing_ambulance(self, condition=None):
        """
        Demo: another ambulance that reaches one of the first ambulance's
        next signals from a side road at about the same time, so the two
        need the same junction and the referee has to decide.
        """

        condition = priority.check_condition(condition or "stable")

        def junctions_ahead(context):
            first = context.runs[0]
            if first.phase != "driving":
                raise ValueError(
                    "The crossing ambulance can be sent once the first "
                    "ambulance is driving."
                )
            route = first.ambulance.route()
            route_index, lane_position = route_position(first.ambulance)
            candidates = []
            for junction in first.engine.upcoming_signals():
                seconds = formula_travel_time(
                    first.route_cache, route_index, lane_position,
                    junction["route_index"],
                )
                if CROSSING_MIN_SECONDS <= seconds <= CROSSING_MAX_SECONDS:
                    candidates.append((
                        junction["signal_id"], route[junction["route_index"]],
                        seconds,
                    ))
            return candidates

        candidates = self.run_in_simulation(junctions_ahead)

        # Planned outside the simulation thread (takes about a second).
        for signal_id, in_road, seconds in candidates:
            trip = route_planner.crossing_trip(signal_id, in_road, seconds)
            if trip is not None:
                return self.add_ambulance(trip, condition)

        raise ValueError(
            "No side road found to cross the first ambulance's route in the "
            "next few minutes. Try again in a moment."
        )

    def set_condition(self, vehicle_id, condition, changed_by=None, source="crew"):
        """The crew sets their patient's condition: applies at once,
        logged in priority_changes. source "admin": the control room
        overrides it (also applied at once and logged)."""

        if source not in ("crew", "admin"):
            raise ValueError("source must be 'crew' or 'admin'.")

        condition = priority.check_condition(condition)

        def act(context):
            run = next(
                (run for run in context.runs if run.vehicle_id == vehicle_id),
                None,
            )
            if run is None:
                raise LookupError(f"No ambulance {vehicle_id} in this run.")
            if run.phase == "arrived":
                raise ValueError(f"{run.label} has already arrived.")
            previous = run.condition
            if previous == condition:
                return run.summary()
            run.set_condition(condition)
            self._log_priority(run, source, context.now, previous, changed_by)
            # Does the hospital still suit the patient? (e.g. chest pain
            # became a heart attack: it needs a cath lab now)
            if hospital_care.need(previous) != hospital_care.need(condition) or not (
                hospital_care.can_treat(run.trip["hospital_name"], condition)
            ):
                run.pending_check = "condition"
            return run.summary()

        before = {
            item["vehicle_id"]: item for item in self.latest_state.get("ambulances", [])
        }.get(vehicle_id)
        result = self.run_in_simulation(act)
        if before is None or before["condition"] != condition:
            who = "control room" if source == "admin" else "crew"
            self.add_agent_message(
                f"{result['label']}: patient is now {result['level_name']} "
                f"({result['condition_label'].lower()}). Changed by the {who}.",
                kind="priority",
            )
        return result

    def _new_run(self, trip, condition, dispatch_at, now):
        """Create an ambulance (inside the simulation thread)."""

        traffic, signals, referee, clearance, level_index = self._fleet
        run = AmbulanceRun(
            number=len(self.runs) + 1,
            trip=trip,
            condition=condition,
            dispatch_at=dispatch_at,
            traffic=traffic,
            signals=signals,
            referee=referee,
            clearance=clearance,
            level_index=level_index,
            on_police_alert=self._on_police_alert,
            add_message=self.add_agent_message,
            check_hospital=self._check_hospital,
        )
        self.runs.append(run)
        self.hospital_board.hold(run.vehicle_id, trip["hospital_name"], condition)
        self._assign_unit(run)
        self._log_priority(run, "dispatch", now)
        return run

    def busy_units(self):
        """108 ambulances that can't take a new call right now."""
        return self.units.busy() if self.running else set()

    def unit_fleet(self):
        """The 108 fleet for dispatch: during a run where each ambulance
        is now (a returning one can take a call from where it is),
        otherwise all at their stations."""
        if self.running and self.units.units:
            return self.units.fleet()
        return dispatch.units(route_planner.hospitals())

    def unit_summary(self):
        if self.running and self.units.units:
            return self.units.summary()
        return [
            {**unit, "status": "available", "ambulance": None,
             "kind_label": dispatch.KIND_LABELS[unit["kind"]],
             "status_label": dispatch.STATUS_LABELS["available"]}
            for unit in dispatch.units(route_planner.hospitals())
        ]

    def _assign_unit(self, run):
        """The 108 ambulance named in run's trip goes on this call (inside
        the simulation thread). A returning one leaves its way home."""
        unit = self.units.units.get((run.trip.get("unit") or {}).get("id"))
        if unit is None:
            return
        if unit["vehicle_id"]:
            try:
                import traci
                traci.vehicle.remove(unit["vehicle_id"])
            except Exception:
                pass
            self.add_agent_message(
                f"{unit['id']} was driving back. It now goes to {run.label}'s patient.",
                kind="hospital",
            )
        unit.update(status="on_call", ambulance=run.vehicle_id,
                    vehicle_id=None, missing_since=None)

    def _step_units(self, now):
        """Follow the 108 ambulances after their patients: handover at the
        hospital, then back in service, driving back to their station."""
        for run in self.runs:
            unit = self.units.units.get((run.trip.get("unit") or {}).get("id"))
            if unit is None or unit["ambulance"] != run.vehicle_id:
                continue
            if run.phase == "driving" and run.snapshot:
                unit["latitude"] = run.snapshot.get("latitude", unit["latitude"])
                unit["longitude"] = run.snapshot.get("longitude", unit["longitude"])
            elif run.phase == "arrived":
                if unit["status"] == "on_call":
                    unit["status"] = "handover"
                    unit["latitude"], unit["longitude"] = run.trip["hospital_point"]
                if unit["status"] == "handover" and now >= run.handover_done_at:
                    self._send_home(unit, run)

        for unit in self.units.units.values():
            if unit["status"] != "returning" or not unit["vehicle_id"]:
                continue
            tracker = SumoAmbulanceTracker(unit["vehicle_id"])
            if tracker.is_on_road():
                unit["missing_since"] = None
                position = tracker.position()
                unit["latitude"], unit["longitude"] = position["latitude"], position["longitude"]
            elif tracker.has_arrived() or now - (unit["missing_since"] or now) > UNIT_MISSING_SECONDS:
                self._unit_home(unit)
            elif unit["missing_since"] is None:
                unit["missing_since"] = now  # teleported past a jam

    def _send_home(self, unit, run):
        """Handover done: the ambulance is back in service and drives to
        its station (free for a new call on the way)."""
        run.service_stage = "returning"
        latitude, longitude = run.trip["hospital_point"]
        try:
            plan = route_planner.plan_route(
                latitude, longitude, unit["station_latitude"], unit["station_longitude"],
                unit["station"],
            )
        except route_planner.PlanningError:
            plan = None
        if plan is None or len(plan["roads"]) < 2:
            # Its station is this hospital (or no way back): back at once.
            self._unit_home(unit)
            return
        self._return_trips += 1
        vehicle_id = f"ambulance_return_{self._return_trips}"
        try:
            SumoAmbulanceTracker(vehicle_id).dispatch(
                plan["roads"], plan["depart_position"], plan["arrival_position"]
            )
        except Exception:
            logger.exception("Could not send %s back to its station", unit["id"])
            self._unit_home(unit)
            return
        unit.update(status="returning", vehicle_id=vehicle_id, missing_since=None)
        self.add_agent_message(
            f"{unit['id']} left the patient at {run.trip['hospital_name']}. "
            f"Free again, driving back to {unit['station']}.",
            kind="hospital",
        )

    def _unit_home(self, unit):
        run = next((item for item in self.runs if item.vehicle_id == unit["ambulance"]), None)
        if run is not None:
            run.service_stage = "available"
        was_returning = unit["status"] == "returning"
        self.units.at_station(unit)
        if was_returning:
            self.add_agent_message(f"{unit['id']} back at {unit['station']}.", kind="hospital")

    def _fleet_busy(self, now):
        """A handover or a drive back to the station still going on."""
        return any(
            run.handover_done_at is not None and now < run.handover_done_at
            for run in self.runs
        ) or any(unit["status"] in ("handover", "returning") for unit in self.units.units.values())

    # One manual police call per ambulance at most this often (s).
    POLICE_CALL_COOLDOWN = 120

    def call_police(self, vehicle_id, called_by="crew"):
        """The crew (or the control room) says the ambulance is stuck:
        call the police station that can reach the front of the queue
        fastest, now. Only while driving, at most once every
        POLICE_CALL_COOLDOWN seconds per ambulance; the phone call itself
        counts towards the per-trip call limit (police_notifier)."""

        def act(context):
            run = next((item for item in context.runs if item.vehicle_id == vehicle_id), None)
            if run is None:
                raise LookupError(f"No ambulance {vehicle_id} in this run.")
            if run.phase != "driving" or run.police_watch is None:
                raise ValueError(f"{run.label} is not driving, so there is nothing to clear.")
            if run.at_patient:
                raise ValueError(f"{run.label} is at the patient, not stuck.")
            last = getattr(run, "police_called_at", None)
            if last is not None and context.now - last < self.POLICE_CALL_COOLDOWN:
                wait = round(self.POLICE_CALL_COOLDOWN - (context.now - last))
                raise ValueError(f"Police were just called for {run.label}. Try again in {wait} s.")
            result = run.police_watch.call_police(context.now, called_by)
            if result["called"]:
                run.police_called_at = context.now
                snapshot = run.snapshot or {}
                run.record_event(
                    context.now, "POLICE", f"Police called by the {called_by}",
                    detail=result["message"],
                    latitude=snapshot.get("latitude"), longitude=snapshot.get("longitude"),
                )
            return {**result, "label": run.label}

        result = self.run_in_simulation(act)
        if result["called"]:
            who = "control room" if called_by == "control room" else "crew"
            self.add_agent_message(f"{result['label']} ({who}): {result['message']}", kind="police")
        return result

    def hospital_declines(self, vehicle_id):
        """Test tool: the hospital this ambulance is going to can't take
        its patient any more (e.g. its cath lab just became busy). The
        call centre finds another one and the ambulance is diverted."""

        def act(context):
            run = next((item for item in context.runs if item.vehicle_id == vehicle_id), None)
            if run is None:
                raise LookupError(f"No ambulance {vehicle_id} in this run.")
            if run.phase == "arrived":
                raise ValueError(f"{run.label} has already arrived.")
            self.hospital_board.close(
                run.trip["hospital_name"], hospital_care.need(run.condition)
            )
            run.pending_check = "declined"
            return run.summary()

        return self.run_in_simulation(act)

    # Hospitals compared by live driving time when diverting.
    DIVERT_CANDIDATES = 4

    def _check_hospital(self, run, now, why):
        """
        The 108 call centre checks the ambulance's hospital can take the
        patient: on the pre-alert (why="pre_alert", once the patient is
        on board), when the crew changes the condition ("condition"), or
        when the hospital says it can't ("declined"). If it can't, the
        nearest hospital that can (by live driving time) is found and
        the ambulance diverted at once, instead of finding out at the door.
        """

        board = self.hospital_board
        name = run.trip["hospital_name"]
        resource = hospital_care.need(run.condition)
        need_label = hospital_care.RESOURCES[resource]
        if board.accepts(name, run.condition, holder=run.vehicle_id):
            board.hold(run.vehicle_id, name, run.condition)
            if why == "pre_alert" or run.pre_alert is None or (
                run.pre_alert["hospital"], run.pre_alert["need_label"]
            ) != (name, need_label):
                run.pre_alert = {"hospital": name, "need_label": need_label, "status": "ready"}
                eta = (run.snapshot or {}).get("eta_seconds")
                self.add_agent_message(
                    f"Told {name} that {run.label} is coming"
                    + (f" (about {max(1, round(eta / 60))} min)" if eta else "")
                    + f". {need_label} is ready.",
                    kind="hospital",
                )
                run.record_event(
                    now, "PRE_ALERT", f"Hospital pre-alerted: {name}",
                    detail=f"{need_label} ready on arrival",
                )
            return

        if not hospital_care.can_treat(name, run.condition):
            problem = f"can't treat {priority.CONDITIONS[run.condition][0].lower()}"
        else:
            problem = f"has no {need_label.lower()} free"

        if run.phase != "driving" or run.on_junction():
            run.pending_check = why  # try again once on a normal road
            return

        position = run.snapshot or {}
        latitude, longitude = position.get("latitude"), position.get("longitude")
        candidates = [
            item for item in route_planner.hospitals()
            if item["name"] != name
            and board.accepts(item["name"], run.condition, holder=run.vehicle_id)
        ]
        if latitude is not None:
            candidates.sort(key=lambda item: (item["latitude"] - latitude) ** 2
                            + (item["longitude"] - longitude) ** 2)
        best = None
        for item in candidates[:self.DIVERT_CANDIDATES]:
            plan = run.divert_route(item["latitude"], item["longitude"])
            if plan and (best is None or plan["seconds"] < best[1]["seconds"]):
                best = (item, plan)

        run.pre_alert = {"hospital": name, "need_label": need_label, "status": "declined"}
        refused = problem.startswith("has no")  # full, not "can't treat"
        if refused:
            admin_service.auto_grievance(
                "HOSPITAL",
                f"{name} could not take {run.label}'s patient",
                description=(
                    f"Pre-alert: no {need_label.lower()} free. "
                    + ("No other hospital could take the patient either."
                       if best is None else f"Sent to {best[0]['name']} instead.")
                ),
                request_id=run.trip_request_id,
                priority="HIGH" if best is None else "MEDIUM",
            )
        if best is None:
            self.add_agent_message(
                f"{name} {problem}. No other hospital can take the patient, "
                f"so {run.label} keeps going.",
                kind="warning",
            )
            return

        hospital, plan = best
        reason = f"{name} {problem}"
        if not run.divert(hospital, plan, now, reason):
            run.pending_check = why
            return
        board.hold(run.vehicle_id, hospital["name"], run.condition)
        run.pre_alert = {"hospital": hospital["name"], "need_label": need_label, "status": "ready"}
        extra = round((plan["seconds"] - plan["old_seconds"]) / 60)
        change = (f"{extra} min longer" if extra > 0
                  else f"{-extra} min shorter" if extra < 0 else "same time")
        self.add_agent_message(
            f"{reason}. {run.label} now goes to {hospital['name']} ({change}). "
            "They are ready.",
            kind="hospital",
        )

    def _log_priority(self, run, source, now, previous=None, changed_by=None):
        entry = {
            "trip_id": self._run_id,
            "ambulance_id": run.vehicle_id,
            "ambulance_label": run.label,
            **priority.describe(run.condition),
            "previous_condition": previous,
            "source": source,
            "changed_by": changed_by,
            "simulation_time": now,
        }
        self.priority_changes.append(entry)
        priority_log.record(entry)

    def _on_police_alert(self, alert):
        """New police alert or status change (corridor/police_watch.py):
        phone the station on a new alert, save every change in the
        database (police_calls), and tell the dashboard."""

        if alert["status"] == "ALERTED":
            police_notifier.alert(alert)  # phones the station, saves the alert
            self.add_agent_message(
                f"Called {alert['station']} to clear {alert['road']}. "
                f"Ambulance is about {max(1, round(alert['ambulance_eta_seconds'] / 60))} min away.",
                kind="police",
            )
            return

        police_notifier.record(alert)  # the new status, in police_calls
        if alert["status"] in ("PASSED", "CANCELLED"):
            self.add_agent_message(
                f"Police done at {alert['road']}: {alert['closed_reason']}.",
                kind="police",
            )

    @staticmethod
    def _police_summary(police_watch):
        """The watch's state, with each alert's phone call status."""
        if police_watch is None:
            return None
        summary = police_watch.summary()
        for alert in summary["alerts"]:
            alert["call"] = police_notifier.call_status(alert["alert_id"])
        return summary

    def _ambulance_detail(self, run, traffic, signals):
        """Live map and chase data for one independently routed ambulance."""
        return {
            "snapshot": run.snapshot,
            "route_signals": run.engine.route_signals,
            "signals": feed.signal_states(signals, run.upcoming),
            "corridor": feed.corridor_entries(
                run.upcoming, signals,
                (run.snapshot or {}).get("speed", 0), run.engine,
            ),
            "route_traffic": (
                feed.route_traffic_segments(run.route_cache, traffic, run.road_shapes)
                if run.phase == "driving" else []
            ),
            "response": run.response.summary() if run.response else None,
            "police_watch": self._police_summary(run.police_watch),
            "police_along_route": run.trip.get("police_along_route", []),
        }

    def create_incident(self):
        """
        "Simulate accident": block every lane of a road without signals
        ahead of the ambulance (at least 300 m ahead), so the police
        alert, the phone call and the police clearing it can be shown.
        """

        def act(context):
            if context.phase != "driving" or context.police_watch is None:
                raise ValueError(
                    "An accident can only be simulated while the ambulance is driving."
                )
            place = context.police_watch.incident_road()
            if place is None:
                raise ValueError(
                    "No road without signals far enough ahead of the ambulance."
                )
            road_id, stretch_name, in_time = place
            incident = context.incidents.block_road(road_id)
            if incident is None:
                raise ValueError("Could not block that road.")
            # Reported at once, like a real accident report: the police
            # are alerted now, not once the queue behind it has built up.
            context.police_watch.report_incident(road_id, context.now)
            road = route_planner.road_name(road_id) or stretch_name
            context.runs[0].record_event(
                context.now, "ACCIDENT", f"Accident on {road}",
                detail="Test crash: the road is blocked",
                road_name=road,
                latitude=incident.get("latitude"),
                longitude=incident.get("longitude"),
            )
            return {
                **incident,
                "road": route_planner.road_name(road_id) or stretch_name,
                "police_in_time": in_time,
            }

        incident = self.run_in_simulation(act)
        self.add_agent_message(
            f"Test accident on {incident['road']}. Road blocked, police called."
            + ("" if incident["police_in_time"] else " They may arrive late."),
            kind="warning",
        )
        return incident

    def add_agent_message(self, text, kind="note"):
        """Record a message from the AI agent for the dashboard."""

        message = {
            "simulation_time": self.latest_state.get("simulation_time"),
            "kind": kind,
            "text": text,
        }
        self.agent_feed.append(message)
        return message

    def run_in_simulation(self, function, timeout=10):
        """
        Run function(context: LiveContext) inside the simulation loop,
        between two steps, and return its result. Safe to call from any
        thread; waits at most `timeout` seconds.
        """

        if not self.running:
            raise SimulationNotRunning(
                "The simulation is not running. Start it first."
            )

        future = Future()
        self._requests.put((function, future))
        return future.result(timeout=timeout)

    def _answer_requests(self, context):
        while True:
            try:
                function, future = self._requests.get_nowait()
            except queue.Empty:
                return

            if not future.set_running_or_notify_cancel():
                continue

            try:
                future.set_result(function(context))
            except Exception as error:
                future.set_exception(error)

    def _fail_pending_requests(self):
        while True:
            try:
                _, future = self._requests.get_nowait()
            except queue.Empty:
                return
            if future.set_running_or_notify_cancel():
                future.set_exception(
                    SimulationNotRunning("The simulation stopped.")
                )

    # -------------------------------------------------------------
    # Simulation loop
    # -------------------------------------------------------------

    def _create_connectors(self, traffic_level="normal"):
        """SUMO versions of the simulation clock and the connectors
        (each ambulance gets its own tracker: AmbulanceRun)."""

        simulation = SumoSimulation()
        simulation.start(sumo_command(traffic_level))
        apply_city_speed_limits()

        return simulation, SumoTrafficSource(), SumoSignalController()

    def _live_snapshot(self):
        """Live traffic along the trip and on main roads (TomTom)."""

        geometry = route_planner.roads_geometry(self.trip["roads"])
        snapshot = live_traffic.fetch_snapshot(
            live_traffic.probe_points(geometry)
        )
        car_minutes = live_traffic.car_travel_minutes(geometry[0], geometry[-1])
        return snapshot, car_minutes

    def _load_live_preview(self):
        """Live traffic for the map before any trip has started."""
        try:
            snapshot, car_minutes = self._live_snapshot()
            if not self.running and self.live_info is None:
                self.live_info = live_traffic.dashboard_summary(
                    snapshot, car_minutes
                )
        except Exception:
            logger.exception("Could not load live traffic for the map")

    def _refresh_live(self):
        """Background refresh; applied by the simulation loop."""
        try:
            self._pending_snapshot = self._live_snapshot()
        except Exception:
            logger.exception("Live traffic refresh failed")

    def _run(self):
        simulation = None

        try:
            snapshot = None
            traffic_level = "normal"

            if live_traffic.available():
                self.latest_state = _state(
                    "starting",
                    message="Fetching live Pune traffic from TomTom…",
                )
                try:
                    snapshot, car_minutes = self._live_snapshot()
                    traffic_level = snapshot["level"]
                    self.live_info = live_traffic.dashboard_summary(
                        snapshot, car_minutes
                    )
                except Exception:
                    logger.exception(
                        "Could not fetch live traffic; using normal traffic."
                    )

            # For testing / demos: force a traffic level (light, normal or
            # heavy) with the SIM_TRAFFIC_LEVEL environment variable.
            forced = os.environ.get("SIM_TRAFFIC_LEVEL")
            if forced in ("light", "normal", "heavy"):
                traffic_level = forced

            simulation, traffic, signals = self._create_connectors(traffic_level)
            logger.info("Simulation started (%s traffic).", traffic_level)
            self._traffic_level = traffic_level

            if snapshot is not None:
                roads = live_traffic.apply_to_simulation(
                    snapshot, traffic, self._road_cache
                )
                logger.info("Live speeds applied to %d roads.", roads)

            self._drive(simulation, traffic, signals)

        except Exception as error:
            logger.exception("Simulation error")
            for run in self.runs:
                run.end_record("FAILED", f"Simulation error: {error}", "Simulation error")
            self.latest_state = _state(
                "error",
                error=str(error),
                route=self.latest_state.get("route", []),
                route_signals=self.latest_state.get("route_signals", []),
            )

        finally:
            self.running = False
            self._fail_pending_requests()

            # Ambulances still on the way when the simulation stopped.
            for run in self.runs:
                run.end_record(
                    "CANCELLED",
                    "Simulation stopped before the ambulance arrived.",
                    "Stopped before arrival",
                )

            if simulation is not None:
                try:
                    simulation.close()
                except Exception:
                    pass

            # The next run finds the hospitals busy with other patients.
            self.hospital_board.reshuffle()

            # Keep the last snapshot, marked as stopped (unless error).
            if self.latest_state.get("status") != "error":
                self.latest_state = {**self.latest_state, "status": "stopped"}

            logger.info("Simulation stopped.")

    def _drive(self, simulation, traffic, signals):
        level_index = TRAFFIC_LEVELS.index(
            getattr(self, "_traffic_level", "normal")
        )

        # One referee for every ambulance's corridor (corridor/referee.py);
        # the AI clearance model decides how early each signal switches.
        referee = JunctionReferee(
            notify=lambda text: self.add_agent_message(text, kind="referee")
        )
        self._fleet = (
            traffic, signals, referee,
            clearance_predictor(traffic, signals), level_index,
        )
        self._run_id = f"run{int(time.time())}"
        first = self._new_run(
            self.trip, self.condition, AMBULANCE_DEPART_TIME, simulation.time()
        )
        # Booked ambulances set off together with the first one.
        for extra_trip, extra_condition in self.extra:
            self._new_run(
                extra_trip, extra_condition, AMBULANCE_DEPART_TIME, simulation.time()
            )

        # What each police station is doing (corridor/police_board.py),
        # and simulated accidents (the dashboard's button).
        police_board = None
        incidents = SumoIncidents()
        quiet_since = None  # every ambulance done (for the auto-stop)
        last_live_fetch = time.time()

        while self.running and simulation.expected_vehicles() > 0:
            step_started = time.time()
            simulation.step()
            now = simulation.time()

            # Live traffic: refresh in the background, apply here.
            if self.live_info is not None:
                if time.time() - last_live_fetch >= LIVE_REFRESH_SECONDS:
                    last_live_fetch = time.time()
                    threading.Thread(
                        target=self._refresh_live, daemon=True
                    ).start()

                if self._pending_snapshot is not None:
                    snapshot, car_minutes = self._pending_snapshot
                    self._pending_snapshot = None
                    live_traffic.apply_to_simulation(
                        snapshot, traffic, self._road_cache
                    )
                    self.live_info = live_traffic.dashboard_summary(
                        snapshot, car_minutes
                    )

            phase = {
                "waiting": "warming_up", "driving": "driving", "arrived": "arrived",
            }[first.phase]

            self._answer_requests(LiveContext(
                now=now,
                phase=phase,
                ambulance=first.ambulance,
                traffic=traffic,
                signals=signals,
                engine=first.engine,
                route_cache=first.route_cache,
                response=first.response,
                police_watch=first.police_watch,
                incidents=incidents,
                runs=list(self.runs),
                referee=referee,
            ))

            for run in list(self.runs):
                run.step(now)
            self._step_units(now)

            # Police never hold back a road any ambulance still needs.
            SumoResponder.protect_roads(
                road for run in self.runs if run.phase == "driving"
                for road in run.remaining_roads()
            )

            # Warm-up: traffic builds up before the ambulance departs.
            # Run at full speed and only report progress.
            if first.phase == "waiting":
                if int(now) % 10 == 0:
                    self.latest_state = _state(
                        "warming_up",
                        simulation_time=now,
                        warmup_seconds=AMBULANCE_DEPART_TIME,
                        vehicle_count=traffic.vehicle_count(),
                        ambulances=[run.summary() for run in self.runs],
                    )
                continue

            # Every ambulance has arrived, handed over its patient and (108)
            # is back at its station: show the result, then stop.
            if all(run.phase == "arrived" for run in self.runs) and not self._fleet_busy(now):
                quiet_since = quiet_since if quiet_since is not None else now
                if now - quiet_since > STOP_AFTER_ARRIVAL_SECONDS:
                    logger.info("Trip finished. Stopping simulation.")
                    break
            else:
                quiet_since = None

            if first.phase == "driving":
                if police_board is None:
                    police_board = PoliceBoard(
                        route_planner.police_stations(), first.ambulance,
                        SumoResponder(),
                    )
                police_board.update(
                    now, first.police_watch.alerts.values(),
                    first.response.police,
                )

            self.latest_state = _state(
                "running",
                simulation_time=now,
                ambulance=first.snapshot,
                vehicles=traffic.vehicles(
                    exclude={run.vehicle_id for run in self.runs} | {
                        unit["vehicle_id"] for unit in self.units.units.values()
                        if unit["vehicle_id"]
                    }
                ),
                signals=feed.signal_states(signals, first.upcoming),
                corridor=feed.corridor_entries(
                    first.upcoming, signals,
                    (first.snapshot or {}).get("speed", 0), first.engine,
                ),
                route=first.route_geometry,
                route_signals=first.engine.route_signals,
                # Red / yellow / green on the route ahead (not after arrival).
                route_traffic=(
                    feed.route_traffic_segments(
                        first.route_cache, traffic, first.road_shapes
                    )
                    if first.phase == "driving"
                    else []
                ),
                response=first.response.summary() if first.response else None,
                police_watch=self._police_summary(first.police_watch),
                police_board=police_board.summary() if police_board else None,
                incidents=incidents.summary(),
                ambulances=[run.summary() for run in self.runs],
                ambulance_details={
                    run.vehicle_id: self._ambulance_detail(run, traffic, signals)
                    for run in self.runs[1:]
                },
                referee=referee.summary(),
            )

            # One simulated second takes 1 / playback_speed real seconds.
            time.sleep(
                max(0.0, 1.0 / self.playback_speed - (time.time() - step_started))
            )


simulation_service = SimulationService()
