import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.database import Base, engine

# Every table model, imported so Base.metadata knows them all before
# create_all() below (the names themselves are not used here).
from backend.models.ambulance import Ambulance
from backend.models.hospital import Hospital
from backend.models.emergency import Emergency
from backend.models.traffic_signal import TrafficSignal
from backend.models.police_station import PoliceStation
from backend.models.police_call import PoliceCall
from backend.models.priority_change import PriorityChange
from backend.models.ambulance_request import AmbulanceRequest
from backend.models.route_optimization_log import RouteOptimizationLog
from backend.models.grievance import Grievance
from backend.models.trip_event import TripEvent

from backend.api.routes.general import router as general_router
from backend.api.routes.ambulance import router as ambulance_router
from backend.api.routes.hospital import router as hospital_router
from backend.api.routes.route import router as route_router
from backend.api.routes.emergency import router as emergency_router
from backend.api.routes.corridor import router as corridor_router
from backend.api.routes.traffic_signal import router as traffic_signal_router
from backend.api.routes.simulation import router as simulation_router
from backend.api.routes.planning import router as planning_router
from backend.api.routes.police import router as police_router
from backend.api.routes.fleet import router as fleet_router
from backend.api.routes.admin import complaints_router, router as admin_router
from backend.api.routes.assistant import router as assistant_router
from backend.mcp_server import mcp

from fastapi.middleware.cors import CORSMiddleware

from backend.services import admin_service
from backend.services.police_station_service import ensure_schema, sync_stations
from simulation.sumo import route_planner

# Show the simulation's and corridor's progress messages in the terminal.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# MCP server for AI agents, served at /mcp (backend/mcp_server.py).
mcp_app = mcp.streamable_http_app(streamable_http_path="/mcp")


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = FastAPI(
    title="AI-Powered Emergency Corridor",
    description="Backend API for the AI-powered emergency corridor system.",
    version="1.0.0",
    lifespan=lifespan,
)

# Websites allowed to call this API: CORS_ORIGINS in .env, comma
# separated (e.g. the server's address); the local dev server otherwise.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create database tables registered with SQLAlchemy.
Base.metadata.create_all(bind=engine)     #only creates tables for models that SQLAlchemy knows about.

# Police stations: add new columns to an older table, then copy the
# stations of the simulated area in (their phone numbers are kept).
try:
    ensure_schema()
    logging.getLogger(__name__).info(
        "Police stations in the database: %d", sync_stations(route_planner.police_stations())
    )
except Exception as error:
    logging.getLogger(__name__).warning("Could not sync police stations: %s", error)

# Admin dashboard: add newer columns to an older requests table, and
# close trips a previous run of the backend left IN_PROGRESS.
try:
    admin_service.ensure_schema()
    closed = admin_service.close_interrupted_requests()
    if closed:
        logging.getLogger(__name__).warning(
            "Marked %d trip(s) cut off by the last backend stop as INTERRUPTED.", closed
        )
except Exception as error:
    logging.getLogger(__name__).warning("Could not update the admin tables: %s", error)


# Register application routes.
app.include_router(general_router) #says hello, and /db-check checks the database is awake.
app.include_router(ambulance_router) #add, look up or update an ambulance.
app.include_router(hospital_router) #add or look up a hospital, and find the nearest hospital to an ambulance.
app.include_router(route_router) #get the road route from an ambulance to a hospital.
app.include_router(emergency_router) #start an emergency trip.
app.include_router(corridor_router) #show me the corridor for this ambulance right now.
app.include_router(traffic_signal_router) #add or look up a light, or change its state.
app.include_router(simulation_router) #start or stop the pretend city, get its state, and the /ws walkie-talkie that sends live updates.
app.include_router(planning_router) #find places, list hospitals and plan the fastest ambulance route.
app.include_router(police_router) #police stations, their contact numbers, and the log of police calls.
app.include_router(fleet_router) #more ambulances, each patient's priority (set by the crew), and the priority log.
app.include_router(complaints_router) #'Report a problem': complaints from users, drivers, hospitals and police.
app.include_router(admin_router) #admin dashboard: request KPIs, delay reasons, route performance and grievance tickets.
app.include_router(assistant_router) #the "Ask" chat: questions about the live simulation, answered by Claude.

# MCP server for AI agents at /mcp. Mounted last so it never hides the routes above.
app.mount("/", mcp_app)
