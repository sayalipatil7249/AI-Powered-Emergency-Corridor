from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models.ambulance_request import DELAY_REASONS, REQUEST_STATUSES
from backend.models.grievance import (
    GRIEVANCE_CATEGORIES,
    GRIEVANCE_PRIORITIES,
    GRIEVANCE_ROLES,
    GRIEVANCE_STATUSES,
)
from backend.services import admin_service


router = APIRouter(
    prefix="/admin",
    tags=["Admin dashboard"],
)

# Complaints raised by people (not the admin): POST /complaints.
complaints_router = APIRouter(prefix="/complaints", tags=["Complaints"])


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


# Period filter shared by the analytics endpoints: last N days, or all.
Days = Query(None, ge=1, le=3650, description="Only the last N days (default: all)")


class RequestUpdate(BaseModel):
    status: str | None = None
    delay_reason: str | None = None
    notes: str | None = None


class GrievanceCreate(BaseModel):
    raised_by_role: str
    category: str
    subject: str
    priority: str = "MEDIUM"
    raised_by_name: str | None = None
    description: str | None = None
    request_id: str | None = None


class GrievanceUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    category: str | None = None
    resolution_note: str | None = None


# The allowed values, for the dashboard's filters and forms
@router.get("/options")
def get_options():
    return {
        "request_statuses": REQUEST_STATUSES,
        "delay_reasons": DELAY_REASONS,
        "grievance_roles": GRIEVANCE_ROLES,
        "grievance_categories": GRIEVANCE_CATEGORIES,
        "grievance_priorities": GRIEVANCE_PRIORITIES,
        "grievance_statuses": GRIEVANCE_STATUSES,
    }


# KPI cards: totals, success vs failure, average response and delay
@router.get("/kpis")
def get_kpis(days: int | None = Days, db: Session = Depends(get_db)):
    return admin_service.kpi_summary(db, days)


# Late trips per delay reason
@router.get("/delays")
def get_delay_breakdown(days: int | None = Days, db: Session = Depends(get_db)):
    return admin_service.delay_breakdown(db, days)


# Requests and response time per day
@router.get("/trend")
def get_trend(days: int = Query(14, ge=1, le=365), db: Session = Depends(get_db)):
    return admin_service.daily_trend(db, days)


# Fast-response measures: optimal route, corridor, police
@router.get("/route-performance")
def get_route_performance(days: int | None = Days, db: Session = Depends(get_db)):
    return admin_service.route_summary(db, days)


# Ambulance requests with their route optimization logs, newest first
@router.get("/requests")
def get_requests(
    status: str | None = None,
    fast_only: bool = False,
    days: int | None = Days,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return admin_service.list_requests(db, status, fast_only, days, limit)


# One trip: its route log, planned route, driven track and events in order
@router.get("/requests/{request_id}")
def get_request_detail(request_id: str, db: Session = Depends(get_db)):
    detail = admin_service.trip_detail(db, request_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return detail


# Junctions where ambulances stood still most (total standing time)
@router.get("/stuck-spots")
def get_stuck_spots(
    days: int | None = Days,
    limit: int = Query(15, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Where ambulances stood still: nearby stops grouped by PostGIS."""
    return admin_service.stuck_spots(db, days, limit)


@router.get("/junctions")
def get_problem_junctions(
    days: int | None = Days,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    return admin_service.junction_report(db, days, limit)


# Correct a request's status or delay reason
@router.patch("/requests/{request_id}")
def patch_request(request_id: str, update: RequestUpdate, db: Session = Depends(get_db)):
    try:
        row = admin_service.update_request(
            db, request_id, update.status, update.delay_reason, update.notes
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return admin_service.request_dict(row)


# Grievance tickets, filtered
@router.get("/grievances")
def get_grievances(
    status: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    db: Session = Depends(get_db),
):
    rows = admin_service.list_grievances(db, status, category, priority, search)
    trips = admin_service.trip_summaries(db, [row.request_id for row in rows])
    return [
        admin_service.grievance_dict(row, trips)
        for row in rows
    ]


# Tickets per status and category
@router.get("/grievances/summary")
def get_grievance_summary(db: Session = Depends(get_db)):
    return admin_service.grievance_counts(db)


# Raise a ticket
@router.post("/grievances", status_code=201)
def post_grievance(ticket: GrievanceCreate, db: Session = Depends(get_db)):
    try:
        row = admin_service.create_grievance(db, **ticket.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    return admin_service.grievance_dict(row, admin_service.trip_summaries(db, [row.request_id]))


# Move a ticket on (status, priority, category, resolution note)
# Who can report with "Report a problem": the ambulance crew using the
# app, and the 108 control room, hospitals and police they work with
# (SYSTEM is the app itself).
PUBLIC_ROLES = ("DRIVER", "CALL_CENTRE", "HOSPITAL", "POLICE")


class ProblemReport(BaseModel):
    raised_by_role: str
    category: str
    subject: str
    raised_by_name: str | None = None
    description: str | None = None
    request_id: str | None = None


@complaints_router.post("", status_code=201)
def report_problem(report: ProblemReport, db: Session = Depends(get_db)):
    """'Report a problem' on the main screen: the ambulance crew (or the
    108 control room, a hospital, the police) raises a ticket; the admin
    handles it on the Admin page."""
    if report.raised_by_role not in PUBLIC_ROLES:
        raise HTTPException(status_code=400, detail="Choose who you are.")
    try:
        row = admin_service.create_grievance(db, **report.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return admin_service.grievance_dict(row, admin_service.trip_summaries(db, [row.request_id]))


@router.patch("/grievances/{grievance_id}")
def patch_grievance(grievance_id: int, update: GrievanceUpdate, db: Session = Depends(get_db)):
    try:
        row = admin_service.update_grievance(db, grievance_id, **update.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    if row is None:
        raise HTTPException(status_code=404, detail="Grievance not found")
    return admin_service.grievance_dict(row, admin_service.trip_summaries(db, [row.request_id]))
