"""
What happened on one trip, for the admin trip page and the problem
junction report (backend/services/admin_service.py).

The simulation loop calls step() every simulated second and add() for
signals, police, accidents and re-routes; everything is kept in memory
and saved once, when the trip ends (finish_request), so the simulation
never waits for the database.
"""

from simulation.sumo import route_planner

TRACK_EVERY_SECONDS = 5      # one track point per this many seconds
STILL_SPEED = 0.5            # m/s: standing still
MIN_STOP_SECONDS = 10        # shorter stops are not listed


def _point(latitude, longitude):
    return [round(latitude, 6), round(longitude, 6)]


class TripRecorder:
    def __init__(self, dispatch_time):
        self.dispatch_time = dispatch_time
        self.last_time = dispatch_time   # the latest simulated second seen
        self.route_geometry = None     # the first (planned) route
        self.track = []                # [[lat, lon, seconds, speed], ...]
        self.events = []
        self._last_track = None
        self._stop = None              # the stop going on now

    def seconds(self, now):
        return round(max(0.0, now - self.dispatch_time), 1)

    def set_route(self, geometry):
        if self.route_geometry is None and geometry:
            self.route_geometry = [_point(*point) for point in geometry]

    def add(self, now, kind, title, **fields):
        """An event: kind (TripEvent kinds), a short title, and any of
        detail, road_name, junction_id, junction_name, junction_signal,
        latitude, longitude, duration_seconds, data."""
        self.events.append({"seconds": self.seconds(now), "kind": kind, "title": title, **fields})

    def step(self, now, snapshot):
        """The ambulance this second: {"latitude", "longitude", "speed",
        "road_id"} (corridor/feed.py ambulance_snapshot)."""
        self.last_time = now
        latitude, longitude = snapshot.get("latitude"), snapshot.get("longitude")
        if latitude is None or longitude is None:
            return
        speed = snapshot.get("speed") or 0.0

        if self._last_track is None or now - self._last_track >= TRACK_EVERY_SECONDS:
            self.track.append([*_point(latitude, longitude), self.seconds(now), round(speed, 1)])
            self._last_track = now

        if speed <= STILL_SPEED:
            if self._stop is None:
                self._stop = {
                    "since": now,
                    "latitude": latitude,
                    "longitude": longitude,
                    "road_id": snapshot.get("road_id") or "",
                }
        else:
            self._end_stop(now)

    def finish(self, now):
        """The trip ended: close a stop still going on."""
        self._end_stop(now)

    def _end_stop(self, now):
        stop, self._stop = self._stop, None
        if stop is None or now - stop["since"] < MIN_STOP_SECONDS:
            return
        duration = now - stop["since"]
        road = stop["road_id"]
        junction = route_planner.junction_info(road) if road else None
        road_name = None if road.startswith(":") else route_planner.road_name(road)
        minutes, seconds = divmod(round(duration), 60)
        self.events.append({
            "seconds": self.seconds(stop["since"]),
            "kind": "STOP",
            "title": f"Stood still {minutes} min {seconds:02d} s" if minutes else f"Stood still {seconds} s",
            "detail": (
                f"Waiting to get through {junction['name']}"
                if junction and junction["name"] else None
            ),
            "road_name": road_name,
            "junction_id": junction["id"] if junction else None,
            "junction_name": junction["name"] if junction else None,
            "junction_signal": junction["signal"] if junction else None,
            "latitude": stop["latitude"],
            "longitude": stop["longitude"],
            "duration_seconds": round(duration, 1),
        })
