"""
One ambulance's trip in the live simulation.

Each ambulance has its own corridor engine, ETA, deadlock response and
police watch. The engines share one junction referee
(corridor/referee.py), which decides who gets a signal when two
ambulances need it at the same time.

Each trip is also recorded for the admin dashboard
(backend/services/admin_service.py): a request row when it sets off,
and when it ends its path, stops, signals, police, re-routes and
give-ways (backend/services/trip_recorder.py).

Runs inside the simulation thread (backend/services/simulation_service.py).
"""

import logging
import os

from ai import deadlock, pretrip
from ai.eta_model import estimate as estimate_eta
from ai.features import RouteCache
from ai.pretrip import TRAFFIC_LEVELS
from backend.services import admin_service
from backend.services.trip_recorder import TripRecorder
from corridor import feed, priority
from corridor.engine import CorridorEngine
from corridor.police_watch import SignallessWatch
from corridor.response import DeadlockResponse
from simulation.sumo import route_planner
from simulation.sumo.adapters import SumoAmbulanceTracker, SumoResponder

logger = logging.getLogger(__name__)

# Handing the patient over at the hospital (s, simulated): with a
# pre-alert the team is waiting at the door; without one the casualty
# staff first have to be found and the patient assessed.
HANDOVER_PREALERTED_SECONDS = 120
HANDOVER_UNANNOUNCED_SECONDS = 480

# Automatic complaint tickets for the admin (admin_service.auto_grievance):
# an ambulance standing still this long (s), and a trip this much later
# than expected (s).
STUCK_TICKET_SECONDS = 120
LATE_TICKET_SECONDS = 300

# Off the road this long without arriving (s): treated as arrived, so a
# vehicle SUMO dropped does not wait for ever.
MAX_MISSING_SECONDS = 300


class AmbulanceRun:
    def __init__(self, number, trip, condition, dispatch_at, traffic, signals,
                 referee, clearance, level_index, on_police_alert, add_message,
                 check_hospital=None):
        self.number = number
        self.vehicle_id = f"ambulance_{number:02d}"
        self.label = f"Ambulance {number}"
        self.trip = trip
        self.condition = condition
        self.dispatch_at = dispatch_at

        self.traffic = traffic
        self.signals = signals
        self.referee = referee
        self.level_index = level_index
        self.on_police_alert = on_police_alert
        self.add_message = add_message
        # The 108 call centre's check that the hospital can take the
        # patient (simulation_service._check_hospital): called with
        # (run, now, why) when the hospital is pre-alerted.
        self.check_hospital = check_hospital
        # A hospital check that has to wait (e.g. the ambulance is inside
        # a junction, where its route can't be changed): the reason.
        self.pending_check = None
        # The hospital's answer to the pre-alert, for the dashboard:
        # {"hospital", "need_label", "status"}.
        self.pre_alert = None
        self.diverted_from = None

        self.ambulance = SumoAmbulanceTracker(self.vehicle_id)
        self.engine = CorridorEngine(
            self.ambulance, traffic, signals,
            clearance_predictor=clearance, referee=referee,
        )
        referee.register(
            self.vehicle_id, self.label, condition, stood_still=self._stood_still
        )

        self.dispatched = False
        # The admin record's id (kept after the trip ends, for tickets and
        # the dashboard), the planned trip time, and the stuck check.
        self.trip_request_id = None
        self.planned_seconds = None
        self.stuck_since = None
        self.stuck_reported = False
        # The 108 timeline (simulated seconds): reaching and leaving the
        # patient, then the handover at the hospital after arrival.
        self.patient_reached_at = None
        self.left_patient_at = None
        self.handover_seconds = None
        self.handover_done_at = None
        # After the handover: "returning" / "available" (a 108 ambulance,
        # set by the service) or "done".
        self.service_stage = None
        self.last_now = 0.0
        self.missing_since = None  # off the road, not arrived (teleport)
        self.route_cache = None
        self.route_geometry = route_planner.roads_geometry(trip["roads"])
        self.road_shapes = {}
        self.response = None       # corridor/response.py, after departure
        self.police_watch = None   # corridor/police_watch.py, after departure
        self.events_shown = 0
        self.depart_time = None
        self.arrival_time = None
        self.snapshot = None
        self.upcoming = []
        self.stops = 0
        self.was_moving = False

        # The whole journey (base -> patient -> hospital): "to_patient",
        # "at_patient" (the planned stop) or "to_hospital". Trips without
        # a pickup are "to_hospital" throughout.
        self.picked_up = not self.trip.get("pickup")
        self.at_patient = False

        # The admin dashboard's record of this trip (see the module notes).
        self.request_id = None
        self.recorder = None
        self.stopped_seconds = 0
        self.reroutes = 0
        self.route_signal_ids = set()
        self.cleared_signal_ids = set()
        self.trip_alerts = set()
        self.police_on_scene = set()
        self._giving_way_at = None
        self.accidents = 0  # simulated accidents on this ambulance's route

    # -------------------------------------------------------------

    @property
    def phase(self):
        """"waiting" (not set off yet), "driving" or "arrived"."""
        if self.arrival_time is not None:
            return "arrived"
        if self.route_cache is not None:
            return "driving"
        return "waiting"

    def _stood_still(self):
        if self.phase != "driving":
            return 0.0
        try:
            return self.ambulance.waiting_time()
        except Exception:
            return 0.0

    @property
    def leg(self):
        if getattr(self, "at_patient", False):
            return "at_patient"
        return "to_hospital" if getattr(self, "picked_up", True) else "to_patient"

    def _follow_pickup(self, now, snapshot):
        """Notice arriving at and leaving the patient."""
        if self.picked_up and not self.at_patient:
            return
        stopped = self.ambulance.at_stop()
        if stopped and not self.at_patient:
            self.at_patient = True
            self.patient_reached_at = now
            pickup = self.trip["pickup"]
            self.add_message(f"{self.label} reached the patient.", kind="note")
            self.record_event(
                now, "PICKUP", "Reached the patient",
                detail=f"About {round(pickup['seconds'] / 60)} min to load the patient",
                latitude=snapshot.get("latitude"), longitude=snapshot.get("longitude"),
            )
        elif not stopped and self.at_patient:
            self.at_patient = False
            self.picked_up = True
            self.left_patient_at = now
            self.add_message(
                f"{self.label} has the patient, going to {self.trip['hospital_name']}.",
                kind="note",
            )
            self.record_event(
                now, "PICKUP", "Left with the patient",
                detail=f"To {self.trip['hospital_name']}",
                latitude=snapshot.get("latitude"), longitude=snapshot.get("longitude"),
            )
            # The crew has assessed the patient: the call centre
            # pre-alerts the hospital now.
            self.pending_check = "pre_alert"

    def remaining_roads(self):
        """Roads this ambulance is on or still has to drive."""
        try:
            return self.ambulance.route()[self.ambulance.route_index():]
        except Exception:
            return []

    def set_condition(self, condition):
        self.condition = condition
        self.referee.set_condition(self.vehicle_id, condition)

    # -------------------------------------------------------------

    def step(self, now):
        """One simulated second for this ambulance."""

        self.last_now = now

        if not self.dispatched and now >= self.dispatch_at:
            self.ambulance.dispatch(
                self.trip["roads"],
                self.trip["depart_position"],
                self.trip["arrival_position"],
                stop=self.trip.get("pickup"),
            )
            self.dispatched = True
            logger.info(
                "%s dispatched: %s to %s.",
                self.label, self.trip["start_name"], self.trip["hospital_name"],
            )

        if self.phase == "arrived":
            return

        if self.route_cache is None and not self.ambulance.is_on_road():
            return  # not on the road yet

        self.upcoming = []

        if self.ambulance.is_on_road():
            self.missing_since = None
            if self.route_cache is None:
                self._set_off(now)
            self._drive(now)
        elif (
            self.ambulance.has_arrived()
            or now - (self.missing_since or now) > MAX_MISSING_SECONDS
        ):
            self._arrive(now)
        elif self.missing_since is None:
            # Off the road without arriving: SUMO is teleporting it past
            # a jam; it comes back further along the route.
            self.missing_since = now

    def _set_off(self, now):
        ambulance, traffic = self.ambulance, self.traffic

        self.route_cache = RouteCache(ambulance, traffic)
        self.route_geometry = feed.route_geometry(ambulance, traffic)
        self.engine.build_route_signals()
        self.depart_time = ambulance.departure_time()

        self.response = DeadlockResponse(
            ambulance, traffic, SumoResponder(),
            route_planner.police_stations(),
            route_ahead=lambda info: deadlock.route_ahead(
                info, traffic, ambulance, self.level_index
            ),
            predict=deadlock.predict,
            traffic_level=self.level_index,
            road_name=route_planner.road_name,
            route_seconds=lambda roads: (
                route_planner.estimate_route_seconds(roads, self.level_index)
            ),
            busy_roads=lambda: (
                self.police_watch.active_roads() if self.police_watch else set()
            ),
            critical_can_reroute=lambda: (
                priority.level(self.condition) == 1 and not self.engine.give_way
            ),
            police_alerts=lambda: (
                self.police_watch.alerts.values() if self.police_watch else ()
            ),
        )
        self.response.set_route(deadlock.route_info(ambulance.route(), traffic))

        self.police_watch = self._new_police_watch(
            self.trip.get("stretches", []), f"trip{int(now)}-{self.number}"
        )

        self._start_record(now)
        if self.picked_up:
            # Straight to hospital: pre-alert it as the ambulance sets off.
            self.pending_check = "pre_alert"

    def _new_police_watch(self, stretches, trip_id):
        return SignallessWatch(
            self.ambulance, self.traffic, stretches,
            notify=self._police_alert,
            responder=SumoResponder(),
            trip_id=trip_id,
            busy_roads=self.response.police_roads,
            road_name=route_planner.road_name,
            stations=route_planner.police_stations(),
            urgent=lambda: priority.level(self.condition) == 1,
        )

    def _drive(self, now):
        ambulance, traffic, engine = self.ambulance, self.traffic, self.engine

        snapshot = feed.ambulance_snapshot(ambulance)
        snapshot.update(estimate_eta(self.route_cache, engine.route_signals))
        snapshot["trip_time_seconds"] = round(now - self.depart_time, 1)
        snapshot["depart_time"] = self.depart_time

        # A stop = slowing below walking pace after moving.
        self._follow_pickup(now, snapshot)

        # Standing at the patient is part of the job, not a stop in traffic.
        moving = snapshot["speed"] > 0.5 or self.at_patient
        if self.was_moving and not moving:
            self.stops += 1
        self.was_moving = moving
        snapshot["stops"] = self.stops
        snapshot["leg"] = self.leg
        if not self.picked_up:
            # The time at the patient is still to come.
            snapshot["eta_seconds"] = round(
                snapshot.get("eta_seconds", 0) + self.trip["pickup"]["seconds"], 1
            )
        self.snapshot = snapshot
        if not moving:
            self.stopped_seconds += 1  # one simulated second per step
        self._check_stuck(now, snapshot, moving)

        self.upcoming = engine.upcoming_signals()
        # The AI's predicted arrival at the junction ahead decides
        # when that signal switches (corridor/engine.py).
        seconds_to_next = (
            snapshot.get("next_signal_eta_seconds")
            if self.upcoming and snapshot.get("next_signal_route_index")
            == self.upcoming[0]["route_index"]
            else None
        )
        engine.apply(self.upcoming, now, seconds_to_next)
        self._record_step(now, snapshot)

        if self.response.step(now) == "rerouted":
            self.reroutes += 1
            self._route_changed(now)

        if (
            self.pending_check and self.check_hospital
            and not self.at_patient
            and not self.ambulance.road_id().startswith(":")
        ):
            why, self.pending_check = self.pending_check, None
            self.check_hospital(self, now, why)

        self.police_watch.step(now)

        # Show the response's decisions in the dashboard feed.
        for message in list(self.response.events)[self.events_shown:]:
            self.add_message(message, kind="response")
            if self.recorder is not None:
                rerouted = "saves ~" in message
                self.recorder.add(
                    now, "REROUTE" if rerouted else "AI",
                    "Re-routed" if rerouted else "AI deadlock watch",
                    detail=message,
                    latitude=snapshot.get("latitude"),
                    longitude=snapshot.get("longitude"),
                    data={"route": self.route_geometry} if rerouted else None,
                )
        self.events_shown = len(self.response.events)

    def _check_stuck(self, now, snapshot, moving):
        """Standing still for STUCK_TICKET_SECONDS (not at the patient):
        file a ticket for the admin, once per trip."""
        if moving:
            self.stuck_since = None
            return
        if self.stuck_since is None:
            self.stuck_since = now
        if self.stuck_reported or now - self.stuck_since < STUCK_TICKET_SECONDS:
            return
        self.stuck_reported = True
        where = (
            route_planner.road_name(snapshot.get("road_id") or "")
            or ((self.engine.next_timing or {}).get("signal_id") and "a signal")
            or "the road"
        )
        reason = self.summary().get("delay_reason") or "unknown"
        level = priority.level(self.condition)
        admin_service.auto_grievance(
            "DELAY",
            f"{self.label} stuck for {round((now - self.stuck_since) / 60)} min at {where}",
            description=(
                f"Reason shown on screen: {reason}. Patient: "
                f"{priority.CONDITIONS[self.condition][0]} ({priority.LEVEL_NAMES[level]})."
            ),
            request_id=self.trip_request_id,
            priority="HIGH" if level == 1 else "MEDIUM",
        )

    def _route_changed(self, now, reason="the ambulance was re-routed"):
        """New route: signals, route data, map line and the stretches
        without signals anew."""
        ambulance, traffic, engine = self.ambulance, self.traffic, self.engine
        engine.reset()
        engine.route_signals = []
        engine.build_route_signals()
        self.route_cache = RouteCache(ambulance, traffic)
        self.route_geometry = feed.route_geometry(ambulance, traffic)
        self.road_shapes.clear()
        self.response.set_route(deadlock.route_info(ambulance.route(), traffic))
        self.police_watch.close(now, "CANCELLED", reason)
        self.police_watch = self._new_police_watch(
            route_planner.route_police_plan(ambulance.route())[0],
            f"trip{int(now)}-{self.number}r{self.reroutes}",
        )

    # -------------------------------------------------------------
    # Another hospital (the first can't take the patient)
    # -------------------------------------------------------------

    def on_junction(self):
        try:
            return self.ambulance.road_id().startswith(":")
        except Exception:
            return True

    def divert_route(self, latitude, longitude):
        """The route to a hospital at this point from where the ambulance
        is now (via the patient if not picked up yet), with live travel
        times: {"roads", "arrival_position", "seconds", "old_seconds"}
        (seconds: from the patient / here on), or None."""

        target = route_planner.hospital_road(latitude, longitude)
        if target is None:
            return None
        road, position = target
        route = self.ambulance.route()
        index = self.ambulance.route_index()
        if index < 0 or index >= len(route):
            return None
        if self.picked_up:
            prefix = [route[index]]
        else:
            try:
                prefix = route[index:route.index(self.trip["pickup"]["road"], index) + 1]
            except ValueError:
                return None
        responder = SumoResponder()
        found = responder.find_route(prefix[-1], road)
        if not found:
            return None
        start = index + len(prefix) - 1
        return {
            "roads": prefix + found["roads"][1:],
            "arrival_position": position,
            "seconds": found["seconds"],
            "old_seconds": responder.route_seconds(route[start:]),
        }

    def divert(self, hospital, plan, now, reason):
        """Send the ambulance to another hospital ({"name", "latitude",
        "longitude"}) along plan (divert_route). Returns True if done."""

        if not SumoResponder().reroute_ambulance(self.vehicle_id, plan["roads"]):
            return False
        self.diverted_from = self.trip["hospital_name"]
        # In place: the service's first trip is the same object.
        self.trip.update({
            "hospital_name": hospital["name"],
            "hospital_point": [hospital["latitude"], hospital["longitude"]],
            "arrival_position": plan["arrival_position"],
        })
        self.reroutes += 1
        self._route_changed(now, f"diverted to {hospital['name']}")
        snapshot = self.snapshot or {}
        self.record_event(
            now, "DIVERT", f"Diverted to {hospital['name']}", detail=reason,
            latitude=snapshot.get("latitude"), longitude=snapshot.get("longitude"),
            data={"route": self.route_geometry},
        )
        return True

    @property
    def stage(self):
        """After arrival: "handover", then service_stage (or "done")."""
        if self.phase != "arrived":
            return None
        if self.handover_done_at is not None and self.last_now < self.handover_done_at:
            return "handover"
        return self.service_stage or "done"

    def _arrive(self, now):
        logger.info("%s reached the hospital.", self.label)
        hospital = self.trip["hospital_name"]
        ready = (
            self.pre_alert is not None and self.pre_alert["status"] == "ready"
            and self.pre_alert["hospital"] == hospital
        )
        self.handover_seconds = (
            HANDOVER_PREALERTED_SECONDS if ready else HANDOVER_UNANNOUNCED_SECONDS
        )
        self.handover_done_at = now + self.handover_seconds
        self.add_message(
            f"{self.label} reached {hospital}. "
            + ("Doctors were waiting." if ready
               else "Hospital was not told before, so doctors are being called."),
            kind="hospital",
        )
        self.engine.reset()
        self.referee.forget(self.vehicle_id)
        self.response.close()
        self.police_watch.close(now)
        self.arrival_time = now
        self.snapshot = {
            **self.snapshot,
            "status": "COMPLETED",
            "distance_left_meters": 0.0,
            "eta_seconds": 0.0,
            "trip_time_seconds": round(now - self.depart_time, 1),
        }
        self._finish_record(now)

    # -------------------------------------------------------------
    # The admin dashboard's record of this trip
    # -------------------------------------------------------------

    def _start_record(self, now):
        level = TRAFFIC_LEVELS[self.level_index]
        self.request_id = admin_service.new_request_id(ambulance_number=self.number)
        self.trip_request_id = self.request_id
        self.recorder = TripRecorder(now)
        self.recorder.set_route(self.route_geometry)
        self.recorder.add(
            now, "DISPATCH",
            f"Dispatched: {self.trip['start_name']} to {self.trip['hospital_name']}",
            detail=f"{self.label} · {priority.CONDITIONS[self.condition][0]} · {level} traffic",
        )
        planned, plan_status = self._planned_seconds()
        try:
            meters = round(sum(
                self.traffic.lane_length(f"{road}_0") for road in self.trip["roads"]
            ))
        except Exception:
            meters = None
        self.planned_seconds = planned
        admin_service.start_request(
            self.request_id, self.vehicle_id, self.trip["start_name"],
            self.trip["hospital_name"], level, planned, meters,
            plan_status=plan_status,
        )

    def _planned_seconds(self):
        """The AI trip-time estimate for this route and traffic level
        (what the delay is measured against): (seconds or None, plan
        status), the status saying why there is no estimate."""
        if not os.path.exists(pretrip.MODEL_FILE):
            logger.warning("No planned trip time: %s not found", pretrip.MODEL_FILE)
            return None, "MODEL_MISSING"
        try:
            seconds = route_planner.estimate_route_seconds(
                self.trip["roads"], self.level_index
            )
        except Exception:
            logger.exception("Could not estimate the planned trip time")
            return None, "ERROR"
        if seconds is None:
            logger.warning("No planned trip time for this route")
            return None, "NO_ESTIMATE"
        return seconds, "OK"

    def _record_step(self, now, snapshot):
        """This second: the path, stops, signals switched green and
        give-ways to other ambulances."""
        engine = self.engine
        self.route_signal_ids.update(
            item.get("signal_id") for item in engine.route_signals
        )
        self.cleared_signal_ids |= engine.overridden_signals
        if self.recorder is None:
            return
        self.recorder.step(now, snapshot)

        for event in reversed(engine.events):
            if event["time"] != now:
                break
            if event["type"] != "active" or event["message"].startswith("Part of"):
                continue
            position = self.signals.position(event["signal_id"]) or {}
            name = self.signals.name(event["signal_id"]) or "a signal"
            self.recorder.add(
                now, "SIGNAL", f"Signal switched green: {name}",
                detail=event["message"], junction_name=name,
                latitude=position.get("latitude"),
                longitude=position.get("longitude"),
            )

        give_way = engine.give_way
        signal_id = give_way["signal_id"] if give_way else None
        if signal_id and signal_id != self._giving_way_at:
            position = self.signals.position(signal_id) or {}
            self.recorder.add(
                now, "GIVE_WAY", f"Gave way to {give_way['give_way_to']}",
                detail=f"At {give_way['signal_name']} ({give_way['reason']})",
                junction_name=give_way["signal_name"],
                latitude=position.get("latitude"),
                longitude=position.get("longitude"),
            )
        self._giving_way_at = signal_id

    def _police_alert(self, alert):
        """A police alert for this ambulance: count it, put it on the
        trip's timeline, then let the service phone / log it."""
        status = alert["status"]
        if status == "ALERTED":
            self.trip_alerts.add(alert["alert_id"])
        elif status == "ON_SCENE":
            self.police_on_scene.add(alert["alert_id"])
        elif (
            status == "PASSED" and alert["alert_id"] in self.trip_alerts
            and alert["alert_id"] not in self.police_on_scene
        ):
            # Called, but the ambulance got there first.
            admin_service.auto_grievance(
                "POLICE",
                f"Police did not reach {alert['road']} before {self.label} passed",
                description=f"{alert['station']} was called; the ambulance got there first.",
                request_id=self.trip_request_id,
                priority="LOW",
            )

        if self.recorder is not None:
            if status == "ALERTED":
                when, title = alert["alerted_at"], f"Police alerted: {alert['station']}"
                detail = (
                    f"{alert['road']}: ambulance in ~{alert['ambulance_eta_seconds']:.0f} s, "
                    f"police can be there in ~{alert['police_eta_seconds']:.0f} s"
                )
            elif status == "ON_SCENE":
                when, title = alert.get("on_scene_at"), f"Police reached {alert['road']}"
                detail = f"Officers from {alert['station']} are clearing the road"
            elif status in ("PASSED", "CANCELLED"):
                when, title = alert.get("closed_at"), f"Police alert closed: {alert['road']}"
                detail = alert.get("closed_reason")
            else:
                when = None
            if when is not None:
                self.recorder.add(
                    when, "POLICE", title, detail=detail, road_name=alert["road"],
                    latitude=alert.get("latitude"), longitude=alert.get("longitude"),
                )

        self.on_police_alert(alert)

    def record_event(self, now, kind, title, **fields):
        """An event from outside (e.g. a simulated accident) for this trip."""
        if kind == "ACCIDENT":
            self.accidents += 1
        if self.recorder is not None:
            self.recorder.add(now, kind, title, **fields)

    def _finish_record(self, now):
        if self.request_id is None:
            return
        recorder, self.recorder = self.recorder, None
        recorder.finish(now)
        recorder.add(
            now, "ARRIVAL", f"Reached {self.trip['hospital_name']}",
            detail=f"{(now - self.depart_time) / 60:.1f} min after setting off",
        )
        recorder.add(
            self.handover_done_at, "HANDOVER", "Patient handed over",
            detail=(
                "Pre-alerted: the team was waiting"
                if self.handover_seconds == HANDOVER_PREALERTED_SECONDS
                else "Not pre-alerted: staff had to be called"
            ),
            duration_seconds=self.handover_seconds,
        )
        self.route_signal_ids.discard(None)
        admin_service.finish_request(
            self.request_id, "COMPLETED",
            response_seconds=now - self.depart_time,
            stopped_seconds=self.stopped_seconds,
            stops=self.stops,
            route={
                "optimal_route_used": self.reroutes == 0,
                "signals_total": len(self.route_signal_ids),
                "signals_cleared": len(self.cleared_signal_ids & self.route_signal_ids),
                "police_alerts": len(self.trip_alerts),
                "police_on_scene": len(self.police_on_scene),
                "reroutes": self.reroutes,
                "incidents": self.accidents,
            },
            route_geometry=recorder.route_geometry,
            track=recorder.track,
            events=recorder.events,
            timings=self.timings(now),
        )
        late = (
            now - self.depart_time - self.planned_seconds
            if self.planned_seconds is not None else 0
        )
        if late > LATE_TICKET_SECONDS:
            admin_service.auto_grievance(
                "DELAY",
                f"{self.label}: {self.trip['start_name']} → {self.trip['hospital_name']} "
                f"was {round(late / 60)} min later than expected",
                description=(
                    f"Expected {round(self.planned_seconds / 60)} min, took "
                    f"{round((now - self.depart_time) / 60)} min."
                ),
                request_id=self.request_id,
            )
        self.request_id = None

    def timings(self, now):
        """The 108 timeline of this trip, for the admin record."""
        unit = self.trip.get("unit") or {}
        reached, left = self.patient_reached_at, self.left_patient_at
        return {
            "unit_id": unit.get("id"),
            "unit_kind": unit.get("kind"),
            "to_patient_seconds": reached - self.depart_time if reached is not None else None,
            "scene_seconds": left - reached if reached is not None and left is not None else None,
            "transport_seconds": now - (left if left is not None else self.depart_time),
            "handover_seconds": self.handover_seconds,
            "pre_alerted": self.handover_seconds == HANDOVER_PREALERTED_SECONDS,
            "diverted": getattr(self, "diverted_from", None) is not None,
        }

    def end_record(self, status, notes, title):
        """The simulation stopped or failed before this ambulance arrived:
        status CANCELLED or FAILED."""
        if self.request_id is None:
            return
        recorder, self.recorder = self.recorder, None
        recorded = {}
        if recorder is not None:
            recorder.finish(recorder.last_time)
            recorder.add(recorder.last_time, "END", title)
            recorded = {
                "route_geometry": recorder.route_geometry,
                "track": recorder.track,
                "events": recorder.events,
            }
        admin_service.finish_request(self.request_id, status, notes=notes, **recorded)
        self.request_id = None

    # -------------------------------------------------------------

    @staticmethod
    def _junction_blocked(snapshot, timing):
        """Stopped inside a junction, or at a green signal with no queue
        ahead: cars stuck in the junction box are in the way."""
        if (snapshot.get("road_id") or "").startswith(":"):
            return True
        seconds = timing.get("seconds_to_arrival")
        return (
            timing.get("stage") == "green" and seconds is not None and seconds < 8
            and not timing.get("queued_ahead_per_lane")
        )

    def _police_here(self, snapshot):
        """Police are working on the road the ambulance is stuck on (not
        just somewhere else on its route)."""
        road = snapshot.get("road_id")
        for alert in (self.police_watch.alerts.values() if self.police_watch else []):
            if alert["status"] not in ("ALERTED", "EN_ROUTE", "ON_SCENE"):
                continue
            roads = set(alert.get("control_roads") or ()) | {alert.get("road_id")}
            if road is None or alert.get("road_id") is None or road in roads:
                return True
        return False

    def summary(self):
        """This ambulance for the fleet list and the map."""

        snapshot = self.snapshot or {}
        timing = self.engine.next_timing or {}
        delay_reason = None
        # Standing at the patient is the planned pickup, not a delay.
        if (
            self.phase == "driving" and snapshot.get("speed", 1) < 0.5
            and not getattr(self, "at_patient", False)
        ):
            if self.engine.give_way:
                delay_reason = (
                    f"letting {self.engine.give_way['give_way_to']} go first "
                    f"at {self.engine.give_way['signal_name']}"
                )
            elif self._junction_blocked(snapshot, timing):
                delay_reason = "junction blocked by stuck cars"
            elif self._police_here(snapshot):
                delay_reason = "police clearing a jam ahead"
            elif timing.get("stage") in ("yellow", "all_red"):
                delay_reason = "signal changing"
            elif timing.get("queued_ahead_per_lane", 0) > 0:
                delay_reason = "queue at the next signal"
            else:
                delay_reason = "traffic"
        return {
            "vehicle_id": self.vehicle_id,
            "number": self.number,
            "label": self.label,
            **priority.describe(self.condition),
            "status": self.phase,
            "start_name": self.trip["start_name"],
            "hospital_name": self.trip["hospital_name"],
            "start_point": self.trip.get("start_point"),
            "hospital_point": self.trip.get("hospital_point"),
            "crosses": self.trip.get("crosses"),
            "leg": self.leg if self.phase == "driving" else None,
            "base_name": (self.trip.get("base") or {}).get("name"),
            "pickup_point": (
                [self.trip["pickup"]["latitude"], self.trip["pickup"]["longitude"]]
                if self.trip.get("pickup") else None
            ),
            "routing_decision": self.trip.get("routing_decision"),
            "latitude": snapshot.get("latitude"),
            "longitude": snapshot.get("longitude"),
            "heading": snapshot.get("heading"),
            "speed": snapshot.get("speed") if self.phase == "driving" else None,
            "eta_seconds": snapshot.get("eta_seconds"),
            "distance_left_meters": snapshot.get("distance_left_meters"),
            "trip_time_seconds": snapshot.get("trip_time_seconds"),
            "stops": snapshot.get("stops"),
            "delay_reason": delay_reason,
            "route": self.route_geometry,
            "next_signal": (
                {
                    "signal_id": timing.get("signal_id"),
                    "name": next(
                        (
                            signal.get("name")
                            for signal in self.engine.route_signals
                            if signal["signal_id"] == timing.get("signal_id")
                        ),
                        None,
                    ),
                    "seconds_to_arrival": timing.get("seconds_to_arrival"),
                    "stage": timing.get("stage"),
                }
                if timing and self.phase == "driving"
                else None
            ),
            "give_way": self.engine.give_way,
            "advised_speed": self.engine.advised_speed,
            "unit": self.trip.get("unit"),
            "request_id": getattr(self, "trip_request_id", None),
            # After arrival: "handover" (with the seconds left), then
            # "returning" / "available" (108) or "done".
            "stage": self.stage if hasattr(self, "service_stage") else None,
            "handover_left_seconds": (
                round(self.handover_done_at - self.last_now)
                if getattr(self, "handover_done_at", None) is not None
                and self.last_now < self.handover_done_at else None
            ),
            "pre_alert": getattr(self, "pre_alert", None),
            "diverted_from": getattr(self, "diverted_from", None),
        }
