"""
How long each road takes an ambulance right now, for route planning.

Like a navigation app in live traffic, the planner picks the route with
the shortest travel time today, not the one with the highest speed
limits. Each road's time (seconds) = its length / the speed traffic is
really moving on it now:

    AI forecast          (simulation running, model trained) the speed
                         ai/road_speed.py predicts for the moment the
                         ambulance reaches the road, like a navigation
                         app; refreshed every FORECAST_EVERY simulated s
    simulation running   the average speed of the cars on the road (a
                         jam of stopped cars is very slow), refreshed
                         every ROAD_STATS_EVERY simulated seconds
    otherwise            TomTom's live speed on that road (main roads)
    no live data         the speed limit (an empty road)

Written from the simulation thread and the live traffic fetch; read by
the planner from any thread (each table is replaced whole, never changed
in place). The trip records' earlier stuck spots are only used to
explain a route ("ambulances got stuck here before"), not to time it.
"""

import logging
import math
import threading
import time

logger = logging.getLogger(__name__)

# Simulated seconds between two counts of the cars on every road.
ROAD_STATS_EVERY = 10
# Car counts older than this (real seconds) are no longer "live".
ROAD_STATS_MAX_AGE = 120
# Simulated seconds between two AI forecasts (made in the background).
FORECAST_EVERY = 30

# A car slower than this (m/s) is standing still.
STOPPED_SPEED = 0.5
# Road space one stopped car takes (m), as corridor/police_watch.py.
VEHICLE_SPACE_METERS = 7.5
# A road whose lanes are at least this full of stopped cars is shown as
# "likely to get stuck" when a route avoids it.
STUCK_SHARE = 0.5
# The slowest speed a road is timed at (m/s, 3.6 km/h): even a jammed
# road moves a little, and the ambulance's siren helps.
SLOWEST_SPEED = 1.0

# Earlier stuck spots (trip records), for explaining a route: this far
# from a road counts (m); refreshed at most this often (s).
HISTORY_METERS = 60
HISTORY_REFRESH_SECONDS = 600

_lock = threading.Lock()
_road_stats = {}        # road id -> {"vehicles", "stopped", "speed"}
_road_stats_at = 0.0    # time.time() of the last count
_tomtom = {}            # road id -> live speed / free-flow speed (0-1)
_history = {}           # road id -> earlier trips stuck near it
_history_at = 0.0
_last_count = None      # simulated time of the last count
_forecast = {}          # road id -> predicted speed share at each horizon
_forecast_at = 0.0
_last_forecast = None   # simulated time of the last forecast started
_forecasting = threading.Event()


# -------------------------------------------------------------
# Feeding it
# -------------------------------------------------------------

def count_vehicles(vehicles, now, level="normal"):
    """From the simulation loop, every step: count the cars on every
    road (vehicles: [{"road_id", "speed"}], ambulances excluded) every
    ROAD_STATS_EVERY simulated seconds, and start an AI forecast in the
    background every FORECAST_EVERY (level: the live traffic level)."""
    global _road_stats, _road_stats_at, _last_count, _last_forecast

    if (
        (_last_forecast is None or now - _last_forecast >= FORECAST_EVERY)
        and not _forecasting.is_set()
    ):
        _last_forecast = now
        _forecasting.set()
        threading.Thread(
            target=_make_forecast, args=(list(vehicles), level), daemon=True
        ).start()

    if _last_count is not None and now - _last_count < ROAD_STATS_EVERY:
        return
    _last_count = now

    stats = {}
    for vehicle in vehicles:
        road = vehicle.get("road_id") or ""
        if not road or road.startswith(":"):
            continue
        entry = stats.setdefault(road, {"vehicles": 0, "stopped": 0, "speed_sum": 0.0})
        entry["vehicles"] += 1
        entry["speed_sum"] += vehicle["speed"]
        if vehicle["speed"] < STOPPED_SPEED:
            entry["stopped"] += 1

    for entry in stats.values():
        entry["speed"] = entry.pop("speed_sum") / entry["vehicles"]

    _road_stats, _road_stats_at = stats, time.time()


def _make_forecast(vehicles, level):
    global _forecast, _forecast_at
    try:
        from ai import road_speed
        table = road_speed.forecast(vehicles, level)
        if table is not None:
            _forecast, _forecast_at = table, time.time()
    except Exception:
        logger.exception("AI traffic forecast failed")
    finally:
        _forecasting.clear()


def clear_vehicles():
    """The simulation stopped: its car counts are no longer live."""
    global _road_stats, _road_stats_at, _last_count, _forecast, _forecast_at, _last_forecast
    _road_stats, _road_stats_at, _last_count = {}, 0.0, None
    _forecast, _forecast_at, _last_forecast = {}, 0.0, None


def set_tomtom(snapshot, net):
    """A new TomTom snapshot (simulation/live_traffic.py): each stretch's
    live speed share, on the simulated roads underneath it."""
    global _tomtom

    shares = {}
    for segment in snapshot.get("segments", []):
        free = segment.get("free_kmh") or 0
        if free <= 0:
            continue
        share = 0.0 if segment.get("closure") else min(1.0, segment["current_kmh"] / free)
        for latitude, longitude in segment.get("coordinates", [])[::2]:
            x, y = net.convertLonLat2XY(longitude, latitude)
            for edge, _ in _neighbours(net, x, y, 15):
                road = edge.getID()
                shares[road] = min(shares.get(road, 1.0), share)
    _tomtom = shares


def _neighbours(net, x, y, radius):
    """Roads near a point (the planner's lock: the road index is not
    safe to search from two threads at once)."""
    from simulation.sumo.route_planner import _SEARCH_LOCK
    with _SEARCH_LOCK:
        return net.getNeighboringEdges(x, y, radius, includeJunctions=False)


def _load_history(net):
    """Earlier stuck spots from the trip records (at most every
    HISTORY_REFRESH_SECONDS); no database: no history."""
    global _history, _history_at

    if time.time() - _history_at < HISTORY_REFRESH_SECONDS:
        return
    with _lock:
        if time.time() - _history_at < HISTORY_REFRESH_SECONDS:
            return
        _history_at = time.time()
        try:
            from backend.database import SessionLocal
            from backend.services.admin_service import stuck_spots

            db = SessionLocal()
            try:
                spots = stuck_spots(db, days=30, limit=50)
            finally:
                db.close()
        except Exception as error:
            logger.info("No stuck-spot history for routing: %s", error)
            return

        trips = {}
        for spot in spots:
            x, y = net.convertLonLat2XY(spot["longitude"], spot["latitude"])
            for edge, _ in _neighbours(net, x, y, HISTORY_METERS):
                road = edge.getID()
                trips[road] = trips.get(road, 0) + spot["trips"]
        _history = trips


# -------------------------------------------------------------
# Reading it
# -------------------------------------------------------------

def basis():
    """What the costs are built from now: "forecast" (AI), "simulation",
    "tomtom" or "speed limits"."""
    if _road_stats and time.time() - _road_stats_at < ROAD_STATS_MAX_AGE:
        if _forecast and time.time() - _forecast_at < ROAD_STATS_MAX_AGE:
            return "forecast"
        return "simulation"
    if _tomtom:
        return "tomtom"
    return "speed limits"


def _share_at(now_share, predicted, after):
    """Speed share `after` seconds from now: from now to the first
    forecast horizon, then between horizons (straight lines), then the
    last one."""
    from ai.road_speed import HORIZONS
    points = [(0, now_share)] + list(zip(HORIZONS, predicted))
    for (t0, s0), (t1, s1) in zip(points, points[1:]):
        if after <= t1:
            return s0 + (s1 - s0) * (after - t0) / (t1 - t0)
    return points[-1][1]


def cost_function(net):
    """(cost(edge, after=0) -> seconds to drive the road, entered `after`
    seconds from now, from the best live data there is; why(edge) ->
    {"stopped", "vehicles", "stuck", "slow_share", "history"}, to
    explain a route)."""
    _load_history(net)
    source = basis()
    stats = _road_stats if source in ("forecast", "simulation") else {}
    forecast = _forecast if source == "forecast" else {}
    tomtom = _tomtom if source == "tomtom" else {}
    history = _history

    def cost(edge, after=0.0):
        road = edge.getID()
        length = edge.getLength()
        limit = max(edge.getSpeed(), 1.0)

        entry = stats.get(road)
        if road in forecast:
            now_share = min(1.0, entry["speed"] / limit) if entry else 1.0
            speed = limit * _share_at(now_share, forecast[road], after)
        elif entry:
            speed = entry["speed"]  # average of every car on it, stopped ones too
        elif road in tomtom:
            if tomtom[road] <= 0.0:
                return math.inf  # closed (TomTom)
            speed = limit * tomtom[road]
        else:
            speed = limit
        return length / min(limit, max(speed, SLOWEST_SPEED))

    def why(edge):
        road = edge.getID()
        entry = stats.get(road) or {}
        lanes = max(1, edge.getLaneNumber())
        stopped = entry.get("stopped", 0)
        return {
            "stopped": stopped,
            "vehicles": entry.get("vehicles", 0),
            "stuck": stopped * VEHICLE_SPACE_METERS / (lanes * max(edge.getLength(), 1.0)) >= STUCK_SHARE,
            # Live speed as a share of the limit (simulation, else TomTom).
            "slow_share": (
                min(1.0, entry["speed"] / max(edge.getSpeed(), 1.0)) if entry
                else tomtom.get(road)
            ),
            "history": history.get(road, 0) > 0,
        }

    return cost, why
