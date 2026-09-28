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

Alert: ALERTED -> EN_ROUTE -> ON_SCENE -> PASSED, or CANCELLED when the
jam clears by itself first (or the officers stand down).

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
MAX_CLEARING_SECONDS = 240      # officers stay at most this long

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
                 road_name=None):
        """
        stretches: from the route planner (indices into ambulance.route())
        notify(alert): called for every new alert and status change
        responder: optional Responder (corridor/interfaces.py) that drives
            a police car there and lets the officers direct traffic
        busy_roads(): roads other police are already clearing (the
            deadlock response), so two cars are not sent to one jam
        road_name(road_id): street name or None
        """

        self.ambulance = ambulance
        self.traffic = traffic
        self.notify = notify or (lambda alert: None)
        self.responder = responder
        self.trip_id = trip_id
        self.busy_roads = busy_roads or (lambda: set())
        self.road_name = road_name or (lambda road_id: None)

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
               ambulance_eta, cause="traffic"):
        road = self._roads[worst_road]
        x, y = self.traffic.lane_shape(f"{road}_0")[0]
        road_name = self.road_name(road) or stretch["name"]

        alert = {
            "alert_id": f"{self.trip_id}-S{stretch['number']}-{int(now)}",
            "status": "ALERTED",
            # "accident" (reported crash) or "traffic" (jam measured)
            "cause": cause,
            "station": station["name"],
            "stretch": stretch["number"],
            "road": road_name,
            "road_id": road,
            **self.traffic.to_latlon(x, y),
            "stopped_share": stretch["stopped_share"],
            "speed_ratio": stretch["speed_ratio"],
            "ambulance_eta_seconds": round(ambulance_eta),
            "police_eta_seconds": round(police_eta),
            "late": police_eta + CLEAR_SECONDS > ambulance_eta,
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
        self._event(
            (
                f"Accident reported on {road_name}. Alert sent at once to "
                if cause == "accident"
                else f"Heavy traffic on {road_name} (no signal there). Alert sent to "
            )
            + f"{station['name']}: ambulance in ~{max(1, round(ambulance_eta / 60))} "
            f"min, police can be there in ~{max(1, round(police_eta / 60))} min."
            + (" Police may arrive after the ambulance." if alert["late"] else "")
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
                    state.get("road_id") in self._stretch_roads(alert["stretch"])
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
                    self.traffic, self._stretch_roads(alert["stretch"])
                )
                self._event(
                    f"Police from {alert['station']} reached {alert['road']} "
                    f"after {now - alert['alerted_at']:.0f} s and are clearing it."
                )
                self.notify(dict(alert))

            if alert["status"] == "ON_SCENE":
                stretch = self._stretch(alert["stretch"])
                roads = self._roads[stretch["start_index"]:stretch["end_index"] + 1]
                alert["vehicles_waved"] += self.responder.control_junctions(
                    roads, self._roads
                )
                if now - alert["on_scene_at"] > MAX_CLEARING_SECONDS:
                    self._close(alert, now, "CANCELLED", "the officers stood down")

    def _close(self, alert, now, status, why):
        if alert["status"] == "EN_ROUTE" and self.responder:
            self.responder.remove_unit(alert["unit_id"])
        alert["status"] = status
        alert["closed_at"] = now
        alert["closed_reason"] = why
        if alert.get("on_scene_at") is not None:
            # How the road looks now the officers are done.
            alert["stopped_after"] = worst_stopped_share(
                self.traffic, self._stretch_roads(alert["stretch"])
            )
            alert["on_scene_seconds"] = round(now - alert["on_scene_at"])

        # Officers stop directing traffic once nobody is on scene.
        if self.responder and not any(
            other["status"] == "ON_SCENE" for other in self.alerts.values()
        ):
            self.responder.release_junctions()

        self._event(f"Alert for {alert['road']} closed: {why}.")
        self.notify(dict(alert))

    def close(self, now):
        """Trip over: every unit stands down."""
        for alert in list(self.alerts.values()):
            if alert["status"] in ACTIVE:
                self._close(alert, now, "PASSED", "the ambulance reached the hospital")

    def active_roads(self):
        """Roads police from this watch are clearing or heading to."""
        roads = set()
        for alert in self.alerts.values():
            if alert["status"] in ACTIVE:
                stretch = self._stretch(alert["stretch"])
                roads.update(
                    self._roads[stretch["start_index"]:stretch["end_index"] + 1]
                )
        return roads

    def _stretch_roads(self, number):
        stretch = self._stretch(number)
        return self._roads[stretch["start_index"]:stretch["end_index"] + 1]

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

    def _stretch(self, number):
        return next(s for s in self.stretches if s["number"] == number)

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
