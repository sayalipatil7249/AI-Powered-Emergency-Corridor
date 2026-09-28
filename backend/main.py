import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.database import Base, engine

from backend.models.ambulance import Ambulance
from backend.models.hospital import Hospital
from backend.models.emergency import Emergency
from backend.models.traffic_signal import TrafficSignal

from backend.api.routes.general import router as general_router
from backend.api.routes.ambulance import router as ambulance_router
from backend.api.routes.hospital import router as hospital_router
from backend.api.routes.route import router as route_router
from backend.api.routes.emergency import router as emergency_router
from backend.api.routes.corridor import router as corridor_router
from backend.api.routes.traffic_signal import router as traffic_signal_router
from backend.api.routes.simulation import router as simulation_router
from backend.api.routes.planning import router as planning_router
from backend.mcp_server import mcp

from fastapi.middleware.cors import CORSMiddleware

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create database tables registered with SQLAlchemy.
Base.metadata.create_all(bind=engine)     #only creates tables for models that SQLAlchemy knows about.


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

# MCP server for AI agents at /mcp. Mounted last so it never hides the routes above.
app.mount("/", mcp_app)
