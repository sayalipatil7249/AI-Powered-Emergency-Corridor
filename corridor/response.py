"""
Deadlock response: when the AI predicts a jam ahead, send traffic police.
If a critical ambulance remains stopped, also reconsider a legal detour.

Every CHECK_EVERY seconds the deadlock predictor (ai/deadlock.py) looks at
the route ahead. If the ambulance is likely to stand still for a while:
    re-route   a legal route avoiding blocked roads ahead. The AI trip-time
               model prices both routes so side-street detours are not
               assumed to be faster merely because they are empty.
    police     the station whose car can reach the jam first; its saving
               = the predicted standing time it can prevent, if it
               arrives in time
Normally police respond; earlier experiments found speculative rerouting
slower. A critical crew that has already stood still for 45 seconds may
divert only when an avoidable road is still ahead and the estimated saving
is at least 90 seconds. Police drive there with sirens; on arrival they
stop traffic coming in from side roads and wave stuck cars through.

Uses only the connectors in corridor/interfaces.py; the predictor, the
route-ahead features and road names are passed in.
"""

import logging
from collections import deque

from corridor.police_watch import worst_stopped_share

logger = logging.getLogger(__name__)

CHECK_EVERY = 5               # seconds between predictions
ALERT_PROBABILITY = 0.4       # predicted chance of getting stuck (best balance
                              # of right alerts vs deadlocks caught early)
MIN_EXPECTED_STUCK = 30       # predicted seconds standing still
DISPATCH_SECONDS = 60         # police get into the car and leave
SIREN_FACTOR = 0.8            # police with siren vs live travel time
POLICE_EFFECTIVENESS = 0.7    # share of the standing time police prevent
# Re-routing is still compared (and shown), but not carried out: in tests
# (ai/response_experiments.py) every re-route made the trip slower, while
# sending police saved 126 s on average. Set True to allow it again.
ALLOW_REROUTE = False
MIN_REROUTE_SAVING = 90       # seconds a new route must save (live travel
                              # times are optimistic for a long detour)
MIN_POLICE_SAVING = 20
MAX_CLEARING_SECONDS = 240    # police stay at most this long
COOLDOWN_SECONDS = 60         # after one response, before the next
MIN_SPEED_FOR_TIMING = 4.0    # m/s, for "when does the ambulance get there"
RESCUE_STALL_SECONDS = 45     # observed continuous stop before a critical detour
RESCUE_CHECK_EVERY = 15       # avoid searching the road network every tick
RESCUE_POLICE_SCENE_GRACE = 90  # let officers clear a junction before diverting


class DeadlockResponse:
    def __init__(self, ambulance, traffic, responder, stations,
                 route_ahead, predict, traffic_level, road_name=None,
                 act=True, route_seconds=None, busy_roads=None,
                 critical_can_reroute=None, police_alerts=None):
        """
        stations: [{"name", "latitude", "longitude", "road"}]
        route_ahead(route_info) -> (features, jam)   (ai/deadlock.py)
        predict(features) -> (probability, seconds) or None
        road_name(road_id) -> street name or None
        route_seconds(roads) -> realistic seconds for a route, or None
             (default: the responder's live travel times)
        act: False = decide but do nothing (the fair "without" case in
             ai/response_experiments.py: same checks, no action)
        busy_roads() -> roads other police are already clearing
             (corridor/police_watch.py), so two cars are not sent there
        critical_can_reroute() -> True for a critical crew not giving way
        police_alerts() -> active signal-less-watch alerts for police timing
        """

        self.busy_roads = busy_roads or (lambda: set())
        self.critical_can_reroute = critical_can_reroute or (lambda: False)
        self.police_alerts = police_alerts or (lambda: ())

        self.act = act
        self.route_seconds = route_seconds or responder.route_seconds

        self.ambulance = ambulance
        self.traffic = traffic
        self.responder = responder
        self.stations = stations
        self.route_ahead = route_ahead
        self.predict = predict
        self.traffic_level = traffic_level
        self.road_name = road_name or (lambda road_id: None)

        self.route_info = None
        self.status = "watching"
        self.risk = None
        self.decision = None
        self.jam = None
        self.police = None
        self.events = deque(maxlen=20)
        self._last_check = -CHECK_EVERY
        self._cooldown_until = 0
        self._units = 0
        self._stopped_since = None
        self._last_rescue_check = -RESCUE_CHECK_EVERY

    # -------------------------------------------------------------

    def set_route(self, route_info):
        """The ambulance's route (again after a re-route)."""
        self.route_info = route_info

    def step(self, now):
        """Call every simulation step. Returns "rerouted" when the
        ambulance was given a new route (the caller refreshes its
        route data and calls set_route)."""

        if self.route_info is None or not self.ambulance.is_on_road():
            return None

        # At the patient: nothing to predict or rescue until it drives on.
        if self.ambulance.at_stop():
            self._stopped_since = None
            return None

        # Standing at the patient (a planned stop) is not being stuck.
        if self.ambulance.speed() < 0.5 and not self.ambulance.at_stop():
            if self._stopped_since is None:
                self._stopped_since = now
        else:
            self._stopped_since = None

        if self._check_critical_rescue(now):
            return "rerouted"

        if self.status in ("police_en_route", "police_clearing"):
            self._follow_police(now)
            return None

        if now < self._cooldown_until or now - self._last_check < CHECK_EVERY:
            return None
        self._last_check = now

        features, jam = self.route_ahead(self.route_info)
        prediction = self.predict(features)
        if prediction is None:
            return None

        probability, seconds = prediction
        self.risk = {
            "probability": round(probability, 2),
            "expected_stuck_seconds": round(seconds),
        }
        if probability < ALERT_PROBABILITY or seconds < MIN_EXPECTED_STUCK:
            self.status = "watching"
            return None
        if self.ambulance.road_id().startswith(":"):
            return None  # decide once back on a normal road

        return self._respond(now, jam, probability, seconds)

    # -------------------------------------------------------------

    def _respond(self, now, jam, probability, seconds):
        route = self.ambulance.route()
        index = self.ambulance.route_index()
        current = self.ambulance.road_id()

        if jam is None:
            # Stuck right here (e.g. a blocked junction just ahead).
            jam = {"start_index": index, "end_index": min(index + 1, len(route) - 1),
                   "distance": 0.0, "length": 0.0}
        jam_roads = route[jam["start_index"]:jam["end_index"] + 1]

        if set(jam_roads) & self.busy_roads():
            # Police are already on their way there (signal-less watch).
            self.status = "watching"
            self._cooldown_until = now + COOLDOWN_SECONDS
            return None

        self.jam = self._describe_jam(jam, jam_roads)

        # Option 1: a new route around the avoidable part of the jam.
        # The current road cannot be skipped by setRoute().
        reroute_saving, new_route = 0.0, None
        avoidable = set(jam_roads) - {current}
        alternative = (self.responder.find_route_avoiding(
            current, route[-1], avoidable
        ) if avoidable else None)
        if alternative and alternative["roads"] != route[index:]:
            now_seconds = self.route_seconds(route[index:])
            new_seconds = self.route_seconds(alternative["roads"])
            if now_seconds is not None and new_seconds is not None:
                # Predicted delay on the current edge is unavoidable.
                future_delay = seconds if jam["start_index"] > index else 0
                reroute_saving = now_seconds + future_delay - new_seconds
                new_route = alternative["roads"]

        # Option 2: police from the station that gets there first.
        best = None
        for station in self.stations:
            trip = self.responder.find_route(station["road"], jam_roads[0], "police")
            if trip is None:
                continue
            eta = DISPATCH_SECONDS + trip["seconds"] * SIREN_FACTOR
            if best is None or eta < best[1]:
                best = (station, eta, trip["roads"])

        police_saving = 0.0
        if best:
            ambulance_arrives = jam["distance"] / max(
                self.ambulance.speed(), MIN_SPEED_FOR_TIMING
            )
            late = max(0.0, best[1] - ambulance_arrives)
            police_saving = max(0.0, seconds - late) * POLICE_EFFECTIVENESS

        self.decision = {
            "time": now,
            "probability": round(probability, 2),
            "expected_stuck_seconds": round(seconds),
            "reroute_saving_seconds": round(reroute_saving) if new_route else None,
            "police_saving_seconds": round(police_saving) if best else None,
            "station": best[0]["name"] if best else None,
            "police_eta_seconds": round(best[1]) if best else None,
        }

        if not self.act:
            self.decision["choice"] = "monitor"
            self.status = "watching"
            self._cooldown_until = now + COOLDOWN_SECONDS
            return None

        if (
            ALLOW_REROUTE
            and new_route
            and reroute_saving >= max(MIN_REROUTE_SAVING, police_saving)
        ):
            if self.responder.reroute_ambulance(self.ambulance.vehicle_id, new_route):
                self.decision["choice"] = "reroute"
                self._finish(now)
                self._event(
                    f"Likely jam on {self.jam['name']}. "
                    f"Took a faster road (saves ~{reroute_saving:.0f} s)."
                )
                return "rerouted"

        if best and police_saving >= MIN_POLICE_SAVING:
            station, eta, roads = best
            self._units += 1
            unit_id = f"police_{self._units}"
            if self.responder.send_unit(unit_id, roads):
                self.decision["choice"] = "police"
                self.status = "police_en_route"
                self.police = {
                    "unit_id": unit_id,
                    "station": station["name"],
                    "station_latitude": station["latitude"],
                    "station_longitude": station["longitude"],
                    "target_road": jam_roads[0],
                    "jam_roads": jam_roads,
                    "jam_end_index": jam["end_index"],
                    "sent_at": now,
                    "eta_seconds": round(eta),
                    "initial_eta_seconds": round(eta),
                    "latitude": station["latitude"],
                    "longitude": station["longitude"],
                    "stage": "en_route",
                    "vehicles_waved": 0,
                    "road": self.jam["name"],
                    # The roads the officers will control, for the map.
                    "zone": self._zone(jam_roads),
                    "stopped_on_arrival": None,
                    "stopped_after": None,
                }
                self._event(
                    f"Likely jam on {self.jam['name']}. "
                    f"Called police from {station['name']} (about {max(1, round(eta / 60))} min)."
                )
                return None

        self.decision["choice"] = "monitor"
        self.status = "watching"
        self._cooldown_until = now + COOLDOWN_SECONDS
        self._event(
            f"Likely jam on {self.jam['name']}. "
            "No faster road, keeping watch."
        )
        return None

    def _check_critical_rescue(self, now):
        """Give a *stalled* critical crew a carefully priced way around a jam.

        Normal trips retain the tested police-first policy. An observed stop
        and a blocked future edge are both required; a detour cannot rescue
        an ambulance trapped on its current edge.
        """
        if (
            not self.act or not self.critical_can_reroute()
            or self._stopped_since is None
            or now - self._stopped_since < RESCUE_STALL_SECONDS
            or now - self._last_rescue_check < RESCUE_CHECK_EVERY
            or self.ambulance.road_id().startswith(":")
        ):
            return False
        self._last_rescue_check = now
        route = self.ambulance.route()
        index = self.ambulance.route_index()
        if index < 0 or index >= len(route):
            return False
        features, jam = self.route_ahead(self.route_info)
        if not jam:
            return False
        blocked = set(route[max(index + 1, jam["start_index"]):jam["end_index"] + 1])
        if not blocked:
            return False
        if (self.police and self.police["stage"] != "done"
                and blocked.intersection(self.police["jam_roads"])):
            if self.status == "police_clearing" and (
                now - self.police["arrived_at"] < RESCUE_POLICE_SCENE_GRACE
            ):
                return False
            if self.status == "police_en_route" and (
                self.police["eta_seconds"] <= RESCUE_POLICE_SCENE_GRACE
                and now - self.police["sent_at"]
                < self.police["initial_eta_seconds"] + RESCUE_POLICE_SCENE_GRACE
            ):
                return False
        for alert in self.police_alerts():
            if alert.get("road_id") not in blocked:
                continue
            if alert["status"] == "ON_SCENE" and (
                now - alert["on_scene_at"] < RESCUE_POLICE_SCENE_GRACE
            ):
                return False
            if alert["status"] == "EN_ROUTE" and (
                now - alert["alerted_at"]
                < alert["police_eta_seconds"] + RESCUE_POLICE_SCENE_GRACE
                and alert["police_eta_seconds"] - (now - alert["alerted_at"])
                <= RESCUE_POLICE_SCENE_GRACE
            ):
                return False
        alternative = self.responder.find_route_avoiding(
            route[index], route[-1], blocked
        )
        if not alternative or alternative["roads"] == route[index:]:
            return False
        old_seconds = self.route_seconds(route[index:])
        new_seconds = self.route_seconds(alternative["roads"])
        if old_seconds is None or new_seconds is None:
            return False
        prediction = self.predict(features)
        future_delay = (
            min(180, max(0, prediction[1]))
            if prediction and prediction[0] >= ALERT_PROBABILITY
            and jam["start_index"] > index else 0
        )
        saving = old_seconds + future_delay - new_seconds
        if saving < MIN_REROUTE_SAVING:
            return False
        if not self.responder.reroute_ambulance(self.ambulance.vehicle_id,
                                                alternative["roads"]):
            return False
        if self.police and self.police["stage"] != "done":
            self.responder.remove_unit(self.police["unit_id"])
            self.responder.release_junctions()
            self.police["stage"] = "done"
        self.jam = self._describe_jam(
            jam, route[jam["start_index"]:jam["end_index"] + 1]
        )
        self.decision = {
            "time": now, "choice": "critical_rescue_reroute",
            "reroute_saving_seconds": round(saving),
            "observed_stall_seconds": round(now - self._stopped_since),
        }
        self._finish(now)
        self._stopped_since = None
        self._event(
            f"Critical ambulance stuck near {self.jam['name']}. "
            f"Took another road (saves ~{saving:.0f} s)."
        )
        return True

    def _follow_police(self, now):
        police = self.police
        state = self.responder.unit_state(police["unit_id"], police["target_road"])

        if self.status == "police_en_route":
            if state and not state["arrived"]:
                if not state.get("pending"):
                    police["latitude"] = state["latitude"]
                    police["longitude"] = state["longitude"]
                police["eta_seconds"] = max(
                    0, round(police["eta_seconds"] - 1)
                )
                return
            # Arrived: officers get out and start directing traffic.
            if state:
                police["latitude"] = state["latitude"]
                police["longitude"] = state["longitude"]
            self.responder.remove_unit(police["unit_id"])
            self.status = "police_clearing"
            police["stage"] = "clearing"
            police["arrived_at"] = now
            police["eta_seconds"] = 0
            police["stopped_on_arrival"] = worst_stopped_share(
                self.traffic, police["jam_roads"]
            )
            self._event(f"Police clearing {self.jam['name']}.")

        route = self.ambulance.route()
        police["vehicles_waved"] += self.responder.control_junctions(
            police["jam_roads"], route
        )

        passed = self.ambulance.route_index() > police["jam_end_index"]
        if passed or now - police["arrived_at"] > MAX_CLEARING_SECONDS:
            self.responder.release_junctions()
            police["stage"] = "done"
            police["stopped_after"] = worst_stopped_share(
                self.traffic, police["jam_roads"]
            )
            police["on_scene_seconds"] = round(now - police["arrived_at"])
            self._event(
                f"{'Ambulance got past' if passed else 'Police left'} "
                f"{self.jam['name']}."
            )
            self._finish(now)

    # -------------------------------------------------------------

    def _finish(self, now):
        self.status = "resolved"
        self._cooldown_until = now + COOLDOWN_SECONDS

    def police_roads(self):
        """Roads this response's police are heading to or clearing."""
        if self.status in ("police_en_route", "police_clearing") and self.police:
            return set(self.police["jam_roads"])
        return set()

    def close(self):
        """Trip over: traffic back to normal, police car removed."""
        if self.police and self.police["stage"] != "done":
            self.responder.remove_unit(self.police["unit_id"])
            self.responder.release_junctions()

    def _zone(self, roads):
        """[[latitude, longitude], ...] along these roads."""
        points = []
        for road in roads:
            for x, y in self.traffic.lane_shape(f"{road}_0"):
                position = self.traffic.to_latlon(x, y)
                points.append([position["latitude"], position["longitude"]])
        return points

    def _describe_jam(self, jam, jam_roads):
        road = jam_roads[0]
        x, y = self.traffic.lane_shape(f"{road}_0")[0]
        name = next(
            (n for n in (self.road_name(r) for r in jam_roads) if n),
            "the road ahead",
        )
        return {
            "name": name,
            **self.traffic.to_latlon(x, y),
            "distance_meters": round(jam["distance"]),
            "length_meters": round(jam["length"]),
        }

    def _event(self, message):
        logger.info(message)
        self.events.append(message)

    def summary(self):
        """State for the dashboard and the AI agent."""
        return {
            "status": self.status,
            "risk": self.risk,
            "jam": self.jam,
            "decision": self.decision,
            "police": (
                {k: v for k, v in self.police.items() if k != "jam_roads"}
                if self.police else None
            ),
            "events": list(self.events),
        }
