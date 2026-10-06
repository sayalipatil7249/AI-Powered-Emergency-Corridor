"""
Grievance and performance tracking for the admin dashboard.

    recording   the live simulation records each ambulance request:
                start_request() when it is dispatched, finish_request()
                when it arrives, is stopped or fails
    analytics   kpi_summary(), delay_breakdown(), list_requests(),
                route_summary(), daily_trend() for the dashboard;
                trip_detail() for one trip's page, junction_report() for
                the junctions where ambulances stand still most
    grievances  list / create / update tickets

Recording never lets a database problem stop the simulation: errors are
logged and the trip goes on.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import distinct, func, or_, text

from backend.database import SessionLocal, engine
from backend.models.ambulance_request import (
    DELAY_REASONS,
    REQUEST_STATUSES,
    AmbulanceRequest,
)
from backend.models.grievance import (
    GRIEVANCE_CATEGORIES,
    GRIEVANCE_PRIORITIES,
    GRIEVANCE_ROLES,
    GRIEVANCE_STATUSES,
    Grievance,
)
from backend.models.route_optimization_log import RouteOptimizationLog
from backend.models.trip_event import TripEvent

logger = logging.getLogger(__name__)

# Arriving at most this much later than planned still counts as on time.
ON_TIME_TOLERANCE_SECONDS = 60

# A late trip's delay is put down to traffic when the ambulance stood
# still for at least this share of the delay.
TRAFFIC_SHARE = 0.4


def ensure_schema():
    """Add columns introduced after ambulance_requests was first created
    (create_all makes new tables but never changes existing ones)."""
    columns = {
        "route_geometry": "JSON", "track": "JSON",
        # The 108 timeline
        "unit_id": "VARCHAR(20)", "unit_kind": "VARCHAR(5)",
        "to_patient_seconds": "FLOAT", "scene_seconds": "FLOAT",
        "transport_seconds": "FLOAT", "handover_seconds": "FLOAT",
        "pre_alerted": "BOOLEAN", "diverted": "BOOLEAN",
    }
    with engine.begin() as connection:
        for column, kind in columns.items():
            connection.execute(text(
                f"ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS {column} {kind}"
            ))
        # PostGIS: every event with a position (stops, signals, police)
        # as a map point, filled in from latitude / longitude by the
        # database, with a spatial index (used by stuck_spots()).
        connection.execute(text(
            "ALTER TABLE trip_events ADD COLUMN IF NOT EXISTS location "
            "geography(Point, 4326) GENERATED ALWAYS AS (CASE WHEN latitude IS NOT NULL "
            "AND longitude IS NOT NULL THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)"
            "::geography END) STORED"
        ))
        connection.execute(text(
            "CREATE INDEX IF NOT EXISTS trip_events_location_idx "
            "ON trip_events USING GIST (location)"
        ))


# -------------------------------------------------------------
# Recording (called from the simulation thread)
# -------------------------------------------------------------

def new_request_id(now=None, ambulance_number=None):
    """e.g. "REQ-20261001-143005-A2": several ambulances can be
    dispatched in the same second."""
    request_id = f"REQ-{(now or datetime.now()).strftime('%Y%m%d-%H%M%S')}"
    return f"{request_id}-A{ambulance_number}" if ambulance_number else request_id


def start_request(request_id, ambulance_id, start_name, hospital_name,
                  traffic_level, planned_seconds, distance_meters):
    """The ambulance was dispatched."""
    db = SessionLocal()
    try:
        now = datetime.now()
        db.add(AmbulanceRequest(
            request_id=request_id,
            ambulance_id=ambulance_id,
            start_name=start_name,
            hospital_name=hospital_name,
            status="IN_PROGRESS",
            traffic_level=traffic_level,
            planned_seconds=planned_seconds,
            distance_meters=distance_meters,
            dispatched_at=now,
            source="simulation",
            created_at=now,
            updated_at=now,
        ))
        db.commit()
    except Exception as error:
        db.rollback()
        logger.warning("Could not record request %s: %s", request_id, error)
    finally:
        db.close()


def classify_delay(delay_seconds, stopped_seconds, reroutes, incidents):
    """Why a trip was late, from what happened on the way."""
    if delay_seconds is None or delay_seconds <= ON_TIME_TOLERANCE_SECONDS:
        return "NONE"
    if incidents:
        return "ACCIDENT"
    if reroutes:
        return "BAD_ROUTE"
    if stopped_seconds and stopped_seconds >= TRAFFIC_SHARE * delay_seconds:
        return "TRAFFIC"
    return "OTHER"


# The 108 timeline columns (finish_request's timings).
TIMING_FIELDS = (
    "unit_id", "unit_kind", "to_patient_seconds", "scene_seconds",
    "transport_seconds", "handover_seconds", "pre_alerted", "diverted",
)


def finish_request(request_id, status, response_seconds=None,
                   stopped_seconds=None, stops=None, route=None, notes=None,
                   route_geometry=None, track=None, events=None, timings=None):
    """
    The trip ended. status: COMPLETED, CANCELLED or FAILED.
    route (completed trips): {"optimal_route_used", "signals_total",
    "signals_cleared", "police_alerts", "police_on_scene", "reroutes",
    "incidents"} for the route optimization log.
    route_geometry, track: for the trip page's map.
    events: [{"seconds", "kind", "title", ...TripEvent columns}], in order.
    timings: the 108 timeline ({TIMING_FIELDS}).
    """
    db = SessionLocal()
    try:
        row = (
            db.query(AmbulanceRequest)
            .filter(AmbulanceRequest.request_id == request_id)
            .first()
        )
        if row is None:
            return
        row.status = status
        row.stopped_seconds = stopped_seconds
        row.stops = stops
        if notes:
            row.notes = notes[:500]
        if route_geometry:
            row.route_geometry = route_geometry
        if track:
            row.track = track
        for key, value in (timings or {}).items():
            if key in TIMING_FIELDS:
                setattr(row, key, round(value, 1) if isinstance(value, float) else value)
        for event in events or ():
            db.add(TripEvent(request_id=request_id, **_event_columns(event)))

        if status == "COMPLETED" and response_seconds is not None:
            row.response_seconds = round(response_seconds, 1)
            row.arrived_at = row.dispatched_at + timedelta(seconds=response_seconds)
            delay = None
            if row.planned_seconds is not None:
                delay = max(0.0, response_seconds - row.planned_seconds)
                row.delay_seconds = round(delay, 1)
            if not row.delay_reason_manual:
                row.delay_reason = classify_delay(
                    delay, stopped_seconds,
                    (route or {}).get("reroutes"), (route or {}).get("incidents"),
                )

            if route is not None:
                vs_plan = (
                    row.planned_seconds - response_seconds
                    if row.planned_seconds is not None else None
                )
                db.add(RouteOptimizationLog(
                    request_id=request_id,
                    optimal_route_used=route.get("optimal_route_used", True),
                    signals_total=route.get("signals_total", 0),
                    signals_cleared=route.get("signals_cleared", 0),
                    corridor_cleared=(
                        route.get("signals_total", 0) > 0
                        and route.get("signals_cleared", 0) >= route.get("signals_total", 0)
                    ),
                    police_alerts=route.get("police_alerts", 0),
                    police_on_scene=route.get("police_on_scene", 0),
                    reroutes=route.get("reroutes", 0),
                    incidents=route.get("incidents", 0),
                    fast_arrival=(
                        vs_plan is not None and vs_plan >= -ON_TIME_TOLERANCE_SECONDS
                    ),
                    seconds_vs_plan=round(vs_plan, 1) if vs_plan is not None else None,
                    created_at=datetime.now(),
                ))

        row.updated_at = datetime.now()
        db.commit()
    except Exception as error:
        db.rollback()
        logger.warning("Could not finish request %s: %s", request_id, error)
    finally:
        db.close()


_EVENT_FIELDS = (
    "seconds", "kind", "title", "detail", "road_name", "junction_id",
    "junction_name", "junction_signal", "latitude", "longitude",
    "duration_seconds", "data",
)


def _event_columns(event):
    columns = {key: event.get(key) for key in _EVENT_FIELDS}
    columns["title"] = (columns["title"] or columns["kind"])[:200]
    if columns["detail"]:
        columns["detail"] = columns["detail"][:500]
    for key in ("road_name", "junction_name"):
        if columns[key]:
            columns[key] = columns[key][:200]
    return columns


# -------------------------------------------------------------
# Analytics
# -------------------------------------------------------------

def _since(days):
    return datetime.now() - timedelta(days=days) if days else None


def _requests(db, days):
    query = db.query(AmbulanceRequest)
    since = _since(days)
    if since is not None:
        query = query.filter(AmbulanceRequest.dispatched_at >= since)
    return query


def kpi_summary(db, days=None):
    """Totals, success rate, response and delay times."""
    query = _requests(db, days)
    counts = dict(
        query.with_entities(AmbulanceRequest.status, func.count())
        .group_by(AmbulanceRequest.status)
        .all()
    )
    completed = query.filter(AmbulanceRequest.status == "COMPLETED")
    averages = completed.with_entities(
        func.avg(AmbulanceRequest.response_seconds),
        func.avg(AmbulanceRequest.delay_seconds),
        func.avg(AmbulanceRequest.planned_seconds),
    ).one()
    timeline = completed.with_entities(
        func.avg(AmbulanceRequest.to_patient_seconds),
        func.avg(AmbulanceRequest.scene_seconds),
        func.avg(AmbulanceRequest.transport_seconds),
        func.avg(AmbulanceRequest.handover_seconds),
        func.count(AmbulanceRequest.pre_alerted),
        func.count().filter(AmbulanceRequest.pre_alerted.is_(True)),
        func.count().filter(AmbulanceRequest.diverted.is_(True)),
    ).one()
    on_time = completed.filter(
        or_(
            AmbulanceRequest.delay_seconds.is_(None),
            AmbulanceRequest.delay_seconds <= ON_TIME_TOLERANCE_SECONDS,
        )
    ).count()

    finished = sum(counts.get(status, 0) for status in ("COMPLETED", "CANCELLED", "FAILED"))
    total_completed = counts.get("COMPLETED", 0)

    open_grievances = (
        db.query(Grievance)
        .filter(Grievance.status.in_(("OPEN", "IN_PROGRESS")))
        .count()
    )

    def rounded(value):
        return round(float(value), 1) if value is not None else None

    return {
        "total_requests": sum(counts.values()),
        "by_status": {status: counts.get(status, 0) for status in REQUEST_STATUSES},
        "success_rate": round(total_completed / finished, 3) if finished else None,
        "on_time_rate": round(on_time / total_completed, 3) if total_completed else None,
        "avg_response_seconds": rounded(averages[0]),
        "avg_delay_seconds": rounded(averages[1]),
        "avg_planned_seconds": rounded(averages[2]),
        "open_grievances": open_grievances,
        # The 108 timeline (trips recorded since it was added)
        "avg_to_patient_seconds": rounded(timeline[0]),
        "avg_scene_seconds": rounded(timeline[1]),
        "avg_transport_seconds": rounded(timeline[2]),
        "avg_handover_seconds": rounded(timeline[3]),
        "pre_alert_rate": round(timeline[5] / timeline[4], 3) if timeline[4] else None,
        "diverted": timeline[6],
    }


def delay_breakdown(db, days=None):
    """Late completed trips per delay reason, with their average delay."""
    rows = (
        _requests(db, days)
        .filter(
            AmbulanceRequest.status == "COMPLETED",
            AmbulanceRequest.delay_reason.isnot(None),
            AmbulanceRequest.delay_reason != "NONE",
        )
        .with_entities(
            AmbulanceRequest.delay_reason,
            func.count(),
            func.avg(AmbulanceRequest.delay_seconds),
        )
        .group_by(AmbulanceRequest.delay_reason)
        .all()
    )
    found = {reason: (count, avg) for reason, count, avg in rows}
    return [
        {
            "reason": reason,
            "count": found.get(reason, (0, None))[0],
            "avg_delay_seconds": (
                round(float(found[reason][1]), 1)
                if reason in found and found[reason][1] is not None else None
            ),
        }
        for reason in DELAY_REASONS
        if reason != "NONE"
    ]


def daily_trend(db, days=14):
    """Per day: requests, completed, average response time."""
    day = func.date(AmbulanceRequest.dispatched_at)
    rows = (
        _requests(db, days)
        .with_entities(
            day,
            func.count(),
            func.count().filter(AmbulanceRequest.status == "COMPLETED"),
            func.avg(AmbulanceRequest.response_seconds),
        )
        .group_by(day)
        .order_by(day)
        .all()
    )
    return [
        {
            "date": str(date),
            "requests": total,
            "completed": completed,
            "avg_response_seconds": round(float(avg), 1) if avg is not None else None,
        }
        for date, total, completed, avg in rows
    ]


def request_dict(row, log=None):
    return {
        "request_id": row.request_id,
        "ambulance_id": row.ambulance_id,
        "start_name": row.start_name,
        "hospital_name": row.hospital_name,
        "status": row.status,
        "traffic_level": row.traffic_level,
        "distance_meters": row.distance_meters,
        "planned_seconds": row.planned_seconds,
        "dispatched_at": row.dispatched_at,
        "arrived_at": row.arrived_at,
        "response_seconds": row.response_seconds,
        "delay_seconds": row.delay_seconds,
        "delay_reason": row.delay_reason,
        "delay_reason_manual": row.delay_reason_manual,
        "stopped_seconds": row.stopped_seconds,
        "stops": row.stops,
        "notes": row.notes,
        **{key: getattr(row, key) for key in TIMING_FIELDS},
        "route": None if log is None else {
            "optimal_route_used": log.optimal_route_used,
            "signals_total": log.signals_total,
            "signals_cleared": log.signals_cleared,
            "corridor_cleared": log.corridor_cleared,
            "police_alerts": log.police_alerts,
            "police_on_scene": log.police_on_scene,
            "reroutes": log.reroutes,
            "incidents": log.incidents,
            "fast_arrival": log.fast_arrival,
            "seconds_vs_plan": log.seconds_vs_plan,
        },
    }


def list_requests(db, status=None, fast_only=False, days=None, limit=100):
    """Requests newest first, each with its route optimization log."""
    query = (
        _requests(db, days)
        .outerjoin(
            RouteOptimizationLog,
            RouteOptimizationLog.request_id == AmbulanceRequest.request_id,
        )
        .with_entities(AmbulanceRequest, RouteOptimizationLog)
    )
    if status:
        query = query.filter(AmbulanceRequest.status == status)
    if fast_only:
        query = query.filter(RouteOptimizationLog.fast_arrival.is_(True))
    rows = (
        query.order_by(AmbulanceRequest.dispatched_at.desc())
        .limit(limit)
        .all()
    )
    return [request_dict(request, log) for request, log in rows]


def route_summary(db, days=None):
    """How often the fast-arrival measures were used and worked."""
    query = db.query(RouteOptimizationLog)
    since = _since(days)
    if since is not None:
        query = query.filter(RouteOptimizationLog.created_at >= since)
    total = query.count()
    if not total:
        return {"trips": 0}
    signals = query.with_entities(
        func.sum(RouteOptimizationLog.signals_total),
        func.sum(RouteOptimizationLog.signals_cleared),
        func.sum(RouteOptimizationLog.police_alerts),
        func.sum(RouteOptimizationLog.police_on_scene),
    ).one()
    return {
        "trips": total,
        "fast_arrivals": query.filter(RouteOptimizationLog.fast_arrival.is_(True)).count(),
        "optimal_route_used": query.filter(RouteOptimizationLog.optimal_route_used.is_(True)).count(),
        "corridor_cleared": query.filter(RouteOptimizationLog.corridor_cleared.is_(True)).count(),
        "signals_total": int(signals[0] or 0),
        "signals_cleared": int(signals[1] or 0),
        "police_alerts": int(signals[2] or 0),
        "police_on_scene": int(signals[3] or 0),
    }


def trip_detail(db, request_id):
    """One request with its route log, map data and events, or None."""
    row = (
        db.query(AmbulanceRequest)
        .filter(AmbulanceRequest.request_id == request_id)
        .first()
    )
    if row is None:
        return None
    log = (
        db.query(RouteOptimizationLog)
        .filter(RouteOptimizationLog.request_id == request_id)
        .first()
    )
    events = (
        db.query(TripEvent)
        .filter(TripEvent.request_id == request_id)
        .order_by(TripEvent.seconds, TripEvent.id)
        .all()
    )
    return {
        **request_dict(row, log),
        "route_geometry": row.route_geometry or [],
        "track": row.track or [],
        "events": [
            {key: getattr(event, key) for key in _EVENT_FIELDS} for event in events
        ],
    }


# Stops closer together than this are one stuck spot (metres).
STUCK_SPOT_METERS = 80


def stuck_spots(db, days=None, limit=15):
    """Where ambulances stood still, from the stops of completed trips:
    PostGIS groups stops within STUCK_SPOT_METERS of each other (DBSCAN
    in metres, UTM zone 43N) into spots, worst first:
    [{"latitude", "longitude", "name", "stops", "trips", "stopped_seconds"}]."""

    since = _since(days)
    rows = db.execute(text(f"""
        WITH stops AS (
            SELECT e.request_id, e.location::geometry AS point,
                   COALESCE(e.duration_seconds, 0) AS seconds,
                   COALESCE(e.junction_name, e.road_name) AS name
            FROM trip_events e
            JOIN ambulance_requests r ON r.request_id = e.request_id
            WHERE e.kind = 'STOP' AND e.location IS NOT NULL
              AND r.status = 'COMPLETED'
              {"AND r.dispatched_at >= :since" if since else ""}
        ),
        spots AS (
            SELECT *, ST_ClusterDBSCAN(ST_Transform(point, 32643),
                                       eps := :meters, minpoints := 1) OVER () AS spot
            FROM stops
        )
        SELECT ST_Y(ST_Centroid(ST_Collect(point))) AS latitude,
               ST_X(ST_Centroid(ST_Collect(point))) AS longitude,
               mode() WITHIN GROUP (ORDER BY name) AS name,
               COUNT(*) AS stops,
               COUNT(DISTINCT request_id) AS trips,
               SUM(seconds) AS stopped_seconds
        FROM spots
        GROUP BY spot
        ORDER BY SUM(seconds) DESC
        LIMIT :limit
    """), {"since": since, "meters": STUCK_SPOT_METERS, "limit": limit}).mappings()
    return [
        {
            "latitude": round(row["latitude"], 6),
            "longitude": round(row["longitude"], 6),
            "name": row["name"] or "Unnamed road",
            "stops": row["stops"],
            "trips": row["trips"],
            "stopped_seconds": round(float(row["stopped_seconds"] or 0), 1),
        }
        for row in rows
    ]


def junction_report(db, days=None, limit=20):
    """Junctions where ambulances stood still, worst first (by total
    standing time): stops, trips affected, total / average / longest
    standing time, signal or not, position."""
    query = (
        db.query(
            TripEvent.junction_id,
            func.max(TripEvent.junction_name),
            func.bool_or(TripEvent.junction_signal),
            func.avg(TripEvent.latitude),
            func.avg(TripEvent.longitude),
            func.count(),
            func.count(distinct(TripEvent.request_id)),
            func.sum(TripEvent.duration_seconds),
            func.avg(TripEvent.duration_seconds),
            func.max(TripEvent.duration_seconds),
        )
        .join(AmbulanceRequest, AmbulanceRequest.request_id == TripEvent.request_id)
        .filter(TripEvent.kind == "STOP", TripEvent.junction_id.isnot(None))
    )
    since = _since(days)
    if since is not None:
        query = query.filter(AmbulanceRequest.dispatched_at >= since)
    rows = (
        query.group_by(TripEvent.junction_id)
        .order_by(func.sum(TripEvent.duration_seconds).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "junction_id": junction_id,
            "name": name or "Unnamed junction",
            "signal": bool(signal),
            "latitude": latitude,
            "longitude": longitude,
            "stops": stops,
            "trips": trips,
            "total_seconds": round(float(total or 0), 1),
            "avg_seconds": round(float(average or 0), 1),
            "max_seconds": round(float(longest or 0), 1),
        }
        for (junction_id, name, signal, latitude, longitude,
             stops, trips, total, average, longest) in rows
    ]


def update_request(db, request_id, status=None, delay_reason=None, notes=None):
    """Admin correction of a request. Returns the row or None."""
    row = (
        db.query(AmbulanceRequest)
        .filter(AmbulanceRequest.request_id == request_id)
        .first()
    )
    if row is None:
        return None
    if status is not None:
        if status not in REQUEST_STATUSES:
            raise ValueError(f"Status must be one of {', '.join(REQUEST_STATUSES)}.")
        row.status = status
    if delay_reason is not None:
        if delay_reason not in DELAY_REASONS:
            raise ValueError(f"Delay reason must be one of {', '.join(DELAY_REASONS)}.")
        row.delay_reason = delay_reason
        row.delay_reason_manual = True
    if notes is not None:
        row.notes = notes[:500] or None
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


# -------------------------------------------------------------
# Grievances
# -------------------------------------------------------------

def trip_summaries(db, request_ids):
    """{request id: {"start_name", "hospital_name", "ambulance_id",
    "dispatched_at"}} for the trips that complaints are about."""
    ids = {request_id for request_id in request_ids if request_id}
    if not ids:
        return {}
    rows = db.query(AmbulanceRequest).filter(AmbulanceRequest.request_id.in_(ids)).all()
    return {
        row.request_id: {
            "start_name": row.start_name,
            "hospital_name": row.hospital_name,
            "ambulance_id": row.ambulance_id,
            "dispatched_at": row.dispatched_at,
        }
        for row in rows
    }


def grievance_dict(row, trips=None):
    """A complaint; trips (trip_summaries) adds the trip it is about."""
    return {
        "trip": (trips or {}).get(row.request_id),
        "id": row.id,
        "ticket_no": row.ticket_no,
        "request_id": row.request_id,
        "raised_by_role": row.raised_by_role,
        "raised_by_name": row.raised_by_name,
        "category": row.category,
        "priority": row.priority,
        "status": row.status,
        "subject": row.subject,
        "description": row.description,
        "resolution_note": row.resolution_note,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "resolved_at": row.resolved_at,
    }


def _check(value, allowed, name):
    if value is not None and value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}.")


def list_grievances(db, status=None, category=None, priority=None,
                    search=None, limit=200):
    query = db.query(Grievance)
    if status:
        query = query.filter(Grievance.status == status)
    if category:
        query = query.filter(Grievance.category == category)
    if priority:
        query = query.filter(Grievance.priority == priority)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(
            Grievance.ticket_no.ilike(pattern),
            Grievance.subject.ilike(pattern),
            Grievance.description.ilike(pattern),
            Grievance.raised_by_name.ilike(pattern),
            Grievance.request_id.ilike(pattern),
        ))
    return query.order_by(Grievance.created_at.desc()).limit(limit).all()


def grievance_counts(db):
    """Tickets per status and per category (for the dashboard)."""
    by_status = dict(
        db.query(Grievance.status, func.count()).group_by(Grievance.status).all()
    )
    by_category = dict(
        db.query(Grievance.category, func.count()).group_by(Grievance.category).all()
    )
    return {
        "by_status": {status: by_status.get(status, 0) for status in GRIEVANCE_STATUSES},
        "by_category": {
            category: by_category.get(category, 0) for category in GRIEVANCE_CATEGORIES
        },
    }


def create_grievance(db, raised_by_role, category, subject, priority="MEDIUM",
                     raised_by_name=None, description=None, request_id=None):
    _check(raised_by_role, GRIEVANCE_ROLES, "Raised by")
    _check(category, GRIEVANCE_CATEGORIES, "Category")
    _check(priority, GRIEVANCE_PRIORITIES, "Priority")
    if not subject or not subject.strip():
        raise ValueError("A subject is required.")

    now = datetime.now()
    row = Grievance(
        ticket_no="GRV-PENDING",
        request_id=(request_id or "").strip() or None,
        raised_by_role=raised_by_role,
        raised_by_name=(raised_by_name or "").strip() or None,
        category=category,
        priority=priority,
        status="OPEN",
        subject=subject.strip()[:200],
        description=(description or "").strip() or None,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()  # the id numbers the ticket
    row.ticket_no = f"GRV-{row.id:05d}"
    db.commit()
    db.refresh(row)
    return row


def auto_grievance(category, subject, description=None, request_id=None,
                   priority="MEDIUM"):
    """A ticket the app files itself (a hospital refused a patient, an
    ambulance stuck, police late, a trip far later than planned), for
    the admin to look into. Skipped if the same open ticket exists.
    Called from the simulation thread; never raises."""
    db = SessionLocal()
    try:
        exists = (
            db.query(Grievance)
            .filter(
                Grievance.subject == subject[:200],
                Grievance.request_id == request_id,
                Grievance.status.in_(("OPEN", "IN_PROGRESS")),
            )
            .first()
        )
        if exists is None:
            create_grievance(
                db, "SYSTEM", category, subject, priority=priority,
                raised_by_name="Automatic", description=description,
                request_id=request_id,
            )
    except Exception as error:
        db.rollback()
        logger.warning("Could not file an automatic ticket: %s", error)
    finally:
        db.close()


def update_grievance(db, grievance_id, status=None, priority=None,
                     category=None, resolution_note=None):
    row = db.query(Grievance).filter(Grievance.id == grievance_id).first()
    if row is None:
        return None
    _check(status, GRIEVANCE_STATUSES, "Status")
    _check(priority, GRIEVANCE_PRIORITIES, "Priority")
    _check(category, GRIEVANCE_CATEGORIES, "Category")
    if status is not None:
        row.status = status
        if status in ("RESOLVED", "CLOSED"):
            row.resolved_at = row.resolved_at or datetime.now()
        else:
            row.resolved_at = None
    if priority is not None:
        row.priority = priority
    if category is not None:
        row.category = category
    if resolution_note is not None:
        row.resolution_note = resolution_note.strip() or None
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row
