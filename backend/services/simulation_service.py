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

from ai import deadlock
from ai.clearance import clearance_predictor
from ai.pretrip import TRAFFIC_LEVELS
from ai.eta_model import estimate as estimate_eta
from ai.features import RouteCache
from corridor import feed
from corridor.engine import CorridorEngine
from corridor.police_board import PoliceBoard
from corridor.police_watch import SignallessWatch
from corridor.response import DeadlockResponse
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

AMBULANCE_ID = "ambulance_01"

# Keep showing the finished trip this long (simulated seconds),
# then stop the simulation automatically.
STOP_AFTER_ARRIVAL_SECONDS = 30

# Playback speed while the ambulance drives: simulated seconds per real
# second (1 = real time). Warm-up always runs at full speed.
DEFAULT_PLAYBACK_SPEED = 2
PLAYBACK_SPEEDS = (1, 2, 5, 10)

# How often live traffic (TomTom) is refreshed while running, real seconds.
LIVE_REFRESH_SECONDS = 600


@dataclass
class LiveContext:
    """What a function passed to run_in_simulation() can use."""

    now: float
    phase: str  # "warming_up", "driving" or "arrived"
    ambulance: object
    traffic: object
    signals: object
    engine: CorridorEngine
    route_cache: object  # ai.features.RouteCache, None before departure
    response: object = None  # corridor/response.py, None before departure
    police_watch: object = None  # corridor/police_watch.py, None before departure
    incidents: object = None     # simulated accidents (SumoIncidents)


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

        # Where the ambulance goes (set by start()).
        self.trip = demo_trip()

        self.playback_speed = DEFAULT_PLAYBACK_SPEED

        # Live traffic (digital twin), when a TomTom key is configured.
        self.live_info = None
        self._pending_snapshot = None
        self._road_cache = {}
        self._live_preview_tried = 0.0

    # -------------------------------------------------------------
    # Controls used by the API
    # -------------------------------------------------------------

    def start(self, trip=None):
        """
        Start the simulation in a background thread. trip: {"roads",
        "depart_position", "arrival_position", "start_name",
        "hospital_name"} from the route planner; default: the demo trip.
        """

        if self.running:
            return {"status": "already_running"}

        # The previous run is still closing its simulation connection.
        if self.thread is not None and self.thread.is_alive():
            return {"status": "stopping"}

        self.trip = trip or demo_trip()
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
                "police_along_route": self.trip.get("police_along_route", []),
            },
            "live_traffic": self.live_info,
            "playback_speed": self.playback_speed,
        }

    def _on_police_alert(self, alert):
        """New police alert or status change (corridor/police_watch.py):
        phone the station on a new alert, save every change in the
        database (police_calls), and tell the dashboard."""

        if alert["status"] == "ALERTED":
            police_notifier.alert(alert)  # phones the station, saves the alert
            self.add_agent_message(
                f"Police alert → {alert['station']}: clear {alert['road']} "
                f"(ambulance in ~{max(1, round(alert['ambulance_eta_seconds'] / 60))} min).",
                kind="police",
            )
            return

        police_notifier.record(alert)  # the new status, in police_calls
        if alert["status"] in ("PASSED", "CANCELLED"):
            self.add_agent_message(
                f"Police alert for {alert['road']} closed: {alert['closed_reason']}.",
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
            return {
                **incident,
                "road": route_planner.road_name(road_id) or stretch_name,
                "police_in_time": in_time,
            }

        incident = self.run_in_simulation(act)
        self.add_agent_message(
            f"Accident reported on {incident['road']} (simulated): the road is "
            "blocked ahead of the ambulance. Police are being alerted now."
            + ("" if incident["police_in_time"] else
               " It is close: the police may not clear it before the ambulance arrives."),
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
        """SUMO versions of the simulation clock and the connectors."""

        simulation = SumoSimulation()
        simulation.start(sumo_command(traffic_level))
        apply_city_speed_limits()

        return (
            simulation,
            SumoAmbulanceTracker(AMBULANCE_ID),
            SumoTrafficSource(),
            SumoSignalController(),
        )

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

            simulation, ambulance, traffic, signals = (
                self._create_connectors(traffic_level)
            )
            logger.info("Simulation started (%s traffic).", traffic_level)
            self._traffic_level = traffic_level

            if snapshot is not None:
                roads = live_traffic.apply_to_simulation(
                    snapshot, traffic, self._road_cache
                )
                logger.info("Live speeds applied to %d roads.", roads)

            self._drive(simulation, ambulance, traffic, signals)

        except Exception as error:
            logger.exception("Simulation error")
            self.latest_state = _state(
                "error",
                error=str(error),
                route=self.latest_state.get("route", []),
                route_signals=self.latest_state.get("route_signals", []),
            )

        finally:
            self.running = False
            self._fail_pending_requests()

            if simulation is not None:
                try:
                    simulation.close()
                except Exception:
                    pass

            # Keep the last snapshot, marked as stopped (unless error).
            if self.latest_state.get("status") != "error":
                self.latest_state = {**self.latest_state, "status": "stopped"}

            logger.info("Simulation stopped.")

    def _drive(self, simulation, ambulance, traffic, signals):
        # The AI clearance model decides how early each signal switches.
        engine = CorridorEngine(
            ambulance, traffic, signals,
            clearance_predictor=clearance_predictor(traffic, signals),
        )

        route_cache = None
        route_geometry = []
        road_shapes = {}

        # Deadlock watch: re-route or send police (corridor/response.py).
        response = None

        # Police for the stretches without signals (corridor/police_watch.py).
        police_watch = None

        # What each police station is doing (corridor/police_board.py),
        # and simulated accidents (the dashboard's button).
        police_board = None
        incidents = SumoIncidents()
        level_index = TRAFFIC_LEVELS.index(
            getattr(self, "_traffic_level", "normal")
        )
        events_shown = 0
        depart_time = None
        arrival_time = None
        last_snapshot = None
        stops = 0
        was_moving = False
        dispatched = False
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

            if route_cache is None:
                phase = "warming_up"
            elif arrival_time is None:
                phase = "driving"
            else:
                phase = "arrived"

            self._answer_requests(LiveContext(
                now=now,
                phase=phase,
                ambulance=ambulance,
                traffic=traffic,
                signals=signals,
                engine=engine,
                route_cache=route_cache,
                response=response,
                police_watch=police_watch,
                incidents=incidents,
            ))

            # Send the ambulance off once the roads have filled up.
            if not dispatched and now >= AMBULANCE_DEPART_TIME:
                ambulance.dispatch(
                    self.trip["roads"],
                    self.trip["depart_position"],
                    self.trip["arrival_position"],
                )
                dispatched = True
                logger.info(
                    "Ambulance dispatched: %s to %s.",
                    self.trip["start_name"], self.trip["hospital_name"],
                )

            # Warm-up: traffic builds up before the ambulance departs.
            # Run at full speed and only report progress.
            if route_cache is None and not ambulance.is_on_road():
                if int(now) % 10 == 0:
                    self.latest_state = _state(
                        "warming_up",
                        simulation_time=now,
                        warmup_seconds=AMBULANCE_DEPART_TIME,
                        vehicle_count=traffic.vehicle_count(),
                    )
                continue

            if (
                arrival_time is not None
                and now - arrival_time > STOP_AFTER_ARRIVAL_SECONDS
            ):
                logger.info("Trip finished. Stopping simulation.")
                break

            upcoming = []

            if ambulance.is_on_road():
                if route_cache is None:
                    route_cache = RouteCache(ambulance, traffic)
                    route_geometry = feed.route_geometry(ambulance, traffic)
                    engine.build_route_signals()
                    depart_time = ambulance.departure_time()

                    response = DeadlockResponse(
                        ambulance, traffic, SumoResponder(),
                        route_planner.police_stations(),
                        route_ahead=lambda info: deadlock.route_ahead(
                            info, traffic, ambulance, level_index
                        ),
                        predict=deadlock.predict,
                        traffic_level=level_index,
                        road_name=route_planner.road_name,
                        route_seconds=lambda roads: (
                            route_planner.estimate_route_seconds(
                                roads, level_index
                            )
                        ),
                        busy_roads=lambda: (
                            police_watch.active_roads() if police_watch else set()
                        ),
                    )
                    response.set_route(
                        deadlock.route_info(ambulance.route(), traffic)
                    )

                    police_watch = SignallessWatch(
                        ambulance, traffic, self.trip.get("stretches", []),
                        notify=self._on_police_alert,
                        responder=SumoResponder(),
                        trip_id=f"trip{int(now)}",
                        busy_roads=response.police_roads,
                        road_name=route_planner.road_name,
                    )
                    police_board = PoliceBoard(
                        route_planner.police_stations(), ambulance, SumoResponder()
                    )

                snapshot = feed.ambulance_snapshot(ambulance)
                snapshot.update(estimate_eta(route_cache, engine.route_signals))
                snapshot["trip_time_seconds"] = round(now - depart_time, 1)
                snapshot["depart_time"] = depart_time

                # A stop = slowing below walking pace after moving.
                moving = snapshot["speed"] > 0.5
                if was_moving and not moving:
                    stops += 1
                was_moving = moving
                snapshot["stops"] = stops
                last_snapshot = snapshot

                upcoming = engine.upcoming_signals()
                # The AI's predicted arrival at the junction ahead decides
                # when that signal switches (corridor/engine.py).
                seconds_to_next = (
                    snapshot.get("next_signal_eta_seconds")
                    if upcoming and snapshot.get("next_signal_route_index")
                    == upcoming[0]["route_index"]
                    else None
                )
                engine.apply(upcoming, now, seconds_to_next)

                if response.step(now) == "rerouted":
                    # New route: signals, route data and map line anew.
                    engine.reset()
                    engine.route_signals = []
                    engine.build_route_signals()
                    route_cache = RouteCache(ambulance, traffic)
                    route_geometry = feed.route_geometry(ambulance, traffic)
                    road_shapes.clear()
                    response.set_route(
                        deadlock.route_info(ambulance.route(), traffic)
                    )
                    # New route, new stretches without signals.
                    police_watch.close(now)
                    police_watch = SignallessWatch(
                        ambulance, traffic,
                        route_planner.route_police_plan(ambulance.route())[0],
                        notify=self._on_police_alert,
                        responder=SumoResponder(),
                        trip_id=f"trip{int(now)}r",
                        busy_roads=response.police_roads,
                        road_name=route_planner.road_name,
                    )

                police_watch.step(now)
                police_board.update(
                    now, police_watch.alerts.values(), response.police
                )

                # Show the response's decisions in the dashboard feed.
                for message in list(response.events)[events_shown:]:
                    self.add_agent_message(message, kind="response")
                events_shown = len(response.events)

            else:
                if arrival_time is None:
                    logger.info("Ambulance reached the hospital.")
                    engine.reset()
                    if response:
                        response.close()
                    if police_watch:
                        police_watch.close(now)
                    arrival_time = now

                snapshot = {
                    **last_snapshot,
                    "status": "COMPLETED",
                    "distance_left_meters": 0.0,
                    "eta_seconds": 0.0,
                    "trip_time_seconds": round(arrival_time - depart_time, 1),
                }

            self.latest_state = _state(
                "running",
                simulation_time=now,
                ambulance=snapshot,
                vehicles=traffic.vehicles(exclude=AMBULANCE_ID),
                signals=feed.signal_states(signals, upcoming),
                corridor=feed.corridor_entries(
                    upcoming, signals, snapshot.get("speed", 0), engine
                ),
                route=route_geometry,
                route_signals=engine.route_signals,
                # Red / yellow / green on the route ahead (not after arrival).
                route_traffic=(
                    feed.route_traffic_segments(route_cache, traffic, road_shapes)
                    if arrival_time is None
                    else []
                ),
                response=response.summary() if response else None,
                police_watch=self._police_summary(police_watch),
                police_board=police_board.summary() if police_board else None,
                incidents=incidents.summary(),
            )

            # One simulated second takes 1 / playback_speed real seconds.
            time.sleep(
                max(0.0, 1.0 / self.playback_speed - (time.time() - step_started))
            )


simulation_service = SimulationService()
