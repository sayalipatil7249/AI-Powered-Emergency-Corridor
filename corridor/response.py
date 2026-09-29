"""
Deadlock response: when the AI predicts the ambulance will get stuck on
the route ahead, re-route it or send traffic police, whichever gets it to
the hospital sooner.

Every CHECK_EVERY seconds the deadlock predictor (ai/deadlock.py) looks at
the route ahead. If the ambulance is likely to stand still for a while:
    re-route   fastest route to the hospital that avoids the jam; both
               routes are judged with the AI trip-time model (junctions,
               turns and signals slow a detour down, which live travel
               times alone miss); saving = current route + predicted
               standing time - new route
    police     the station whose car can reach the jam first; its saving
               = the predicted standing time it can prevent, if it
               arrives in time
The larger saving wins. Police drive there with sirens; on arrival they
stop traffic coming in from side roads and wave stuck cars through until
the ambulance has passed.

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

# Stuck re-route: unlike the predicted re-route above, this reacts to
# what is really happening. When the ambulance has stood still this long
# (or an accident blocks its route ahead), it takes the fastest route from
# where it is now that avoids the jammed roads ahead - if the trip-time
# model says that is no slower than staying and waiting.
STILL_SPEED = 0.5             # m/s: standing still
STUCK_REROUTE_SECONDS = 40    # standing still this long -> look for a way round
REROUTE_LOOKAHEAD_METERS = 400  # jammed roads this far ahead are avoided
ACCIDENT_WAIT_SECONDS = 300   # expected wait behind a crash (police must clear it)
REROUTE_COOLDOWN_SECONDS = 90 # between two stuck re-routes (or failed searches)
MAX_STUCK_REROUTES = 3        # per trip
MIN_STUCK_REROUTE_SAVING = 30 # seconds a way round must save (no flip-flopping
                              # between two routes that are about as good)


class DeadlockResponse:
    def __init__(self, ambulance, traffic, responder, stations,
                 route_ahead, predict, traffic_level, road_name=None,
                 act=True, route_seconds=None, busy_roads=None,
                 incident_roads=None):
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
        incident_roads() -> roads blocked by an accident right now
        """

        self.busy_roads = busy_roads or (lambda: set())
        self.incident_roads = incident_roads or (lambda: set())

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
        self._still_since = None           # standing still since (s)
        self._stuck_reroutes = 0
        self._next_reroute_check = 0.0
        # Every road avoided this trip: a later re-route must not lead
        # back into a jam it already went round.
        self._avoided = set()

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

        # Really stuck (or an accident ahead): a way round from here.
        if self._reroute_if_stuck(now):
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

    def _reroute_if_stuck(self, now):
        """Re-route from the ambulance's current road when it has stood
        still for STUCK_REROUTE_SECONDS or an accident blocks the road
        ahead. Returns True when it was given a new route."""

        if self.ambulance.speed() > STILL_SPEED:
            self._still_since = None
        elif self._still_since is None:
            self._still_since = now

        if (
            not self.act
            or self._stuck_reroutes >= MAX_STUCK_REROUTES
            or now < self._next_reroute_check
            or self.ambulance.road_id().startswith(":")
        ):
            return False

        route = self.ambulance.route()
        index = self.ambulance.route_index()

        # The roads just ahead (the ambulance's own road cannot be avoided:
        # a new route must start on it).
        ahead, meters = [], 0.0
        for road in route[index + 1:]:
            if meters > REROUTE_LOOKAHEAD_METERS:
                break
            ahead.append(road)
            meters += self.traffic.lane_length(f"{road}_0")
        if not ahead:
            return False  # already on the last road

        blocked = [road for road in ahead if road in self.incident_roads()]
        still_for = now - self._still_since if self._still_since is not None else 0.0
        if blocked:
            avoid, cause, expected_wait = blocked, "an accident", ACCIDENT_WAIT_SECONDS
        elif still_for >= STUCK_REROUTE_SECONDS:
            # The queue it is stuck in: roads ahead with stopped vehicles
            # (at least the next one).
            avoid = [
                road for road in ahead if self.traffic.road_halting_count(road) > 0
            ] or ahead[:1]
            # Stuck this long already: expect about as long again.
            cause, expected_wait = "heavy traffic", still_for
        else:
            return False

        self._next_reroute_check = now + REROUTE_COOLDOWN_SECONDS
        current = route[index]
        avoid_all = sorted((set(avoid) | self._avoided) - {current})
        new_route = self.responder.route_around(self.ambulance.vehicle_id, avoid_all)
        if not new_route:
            self._event(
                f"Ambulance held up by {cause} on {self._names(avoid)}: no other "
                "way to the hospital from here. Staying on the route."
            )
            return False

        stay_seconds = self.route_seconds(route[index:])
        new_seconds = self.route_seconds(new_route)
        if stay_seconds is None or new_seconds is None:
            # No trip-time model: compare both on live travel times.
            stay_seconds = self.responder.route_seconds(route[index:])
            new_seconds = self.responder.route_seconds(new_route)
        saving = stay_seconds + expected_wait - new_seconds
        if saving < MIN_STUCK_REROUTE_SAVING:
            self._event(
                f"Ambulance held up by {cause} on {self._names(avoid)}. The best "
                f"way round (via {self._names(new_route[1:])}) would not save "
                "time. Staying on the route."
            )
            return False

        if not self.responder.reroute_ambulance(self.ambulance.vehicle_id, new_route):
            return False

        self._stuck_reroutes += 1
        self._avoided.update(avoid)
        self._still_since = None
        # Police on their way to the old jam are no longer needed.
        if self.status in ("police_en_route", "police_clearing") and self.police:
            if self.police["stage"] != "done":
                self.responder.remove_unit(self.police["unit_id"])
                self.police["stage"] = "done"
            self.responder.release_junctions()
        self.status = "resolved"
        self._cooldown_until = now + COOLDOWN_SECONDS
        self.decision = {
            "time": now,
            "choice": "reroute",
            "reason": cause,
            "stuck_seconds": round(still_for),
            "avoided": self._names(avoid),
            "reroute_saving_seconds": None,
            "police_saving_seconds": None,
        }
        self._event(
            f"Ambulance held up by {cause} on {self._names(avoid)}"
            + (f" (standing still {still_for:.0f} s)" if still_for >= 1 else "")
            + f". Re-routed from its current position via "
            f"{self._names(new_route[1:])}: about {new_seconds / 60:.0f} min to "
            "the hospital."
        )
        return True

    def _names(self, roads, limit=2):
        """'Main Road, Station Road' for a list of road ids."""
        names = []
        for road in roads:
            name = self.road_name(road)
            if name and name not in names:
                names.append(name)
            if len(names) == limit:
                break
        return ", ".join(names) or "the road ahead"

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

        # Option 1: a new route around the jam.
        reroute_saving, new_route = 0.0, None
        alternative = self.responder.find_route(current, route[-1])
        if (
            alternative
            and alternative["roads"][0] == current
            and not set(alternative["roads"]) & set(jam_roads)
        ):
            now_seconds = self.route_seconds(route[index:])
            new_seconds = self.route_seconds(alternative["roads"])
            if now_seconds is not None and new_seconds is not None:
                reroute_saving = now_seconds + seconds - new_seconds
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
                    f"Deadlock predicted on {self.jam['name']} "
                    f"({probability:.0%} likely, ~{seconds:.0f} s stuck). "
                    f"Re-routed the ambulance around it: saves ~{reroute_saving:.0f} s."
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
                    f"Deadlock predicted on {self.jam['name']} "
                    f"({probability:.0%} likely, ~{seconds:.0f} s stuck). "
                    f"Alert sent to {station['name']}: police arriving in "
                    f"~{eta / 60:.0f} min to clear the traffic."
                )
                return None

        self.decision["choice"] = "monitor"
        self.status = "watching"
        self._cooldown_until = now + COOLDOWN_SECONDS
        self._event(
            f"Jam ahead on {self.jam['name']} ({probability:.0%} likely), but "
            "neither a new route nor police would get the ambulance there "
            "sooner. Watching."
        )
        return None

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
            self._event(
                f"Police from {police['station']} arrived at "
                f"{self.jam['name']} after {now - police['sent_at']:.0f} s "
                "and are clearing the traffic."
            )

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
                f"{'Ambulance through' if passed else 'Police stood down'}: "
                f"{police['vehicles_waved']} vehicles waved through at "
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
