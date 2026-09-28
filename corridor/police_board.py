"""
Live board of the police stations for the dashboard: what each station is
doing right now, and how fast its officers could reach the ambulance.

    status     available / alerted / unit_en_route / on_scene, from the
               police alerts (corridor/police_watch.py) and the deadlock
               response (corridor/response.py)
    reach      seconds for officers to reach the ambulance's road now,
               with live travel times (refreshed every UPDATE_EVERY s)
    nearby     the NEARBY_COUNT stations that could reach it first

Uses only the connectors in corridor/interfaces.py.
"""

from corridor.police_watch import DISPATCH_SECONDS, SIREN_FACTOR

UPDATE_EVERY = 10     # seconds between drive-time refreshes
NEARBY_COUNT = 3

STATUS_TEXT = {
    "available": "Available",
    "alerted": "Alert received",
    "unit_en_route": "Unit on the way",
    "on_scene": "Officers directing traffic",
}

# Police alert status -> station status.
_ALERT_STATUS = {
    "ALERTED": "alerted",
    "EN_ROUTE": "unit_en_route",
    "ON_SCENE": "on_scene",
}

# Deadlock response police stage -> station status.
_RESPONSE_STAGE = {"en_route": "unit_en_route", "clearing": "on_scene"}


class PoliceBoard:
    def __init__(self, stations, ambulance, responder):
        """stations: route_planner.police_stations()."""

        self.stations = stations
        self.ambulance = ambulance
        self.responder = responder
        self._reach = {}              # station name -> seconds, or None
        self._last_update = -UPDATE_EVERY
        self._handled = {}            # station name -> alerts completed

    def update(self, now, alerts=(), response_police=None):
        """Call every step. alerts: police watch alerts; response_police:
        the deadlock response's police unit (or None)."""

        self._alerts = list(alerts)
        self._response_police = response_police

        if now - self._last_update < UPDATE_EVERY or not self.ambulance.is_on_road():
            return
        self._last_update = now

        target = self._ambulance_road()
        if target is None:
            return
        for station in self.stations:
            trip = self.responder.find_route(station["road"], target, "police")
            self._reach[station["name"]] = (
                round(DISPATCH_SECONDS + trip["seconds"] * SIREN_FACTOR)
                if trip else None
            )

    def _ambulance_road(self):
        road = self.ambulance.road_id()
        if not road.startswith(":"):
            return road
        route = self.ambulance.route()
        index = self.ambulance.route_index() + 1
        return route[index] if index < len(route) else None

    def _task(self, name):
        """(status, road it is working on) for a station."""

        for alert in reversed(self._alerts):
            if alert["station"] == name and alert["status"] in _ALERT_STATUS:
                return _ALERT_STATUS[alert["status"]], alert["road"]
        police = self._response_police
        if police and police["station"] == name and police["stage"] in _RESPONSE_STAGE:
            return _RESPONSE_STAGE[police["stage"]], police.get("road")
        return "available", None

    def summary(self):
        stations = []
        for station in self.stations:
            status, road = self._task(station["name"])
            handled = sum(
                1 for alert in getattr(self, "_alerts", [])
                if alert["station"] == station["name"]
                and alert["status"] == "PASSED" and alert.get("on_scene_at")
            )
            stations.append({
                "name": station["name"],
                "status": status,
                "status_text": STATUS_TEXT[status],
                "task_road": road,
                "reach_seconds": self._reach.get(station["name"]),
                "jams_cleared": handled,
            })

        reachable = [s for s in stations if s["reach_seconds"] is not None]
        reachable.sort(key=lambda s: s["reach_seconds"])
        return {
            "stations": stations,
            "nearby": [s["name"] for s in reachable[:NEARBY_COUNT]],
        }
