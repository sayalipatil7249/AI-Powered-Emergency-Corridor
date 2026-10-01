"""
Check that the admin figures never count a trip without a planned time
(no AI pre-trip estimate) as on time, and that trips left IN_PROGRESS by
a stopped backend are closed. Uses a throwaway in-memory SQLite
database, so the real one is not touched (no backend or SUMO needed).

    python -m scripts.tests.test_admin_data_quality
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.models.ambulance_request import AmbulanceRequest
from backend.models.grievance import Grievance
from backend.models.route_optimization_log import RouteOptimizationLog
from backend.models.trip_event import TripEvent
from backend.services import admin_service

ROUTE = {"signals_total": 3, "signals_cleared": 3, "reroutes": 0, "incidents": 0}


def use_test_database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine, tables=[
        AmbulanceRequest.__table__, RouteOptimizationLog.__table__,
        TripEvent.__table__, Grievance.__table__,
    ])
    admin_service.SessionLocal = sessionmaker(bind=engine)
    return admin_service.SessionLocal


def trip(request_id, planned, actual, plan_status=None):
    admin_service.start_request(
        request_id, "AMB-1", "Start", "Hospital", "normal", planned, 5000.0,
        plan_status=plan_status,
    )
    admin_service.finish_request(
        request_id, "COMPLETED", response_seconds=actual, stopped_seconds=0,
        stops=0, route=ROUTE,
    )


def check(name, actual, expected):
    status = "ok" if actual == expected else "FAILED"
    print(f"  {status:6} {name}: {actual!r}" + ("" if actual == expected else f" (expected {expected!r})"))
    return actual == expected


def main():
    Session = use_test_database()
    passed = True

    print("classify_delay")
    passed &= check("no plan -> unknown", admin_service.classify_delay(None, 50, 0, 0), None)
    passed &= check("on time", admin_service.classify_delay(30, 0, 0, 0), "NONE")
    passed &= check("late, stood still", admin_service.classify_delay(200, 100, 0, 0), "TRAFFIC")

    print("trips")
    trip("REQ-ON-TIME", 600, 620)                     # 20 s late: on time
    trip("REQ-LATE", 600, 800)                        # 200 s late
    trip("REQ-NO-MODEL", None, 500, "MODEL_MISSING")  # no estimate
    trip("REQ-NO-PLAN", None, 700)                    # status inferred

    db = Session()
    rows = {row.request_id: row for row in db.query(AmbulanceRequest)}
    passed &= check("plan_status with a plan", rows["REQ-ON-TIME"].plan_status, "OK")
    passed &= check("plan_status, model missing", rows["REQ-NO-MODEL"].plan_status, "MODEL_MISSING")
    passed &= check("plan_status inferred", rows["REQ-NO-PLAN"].plan_status, "NO_ESTIMATE")
    passed &= check("no-plan delay", rows["REQ-NO-MODEL"].delay_seconds, None)
    passed &= check("no-plan delay reason", rows["REQ-NO-MODEL"].delay_reason, None)

    print("kpi_summary")
    kpis = admin_service.kpi_summary(db)
    passed &= check("completed with plan", kpis["completed_with_plan"], 2)
    passed &= check("completed without plan", kpis["completed_without_plan"], 2)
    # Before the fix this was 3/4 = 0.75 (both no-plan trips "on time").
    passed &= check("on-time rate", kpis["on_time_rate"], 0.5)
    passed &= check("success rate", kpis["success_rate"], 1.0)

    print("route_summary")
    summary = admin_service.route_summary(db)
    passed &= check("trips", summary["trips"], 4)
    passed &= check("trips with plan", summary["trips_with_plan"], 2)
    passed &= check("fast arrivals", summary["fast_arrivals"], 1)

    print("delay_breakdown")
    reasons = {item["reason"]: item["count"] for item in admin_service.delay_breakdown(db)}
    passed &= check("late trips explained", sum(reasons.values()), 1)

    print("list_requests")
    listed = {item["request_id"]: item for item in admin_service.list_requests(db)}
    passed &= check("plan_status in the API", listed["REQ-NO-MODEL"]["plan_status"], "MODEL_MISSING")
    db.close()

    print("close_interrupted_requests")
    admin_service.start_request("REQ-CUT-OFF", "AMB-1", "Start", "Hospital", "normal", 600, 5000.0)
    passed &= check("closed", admin_service.close_interrupted_requests(), 1)
    db = Session()
    row = db.query(AmbulanceRequest).filter_by(request_id="REQ-CUT-OFF").one()
    passed &= check("status", row.status, "FAILED")
    passed &= check("finished trips untouched", rows["REQ-LATE"].status, "COMPLETED")
    passed &= check("nothing left open", admin_service.close_interrupted_requests(), 0)
    db.close()

    print("\nALL PASSED" if passed else "\nSOME CHECKS FAILED")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
