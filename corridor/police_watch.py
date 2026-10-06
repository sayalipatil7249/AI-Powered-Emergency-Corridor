"""
Police for the parts of the route where no signal can clear the way.

The corridor engine clears queues at signals by switching them green.
Between signals (and at junctions without a signal) only people can:
when a stretch ahead of the ambulance stays jammed - an accident,
roadwork or just too many vehicles - the police station that can get
there first is alerted (a phone call, backend/services/police_notifier.py)
early enough for officers to clear it before the ambulance arrives.

    planning   route_planner.signalless_stretches / police_cover: the
               stretches of the route without signals and, per stretch,
               the stations ranked by driving time
    here       every CHECK_EVERY seconds: is a stretch ahead jammed
               (for CONFIRM_SECONDS)? Is the ambulance close enough that
               police must leave now? Then alert.

    stalled    anywhere on the route (signal or not): when the ambulance
               has been stopped most of the last 30 s, find the car at the
               front of its queue and send police to the junction that car
               is stuck at - often well past the start of a jammed stretch,
               or at a green signal whose road beyond is full.

Alert: ALERTED -> EN_ROUTE -> ON_SCENE -> PASSED, or CANCELLED when the
jam clears by itself first (or the officers stand down). Officers on
scene hold traffic coming into their junctions from other roads and wave
the stuck vehicles through.

Uses only the connectors in corridor/interfaces.py.
"""

import logging
import math
from collections import deque

logger = logging.getLogger(__name__)

CHECK_EVERY = 5                 # seconds between checks
LOOKAHEAD_METERS = 3000         # only stretches this close are watched
VEHICLE_SPACE_METERS = 7.5      # road space per stopped vehicle

# Jammed: stopped vehicles fill JAM_STOPPED_SHARE of a road, or traffic
# crawls below JAM_SPEED_RATIO of the speed limit with CLEAR_STOPPED_SHARE
# stopped. It stays jammed while CLEAR_STOPPED_SHARE is stopped or traffic
# still crawls, so a jam that comes and goes does not alert on, off, on.
JAM_STOPPED_SHARE, CLEAR_STOPPED_SHARE = 0.4, 0.2
JAM_SPEED_RATIO = 0.25

CONFIRM_SECONDS = 15            # jammed this long before alerting
DISPATCH_SECONDS = 60           # officers get into the car
SIREN_FACTOR = 0.8              # siren drive vs normal drive time
CLEAR_SECONDS = 90              # officers need this long on scene
ALERT_HORIZON_SECONDS = 300     # always alert when the ambulance is this close
MIN_SPEED_FOR_TIMING = 4.0      # m/s, for "when does the ambulance get there"
STAND_DOWN_SECONDS = 60         # jam gone this long -> cancel the alert
REALERT_SECONDS = 120           # no new alert (phone call) for the same
                                # stretch this soon after one was closed
MAX_CLEARING_SECONDS = 240      # officers on scene stand down after
                                # this long, but only while the ambulance
                                # is still over ALERT_HORIZON_SECONDS
                                # away: otherwise the jam returns just
                                # before it arrives and the next unit is
                                # too far to help

# A reported accident (not just heavy traffic) is alerted at once: there
# is no need to wait for the queue behind it to build up.
# Officers need INCIDENT_CLEAR_SECONDS on scene to move the crashed
# vehicles (simulation/sumo/adapters.py POLICE_CLEAR_SECONDS).
INCIDENT_CLEAR_SECONDS = 45
# For placing a simulated accident where police can beat the ambulance:
# the ambulance's typical speed in traffic, and a safety margin.
TYPICAL_AMBULANCE_SPEED = 7.0   # m/s
INCIDENT_MARGIN_SECONDS = 60

ACTIVE = ("ALERTED", "EN_ROUTE", "ON_SCENE")

# Officers stuck in the queue this close to the jam park and walk.
WALK_METERS = 250

# The ambulance stopped at STALL_CHECKS of the last STALL_WINDOW checks
# (every CHECK_EVERY s: 20 of the last 30 s, so creeping forward a few
# metres does not reset it) sends police to whatever is holding the front
# of its queue, signal or not.
STALL_WINDOW = 6
STALL_CHECKS = 4
# A Critical patient: police are sent after 10 of the last 15 s stopped.
URGENT_STALL_CHECKS = 2
# Police with siren drive past queues: their time is the free-flow time
# times this (not the live travel time of a gridlocked road).
SIREN_FREE_FLOW_FACTOR = 1.3
# A blockage this close to one police already work on is theirs (m).
SAME_BLOCKAGE_METERS = 150
# Stations tried for a blockage (nearest in a straight line first).
BLOCKAGE_CANDIDATES = 3


def _meters(lat1, lon1, lat2, lon2):
    """Straight-line distance for short distances (m)."""
    x = (lon2 - lon1) * 111320 * math.cos(math.radians(lat1))
    y = (lat2 - lat1) * 110540
    return math.hypot(x, y)


def worst_stopped_share(traffic, roads):
    """Share of the most jammed of these roads filled with stopped
    vehicles (0 to 1): the "before / after" of police clearing a road."""

    worst = 0.0
    for road in roads:
        length = max(traffic.lane_length(f"{road}_0"), 1.0)
        lanes = max(1, traffic.road_lane_count(road))
        worst = max(worst, min(
            1.0, traffic.road_halting_count(road) * VEHICLE_SPACE_METERS / lanes / length
        ))
    return round(worst, 2)


class SignallessWatch:
    def __init__(self, ambulance, traffic, stretches, notify=None,
                 responder=None, trip_id="trip", busy_roads=None,
                 road_name=None, stations=(), urgent=None):
        """
        stretches: from the route planner (indices into ambulance.route())
        notify(alert): called for every new alert and status change
        responder: optional Responder (corridor/interfaces.py) that drives
            a police car there and lets the officers direct traffic
        busy_roads(): roads other police are already clearing (the
            deadlock response), so two cars are not sent to one jam
        road_name(road_id): street name or None
        stations: every police station [{"name", "latitude", "longitude",
            "road"}], for blockages off the planned stretches
        urgent(): True for a Critical patient (police sent sooner)
        """

        self.ambulance = ambulance
        self.traffic = traffic
        self.notify = notify or (lambda alert: None)
        self.responder = responder
        self.trip_id = trip_id
        self.busy_roads = busy_roads or (lambda: set())
        self.road_name = road_name or (lambda road_id: None)
        self.stations = list(stations)
        self.urgent = urgent or (lambda: False)
        self._stopped_checks = deque(maxlen=STALL_WINDOW)
        self._blockages = 0

        self.stretches = [
            {
                **stretch,
                "state": "clear",
                "jam_since": None,
                "clear_since": None,
                "stopped_share": 0.0,
                "speed_ratio": 1.0,
            }
            for stretch in stretches
        ]
        self.alerts = {}          # stretch number -> its latest alert
        self._incidents = {}      # road id -> time an accident was reported
        self.events = deque(maxlen=20)
        self._last_check = -CHECK_EVERY

        # Metres from the start of the route to the start of each road.
        self._roads = ambulance.route()
        self._offsets, total = [], 0.0
        for road in self._roads:
            self._offsets.append(total)
            total += traffic.lane_length(f"{road}_0")

    # -------------------------------------------------------------

    def step(self, now):
        """Call every simulation step."""

        if not self.ambulance.is_on_road():
            return
        self._follow_units(now)

        if now - self._last_check < CHECK_EVERY:
            return
        self._last_check = now

        position = self._ambulance_meters()
        speed = max(self.ambulance.speed(), MIN_SPEED_FOR_TIMING)

        # Road the ambulance is on (or entering, inside a junction).
        here = self.ambulance.route_index()
        if self.ambulance.road_id().startswith(":"):
            here += 1

        for stretch in self.stretches:
            start = self._offsets[stretch["start_index"]]
            last = stretch["end_index"]
            end = self._offsets[last] + self.traffic.lane_length(
                f"{self._roads[last]}_0"
            )
            alert = self.alerts.get(stretch["number"])
            active = alert if alert and alert["status"] in ACTIVE else None

            if position > end:
                stretch["state"] = "passed"
                if active:
                    self._close(active, now, "PASSED", "the ambulance got through")
                continue

            distance = max(0.0, start - position)

            # Officers hold the road until the ambulance is through; they
            # only leave early when it is still far off (if the road jams
            # again, a new alert brings police back in time).
            if (
                active
                and active["status"] == "ON_SCENE"
                and now - active["on_scene_at"] > MAX_CLEARING_SECONDS
                and distance / speed > ALERT_HORIZON_SECONDS
            ):
                self._close(active, now, "CANCELLED", "the officers stood down")
                continue

            # A reported accident on this stretch, ahead of the ambulance:
            # alert at once, however far ahead (police need the time).
            crash = next(
                (
                    index
                    for index in range(max(stretch["start_index"], here), last + 1)
                    if self._roads[index] in self._incidents
                ),
                None,
            )
            if crash is not None and not active:
                self._measure(stretch, max(stretch["start_index"], here), now)
                if stretch["stations"] and self._roads[crash] not in self.busy_roads():
                    station, police_eta, roads = self._best_station(stretch, crash)
                    ambulance_eta = max(0.0, self._offsets[crash] - position) / speed
                    self._alert(stretch, crash, station, police_eta, roads,
                                now, ambulance_eta, cause="accident")
                continue

            if distance > LOOKAHEAD_METERS:
                continue

            # Only the part of the stretch still ahead of the ambulance.
            worst_road = self._measure(stretch, max(stretch["start_index"], here), now)

            if active:
                if (
                    # An accident does not clear by itself: officers
                    # stay on it until the ambulance is through.
                    active.get("cause") != "accident"
                    and active["status"] != "ON_SCENE"
                    and stretch["state"] == "clear"
                    and now - stretch["clear_since"] >= STAND_DOWN_SECONDS
                ):
                    self._close(active, now, "CANCELLED",
                                "the traffic cleared by itself")
                continue

            if (
                stretch["state"] != "jammed"
                or now - stretch["jam_since"] < CONFIRM_SECONDS
                # Vehicles must really be stopped there right now.
                or stretch["stopped_share"] < CLEAR_STOPPED_SHARE
                or not stretch["stations"]
                or (alert and now - alert["closed_at"] < REALERT_SECONDS)
                or self._roads[worst_road] in self.busy_roads()
            ):
                continue

            # When must the police leave? Alert once the ambulance is
            # within the horizon, or earlier if the police need longer.
            jam_distance = max(0.0, self._offsets[worst_road] - position)
            ambulance_eta = jam_distance / speed
            station, police_eta, roads = self._best_station(stretch, worst_road)
            if ambulance_eta <= max(ALERT_HORIZON_SECONDS, police_eta + CLEAR_SECONDS):
                self._alert(stretch, worst_road, station, police_eta, roads,
                            now, ambulance_eta)

        self._check_stall(now, here)

    # -------------------------------------------------------------
    # The ambulance is stuck: police to the front of its queue
    # -------------------------------------------------------------

    def _check_stall(self, now, here):
        # Standing at the patient (a planned stop) is not being stuck.
        self._stopped_checks.append(
            self.ambulance.speed() <= 0.5 and not self.ambulance.at_stop()
        )
        if self.urgent():
            stalled = sum(list(self._stopped_checks)[-3:]) >= URGENT_STALL_CHECKS
        else:
            stalled = sum(self._stopped_checks) >= STALL_CHECKS

        # Blockages the ambulance got past, or that cleared by themselves.
        for alert in list(self.alerts.values()):
            if alert.get("kind") != "blockage" or alert["status"] not in ACTIVE:
                continue
            if here > alert["route_index"]:
                self._close(alert, now, "PASSED", "the ambulance got through")
            elif (
                alert["status"] != "ON_SCENE"
                and not stalled
                and now - alert["alerted_at"] >= STAND_DOWN_SECONDS
            ):
                self._close(alert, now, "CANCELLED", "the traffic cleared by itself")

        if not stalled:
            return
        self._dispatch_blockage(now, here)

    def call_police(self, now, called_by="crew"):
        """The crew (or the control room) calls police now: the station
        that can reach the front of the queue fastest is sent, as when
        the ambulance is found stuck. Returns {"called": bool, "message"}."""
        here = self.ambulance.route_index()
        if self.ambulance.road_id().startswith(":"):
            here += 1
        return self._dispatch_blockage(now, here, called_by=called_by)

    def _dispatch_blockage(self, now, here, called_by=None):
        """Send police to the front of the queue holding the ambulance up.
        Returns {"called": bool, "message"} (why not, when not)."""

        if not self.stations or not self.responder:
            return {"called": False, "message": "No police stations in this area."}

        front = self.traffic.queue_front(self.ambulance.vehicle_id)
        if front is not None:
            roads = [road for road in (front["road_id"], front["next_road_id"]) if road]
        elif called_by:
            # Called by people: the road ahead of the ambulance.
            front = {"kind": "called", "queue": 0.0}
            roads = self._roads[here:here + 2]
        else:
            return {"called": False, "message": "Nothing is holding the ambulance up."}
        if not roads:
            return {"called": False, "message": "No road ahead to send police to."}
        x, y = self.traffic.lane_shape(f"{roads[0]}_0")[-1]
        point = self.traffic.to_latlon(x, y)

        # Police already on (or heading to) this blockage?
        for alert in self.alerts.values():
            if alert["status"] in ACTIVE and (
                set(alert["control_roads"]) & set(roads)
                or _meters(alert["latitude"], alert["longitude"],
                           point["latitude"], point["longitude"]) <= SAME_BLOCKAGE_METERS
            ):
                return {
                    "called": False,
                    "message": f"Police from {alert['station']} are already on their way there.",
                }
        if set(roads) & self.busy_roads():
            return {"called": False, "message": "Police are already working on that road."}

        target = roads[0]
        nearest = sorted(
            self.stations,
            key=lambda station: _meters(
                station["latitude"], station["longitude"],
                point["latitude"], point["longitude"],
            ),
        )[:BLOCKAGE_CANDIDATES]
        best = None
        for station in nearest:
            trip = self.responder.find_route(station["road"], target, "police")
            if trip is None:
                continue
            eta = DISPATCH_SECONDS + self._siren_seconds(trip["roads"])
            if best is None or eta < best[1]:
                best = (station, eta, trip["roads"])
        if best is None:
            return {"called": False, "message": "No police station can reach that road."}

        station, police_eta, drive = best
        try:
            route_index = self._roads.index(target, here)
        except ValueError:
            route_index = here  # the front car is on another road
        self._blockages += 1
        road_name = self.road_name(target) or "the junction ahead"
        why = {
            "signal": "road beyond the signal is full",
            "junction": "stuck at a junction",
            "box": "car stuck inside the junction",
            "ambulance": "ambulance waiting at the junction",
        }.get(front["kind"], "traffic not moving")
        if called_by:
            why = f"called by the {called_by}"
        self._alert(
            {
                "number": f"B{self._blockages}",
                "name": road_name,
                "stations": [station],
                "stopped_share": front["queue"],
                "speed_ratio": 0.0,
                "geometry": self._roads_geometry(roads),
            },
            None, station, police_eta, drive, now,
            ambulance_eta=0.0, cause="blockage",
            control_roads=roads, road_id=target, route_index=route_index,
            detail=why,
        )
        return {
            "called": True,
            "message": (
                f"Called {station['name']}: police to {road_name}, "
                f"about {max(1, round(police_eta / 60))} min."
            ),
        }

    def _siren_seconds(self, roads):
        """Police time along these roads with the siren on."""
        seconds = sum(
            self.traffic.lane_length(f"{road}_0")
            / max(self.traffic.lane_speed_limit(f"{road}_0"), 1.0)
            for road in roads
        )
        return seconds * SIREN_FREE_FLOW_FACTOR

    def _roads_geometry(self, roads):
        points = []
        for road in roads:
            for x, y in self.traffic.lane_shape(f"{road}_0"):
                position = self.traffic.to_latlon(x, y)
                points.append([position["latitude"], position["longitude"]])
        return points

    # -------------------------------------------------------------
    # Measuring a stretch
    # -------------------------------------------------------------

    def _ambulance_meters(self):
        index = self.ambulance.route_index()
        if self.ambulance.road_id().startswith(":"):
            return self._offsets[min(index + 1, len(self._offsets) - 1)]
        return self._offsets[index] + self.ambulance.lane_position()

    def _measure(self, stretch, first_index, now):
        """Update the stretch's jammed / clear state from its roads from
        first_index on; returns the route index of the most jammed one."""

        worst_stopped, worst_ratio = 0.0, 1.0
        worst_road, worst_score = first_index, -1.0

        for index in range(first_index, stretch["end_index"] + 1):
            road = self._roads[index]
            lane = f"{road}_0"
            length = max(self.traffic.lane_length(lane), 1.0)
            lanes = max(1, self.traffic.road_lane_count(road))
            stopped = min(1.0, self.traffic.road_halting_count(road)
                          * VEHICLE_SPACE_METERS / lanes / length)
            ratio = 1.0
            if self.traffic.road_vehicle_count(road) > 0:
                ratio = min(1.0, self.traffic.road_mean_speed(road)
                            / max(self.traffic.lane_speed_limit(lane), 0.1))

            worst_stopped = max(worst_stopped, stopped)
            worst_ratio = min(worst_ratio, ratio)
            score = stopped + (1.0 - ratio)
            if score > worst_score:
                worst_road, worst_score = index, score

        if stretch["state"] == "jammed":
            jammed = (
                worst_stopped >= CLEAR_STOPPED_SHARE
                or worst_ratio <= JAM_SPEED_RATIO
            )
        else:
            jammed = worst_stopped >= JAM_STOPPED_SHARE or (
                worst_ratio <= JAM_SPEED_RATIO
                and worst_stopped >= CLEAR_STOPPED_SHARE
            )

        stretch["state"] = "jammed" if jammed else "clear"
        stretch["stopped_share"] = round(worst_stopped, 2)
        stretch["speed_ratio"] = round(worst_ratio, 2)
        if jammed:
            stretch["clear_since"] = None
            stretch["jam_since"] = stretch["jam_since"] or now
        else:
            stretch["jam_since"] = None
            stretch["clear_since"] = stretch["clear_since"] or now
        return worst_road

    def _best_station(self, stretch, worst_road):
        """(station, police seconds to the jam, roads to drive or None).
        With a responder: live travel times to the jammed road; else the
        planner's driving times to the stretch."""

        best = None
        for station in stretch["stations"]:
            seconds, roads = station["drive_seconds"], None
            if self.responder:
                trip = self.responder.find_route(
                    station["road"], self._roads[worst_road], "police"
                )
                if trip:
                    seconds, roads = trip["seconds"], trip["roads"]
            eta = DISPATCH_SECONDS + seconds * SIREN_FACTOR
            if best is None or eta < best[1]:
                best = (station, eta, roads)
        return best

    # -------------------------------------------------------------
    # Alerts
    # -------------------------------------------------------------

    def _alert(self, stretch, worst_road, station, police_eta, roads, now,
               ambulance_eta, cause="traffic", control_roads=None,
               road_id=None, route_index=None, detail=None):
        """control_roads: the roads whose junctions the officers control
        (default: the stretch's); road_id: where the police drive to."""
        road = road_id or self._roads[worst_road]
        x, y = self.traffic.lane_shape(f"{road}_0")[0]
        road_name = self.road_name(road) or stretch["name"]
        if control_roads is None:
            control_roads = self._roads[stretch["start_index"]:stretch["end_index"] + 1]

        alert = {
            "alert_id": f"{self.trip_id}-S{stretch['number']}-{int(now)}",
            "status": "ALERTED",
            # "accident" (reported crash), "traffic" (jam measured on a
            # stretch without signals) or "blockage" (the ambulance stuck)
            "cause": cause,
            "kind": "blockage" if cause == "blockage" else "stretch",
            "control_roads": list(control_roads),
            "route_index": route_index,
            "detail": detail,
            "station": station["name"],
            "stretch": stretch["number"],
            "road": road_name,
            "road_id": road,
            **self.traffic.to_latlon(x, y),
            "stopped_share": stretch["stopped_share"],
            "speed_ratio": stretch["speed_ratio"],
            "ambulance_eta_seconds": round(ambulance_eta),
            "police_eta_seconds": round(police_eta),
            "late": cause != "blockage" and police_eta + CLEAR_SECONDS > ambulance_eta,
            "alerted_at": now,
            "unit_id": None,
            "unit_latitude": None,
            "unit_longitude": None,
            "vehicles_waved": 0,
            "call": None,          # filled in by the notifier
            # The roads the officers will control, for the map.
            "zone": stretch.get("geometry", []),
            "stopped_on_arrival": None,
            "stopped_after": None,
        }
        self.alerts[stretch["number"]] = alert
        if cause == "blockage":
            self._event(
                f"Ambulance stuck at {road_name}. "
                f"Police from {station['name']} coming, about {max(1, round(police_eta / 60))} min."
            )
        else:
            self._event(
                f"{'Accident' if cause == 'accident' else 'Jam'} on {road_name}. "
                f"Called police from {station['name']} (about {max(1, round(police_eta / 60))} min away)."
                + (" Police may be late." if alert["late"] else "")
            )
        self.notify(dict(alert))

        # In the simulation: a police car drives there with its siren on.
        if self.responder and roads:
            unit_id = f"police_{alert['alert_id']}"
            if self.responder.send_unit(unit_id, roads):
                alert["unit_id"] = unit_id
                alert["status"] = "EN_ROUTE"
                self.notify(dict(alert))

    def _follow_units(self, now):
        for alert in self.alerts.values():
            if alert["status"] == "EN_ROUTE":
                state = self.responder.unit_state(alert["unit_id"], alert["road_id"])
                # A road blocked right across cannot be driven through,
                # even with a siren: officers park at the back of the
                # queue and walk. Reaching the stretch, or getting within
                # WALK_METERS of the jam, counts as arrived.
                if state and not state.get("pending") and (
                    state.get("road_id") in alert["control_roads"]
                    or _meters(state["latitude"], state["longitude"],
                               alert["latitude"], alert["longitude"]) <= WALK_METERS
                ):
                    state = {**state, "arrived": True}
                if state and not state["arrived"]:
                    if not state.get("pending"):
                        alert["unit_latitude"] = state["latitude"]
                        alert["unit_longitude"] = state["longitude"]
                    continue
                # Arrived (or left the simulation): officers get to work.
                if state:
                    alert["unit_latitude"] = state["latitude"]
                    alert["unit_longitude"] = state["longitude"]
                self.responder.remove_unit(alert["unit_id"])
                alert["status"] = "ON_SCENE"
                alert["on_scene_at"] = now
                alert["stopped_on_arrival"] = worst_stopped_share(
                    self.traffic, alert["control_roads"]
                )
                self._event(f"Police clearing {alert['road']}.")
                self.notify(dict(alert))

            if alert["status"] == "ON_SCENE":
                alert["vehicles_waved"] += self.responder.control_junctions(
                    alert["control_roads"], self._roads
                )

    def _close(self, alert, now, status, why):
        if alert["status"] == "EN_ROUTE" and self.responder:
            self.responder.remove_unit(alert["unit_id"])
        alert["status"] = status
        alert["closed_at"] = now
        alert["closed_reason"] = why
        if alert.get("on_scene_at") is not None:
            # How the road looks now the officers are done.
            alert["stopped_after"] = worst_stopped_share(
                self.traffic, alert["control_roads"]
            )
            alert["on_scene_seconds"] = round(now - alert["on_scene_at"])

        # Officers stop directing traffic once nobody is on scene.
        if self.responder and not any(
            other["status"] == "ON_SCENE" for other in self.alerts.values()
        ):
            self.responder.release_junctions()

        self._event(f"Police done at {alert['road']}: {why}.")
        self.notify(dict(alert))

    def close(self, now, status="PASSED", why="the ambulance reached the hospital"):
        """Trip over (or a new route): every unit stands down."""
        for alert in list(self.alerts.values()):
            if alert["status"] in ACTIVE:
                self._close(alert, now, status, why)

    def active_roads(self):
        """Roads police from this watch are clearing or heading to."""
        roads = set()
        for alert in self.alerts.values():
            if alert["status"] in ACTIVE:
                roads.update(alert["control_roads"])
        return roads

    def report_incident(self, road_id, now):
        """An accident was reported on this road: alert its police at the
        next check, without waiting for a queue to build up."""
        self._incidents[road_id] = now
        self._last_check = -CHECK_EVERY

    def incident_road(self, min_meters=300):
        """
        For the "simulate accident" demo: a road without signals ahead of
        the ambulance, far enough that the police can get there and clear
        it before the ambulance arrives (police drive + clearing + margin
        < the ambulance's typical time to get there). Returns (road id,
        stretch name, police can make it: bool), or None. If no stretch
        is far enough, the farthest one.
        """

        position = self._ambulance_meters()
        farthest = None
        for stretch in self.stretches:
            if not stretch["stations"]:
                continue
            indices = range(stretch["start_index"], stretch["end_index"] + 1)
            # A road long enough to hold the crashed vehicles.
            usable = [
                index for index in indices
                if self._offsets[index] - position >= min_meters
                and self.traffic.lane_length(f"{self._roads[index]}_0") >= 15
            ]
            if not usable:
                continue
            index = usable[0]
            police_needs = (
                DISPATCH_SECONDS
                + min(s["drive_seconds"] for s in stretch["stations"]) * SIREN_FACTOR
                + INCIDENT_CLEAR_SECONDS + INCIDENT_MARGIN_SECONDS
            )
            ambulance_needs = (self._offsets[index] - position) / TYPICAL_AMBULANCE_SPEED
            if ambulance_needs >= police_needs:
                return self._roads[index], stretch["name"], True
            farthest = (self._roads[index], stretch["name"], False)
        return farthest

    def _event(self, message):
        logger.info(message)
        self.events.append(message)

    def summary(self):
        """State for the dashboard and the AI agent."""
        return {
            "stretches": [
                {key: stretch.get(key) for key in (
                    "number", "name", "latitude", "longitude", "length_meters",
                    "state", "stopped_share", "speed_ratio", "geometry",
                )}
                for stretch in self.stretches
            ],
            "alerts": list(self.alerts.values()),
            "events": list(self.events),
        }
