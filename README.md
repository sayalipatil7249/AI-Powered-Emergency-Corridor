# AI-Powered Emergency Corridor 🚑

A traffic simulation where an ambulance gets a **green corridor**: traffic lights ahead of it
turn green *before* it arrives, so it never has to stop at a red light.

The system runs on a real road network of **Pune, India** (from OpenStreetMap), simulated in
**SUMO**. A **FastAPI** backend watches the ambulance every second and controls the traffic
lights, and a **React** dashboard shows it all live on a map.

> **Current scope:** this is a demo built around **one fixed, tested route**. `ambulance_01`
> drives about 3.7 km (the fastest route) to **Ruby Hall Clinic** through realistic Pune traffic (hundreds to thousands of
> vehicles). Signal control is rule-based; the arrival time (ETA) comes from a machine-learning
> model trained on simulated trips (see [AI layer](#ai-layer)).

---

## How it works

```
 ┌──────────────┐   TraCI    ┌───────────────────────┐  WebSocket  ┌──────────────────┐
 │  SUMO        │ ◄────────► │  FastAPI backend      │ ──────────► │  React dashboard │
 │  (Pune roads,│  control   │  simulation_service   │  every 0.5s │  (Leaflet map)   │
 │  cars, lights│  & read    │  corridor logic       │             │                  │
 └──────────────┘            └──────────┬────────────┘             └──────────────────┘
                                        │
                              PostgreSQL + PostGIS
                        (ambulances, hospitals, signals,
                         emergencies, nearest-hospital)
```

1. SUMO simulates the city, one step per second.
2. Each step, the backend reads the ambulance's position and asks SUMO which traffic lights are
   coming up on its route.
3. The next light ahead keeps its **normal cycle** until the ambulance is close enough, then
   switches **just in time**, like real emergency pre-emption systems:

   | Step | What happens |
   |---|---|
   | Normal cycle | Cross traffic keeps moving. The AI next-signal model predicts when the ambulance will arrive |
   | Decide | Switch when the predicted arrival is within *yellow 3 s + all-red 2 s + queue clearing (2 s + 2 s per car queued in front of the ambulance) + 5 s margin*, at most 45 s (or when it is within 60 m) |
   | Yellow, then all red | Cross traffic stops safely |
   | Green | The ambulance's lane is held green until it has passed |

   Turning every light green as soon as it is "next" would stop cross traffic for minutes (on
   the demo route the third light is almost 2 km after the second), and block other emergency
   vehicles on crossing roads. The AI agent can still ask for one extra early green further
   ahead (safety rules: within 1 km, at most 2 held lights, released after 90 s).

4. Once the ambulance passes a light, that light goes back to its normal program (through its
   yellow phase). When the
   ambulance reaches the hospital, every light is restored.
5. The dashboard receives the live state over a WebSocket and draws the ambulance, the cars,
   the lights and the corridor.

---

## Architecture

The corridor logic never talks to SUMO directly. It uses three connectors
(`corridor/interfaces.py`), so moving from simulation to real city signals means writing
new connectors, not changing the logic:

```
        Corridor engine · AI ETA model · Dashboard feed
                           │
      ┌────────────────────┼─────────────────────┐
AmbulanceTracker     TrafficSource        SignalController
      │                    │                     │
 SUMO (today)         SUMO (today)          SUMO (today)
 GPS app (later)   Google Maps / cameras   City signal control
                     (later)                centre (later)
```

`python -m scripts.tests.regression_check --save/--compare <file>` runs four fixed
simulations and checks that a refactor did not change their results.

---

## Tech stack

| Layer | Technology |
|---|---|
| Simulation | [SUMO](https://eclipse.dev/sumo/) 1.27, TraCI, sumolib |
| Backend | Python 3.11, FastAPI, Uvicorn, WebSockets, Pydantic |
| Database | PostgreSQL + PostGIS, SQLAlchemy, GeoAlchemy2 |
| Maps & routing | OpenStreetMap, OSRM (routing), Overpass API (traffic-signal lookup), pyproj |
| Frontend | React 19, Vite, Leaflet / React-Leaflet |

---

## Project structure

```
.
├── backend/                     FastAPI application
│   ├── main.py                  App entry point: CORS, table creation, routers
│   ├── database.py              PostgreSQL connection (reads DATABASE_URL from .env)
│   ├── models/                  Database tables: ambulance, hospital, traffic_signal, emergency
│   ├── schemas/                 Request/response shapes (Pydantic)
│   ├── api/routes/              HTTP + WebSocket endpoints
│   ├── mcp_server.py            MCP tools for AI agents, served at /mcp
│   └── services/
│       ├── simulation_service.py  ★ Runs the live simulation loop for the dashboard
│       ├── route_service.py       Road route from OSRM
│       ├── junction_service.py    Finds junctions and traffic lights on a route
│       ├── eta_service.py         Time-to-reach estimates for each signal
│       ├── corridor_service.py    ACTIVE / PREPARING / STANDBY assignment
│       ├── signal_service.py      Same state machine for a moving ambulance
│       ├── emergency_service.py   Creates an emergency trip and saves its route
│       └── ambulance_ / hospital_ / traffic_signal_service.py   Database records
│
├── corridor/                    ★ The corridor "brain" (no SUMO code; see "Architecture")
│   ├── interfaces.py            The 3 connectors: AmbulanceTracker, TrafficSource, SignalController
│   ├── engine.py                Finds signals ahead and turns the next one green
│   ├── safety.py                Rules for early-green requests from AI agents
│   └── feed.py                  Builds the live state for the dashboard
│
├── simulation/sumo/
│   ├── adapters.py              SUMO versions of the 3 connectors (the only code using traci)
│   ├── route_planner.py         Fastest ambulance route between any two points
├── simulation/live_traffic.py   Live TomTom traffic for the digital twin
│   ├── sumo_bridge.py           SUMO start command, Pune speed limits, x/y → lat/lon
│   └── pune_network_v2/         SUMO network and route files
│       ├── pune_vtypes.add.xml                  ← Pune driving profile (speeds, gaps)
│       ├── edge_type_weights.txt                ← traffic concentrates on main roads
│       ├── ambulance_vtype.add.xml              ← the ambulance vehicle type
│       ├── hospitals.json                       ← hospitals in the area (from OpenStreetMap)
│       ├── scenarios/                           ← demo traffic + ambulance used by the dashboard
│       └── expanded_network/
│           ├── expanded.net.xml.gz              ← road network used by the demo
│           └── ambulance_hospital.rou.xml       ← ambulance route used by the demo
│
├── frontend/                    React dashboard
│   └── src/
│       ├── App.jsx              Start/stop buttons, WebSocket connection, layout
│       ├── MapView.jsx          Live Leaflet map
│       └── components/          Header, ambulance card, corridor panel, signal panel, vehicle table
│
├── agent/                       AI supervisor agent (LangGraph + Claude + MCP)
│   ├── graph.py                 The observe → assess → think → wait loop
│   ├── brain.py                 Claude calls and the tool-use loop
│   ├── prompts.py               The agent's instructions
│   ├── mcp_link.py              Connection to the MCP server
│   └── run.py                   Entry point: python -m agent.run
│
├── ai/                          AI layer (see "AI layer" below)
│   ├── scenarios.py             Realistic traffic levels (light / normal / heavy)
│   ├── features.py              What the ambulance "sees" each second (model inputs)
│   ├── run_experiments.py       Runs hundreds of trips headless and records training data
│   ├── train_eta.py             Trains the ETA models and compares them with the old formula
│   ├── eta_model.py             Loads the trained models for the live dashboard
│   ├── compare_corridor.py      With vs without corridor results
│   └── models/                  Trained models, scores (eta_metrics.json), chart (eta_report.png)
│
├── database/
│   └── emergency_corridor_database.sql   PostGIS setup and checking queries
│
├── scripts/                     One-off helper scripts used while building the demo
│   ├── route_building/          Finding signals and hospital edges, generating the ambulance route
│   ├── traffic_repair/          Fixing normal-traffic routes after the network was expanded
│   ├── signal_inspection/       Inspecting SUMO traffic lights
│   └── tests/                   Manual checks + regression_check.py (refactor safety net)
│
├── requirements.txt             Python dependencies
└── .env.example                 Template for your .env file
```

`_archive/` (not tracked by git) holds old files that are no longer used: the first Pune
network, a backup of v2, logs and a zip backup. It can be deleted once you no longer need them.

---

## Setup

### 1. Prerequisites

- **Python 3.11**
- **Node.js 20+**
- **PostgreSQL** with the **PostGIS** extension
- **SUMO 1.27**, with the `SUMO_HOME` environment variable pointing at its install folder
  (for example `C:\Program Files (x86)\Eclipse\Sumo` on Windows)

### 2. Database

Create a database and enable PostGIS:

```sql
CREATE DATABASE emergency_corridor;
\c emergency_corridor
CREATE EXTENSION postgis;
```

The backend creates its tables automatically on first start.

### 3. Backend

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env        # then edit DATABASE_URL
```

### 4. Frontend

```bash
cd frontend
npm install
```

---

## Running the demo

Run each command from the **project root** in its own terminal:

```bash
# Terminal 1: backend (http://localhost:8000, API docs at /docs)
uvicorn backend.main:app --reload

# Terminal 2: frontend (http://localhost:5173)
cd frontend
npm run dev
```

**Plan a trip (optional):** in the side panel, type a place in central Pune (or click
**Pick on map**), choose a hospital and click **Find fastest route**. The map highlights the
route and its signals, with its length and time. Without a plan, Start runs the tested demo
trip (Shukrawar Peth → Ruby Hall Clinic, along the fastest route).

Open http://localhost:5173 and click **Start simulation**. Traffic first builds up for 10
simulated minutes (fast-forwarded, a few seconds of real time), then the ambulance departs.
Watch the route panel and the map as signals ahead switch to green. The simulation stops by
itself shortly after the ambulance arrives, or click **Stop**.

To run SUMO without its window, set `SUMO_GUI=0` in `.env` (needed on Apple Silicon Macs,
where the SUMO window can hang).

---

## API overview

Full interactive docs: http://localhost:8000/docs

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Health check |
| GET | `/db-check` | Database connection check |
| POST | `/simulation/start` | Start the SUMO simulation (optional body: `start` and `hospital` points; default: demo trip) |
| GET | `/plan/area` | The simulated area (trips must start and end inside it) |
| GET | `/plan/hospitals` | Hospitals inside the area |
| GET | `/plan/search?q=` | Find places in the area (OpenStreetMap Nominatim) |
| POST | `/plan/route` | Fastest ambulance route between a start point and a hospital |
| POST | `/simulation/stop` | Stop the simulation |
| GET | `/simulation/state` | Latest simulation snapshot |
| WS | `/simulation/ws` | Live simulation state, every 0.5 s |
| POST | `/mcp` | MCP server for AI agents (see [AI layer](#ai-layer)) |
| POST / GET / PUT | `/ambulances/…` | Register, read and update ambulances |
| POST / GET | `/hospitals/…` | Register and read hospitals |
| GET | `/hospitals/nearest/{ambulance_id}` | Nearest hospital (PostGIS distance) |
| POST / GET / PUT | `/traffic-signals/…` | Register, read and update signal state |
| GET | `/routes/{ambulance_id}/{hospital_id}` | OSRM route, junctions and signal ETAs |
| POST | `/emergencies/` | Start an emergency trip and save its route |
| GET | `/corridors/{ambulance_id}` | Current corridor for an active emergency |

The live dashboard uses the `/simulation/*` endpoints. The database and OSRM endpoints work
alongside it but are not yet connected to the SUMO simulation.

---

## Helper scripts

Scripts in `scripts/` were used to build and check the demo route. Run them from the project
root as modules so their imports and relative paths resolve:

```bash
python -m scripts.tests.test_route
python -m scripts.route_building.find_route_signals
```

---

## AI layer

### Arrival-time (ETA) model

The dashboard's "Estimated arrival" comes from a gradient-boosted tree model
(scikit-learn `HistGradientBoostingRegressor`) trained on hundreds of simulated trips. A second
model predicts the time to the next traffic signal. Both fall back to a physics formula (sum of
live road travel times) if no trained model is present.

Rebuild the data and models from the project root:

```bash
python -m ai.run_experiments --seeds 15 --corridor both   # ~270 trips, runs in parallel
python -m ai.train_eta                                     # trains + scores the models
python -m ai.compare_corridor                              # with vs without corridor
```

Training data lives in `data/` (not tracked by git). The models are scored only on traffic
patterns they never saw during training; results are in `ai/models/eta_metrics.json` and
`ai/models/eta_report.png`.

### Just-in-time signal switching (AI clearance model)

A signal ahead keeps its normal cycle until the ambulance is predicted to arrive within the
time the junction needs: yellow and all-red for cross traffic, plus the time for the cars
queued in front of the ambulance to drive off. That clearance time comes from a model
(`ai/clearance.py`) trained on thousands of simulated junction switches
(`python -m ai.clearance_experiments`, `python -m ai.train_clearance`). Signals a few metres
apart, or one big junction crossed several times, switch together.

### Trip-time estimate before departure (AI pre-trip model)

"Find fastest route" shows how long an ambulance will really take in the current live
traffic, learned from hundreds of simulated trips on random routes (`ai/pretrip.py`;
`python -m ai.pretrip_experiments`, `python -m ai.train_pretrip`). The old "empty roads" time
is shown next to it.

### Deadlock response: re-route or send police

Every 5 seconds a model (`ai/deadlock.py`) looks 1.5 km along the route and predicts whether the
ambulance will get stuck there (cars stuck inside junctions, stretches full of stopped cars). If
so, `corridor/response.py` compares two options and takes the one that saves more time:

- **Re-route** around the jam using live travel times.
- **Traffic police** from the station that can get there first (11 real Pune police stations
  and chowkis from OpenStreetMap). A police car drives there with its siren on; on arrival
  the officers stop traffic coming in from side roads and wave stuck cars through until the
  ambulance has passed.

**Tested** on 60 paired trips (same routes and traffic, with and without the response): in
heavy traffic trips were 5% faster and the ambulance stood still 26% less; where police were
sent they saved 126 s on average (up to 11 min in the worst deadlocks). Re-routing made every
trip it was used on slower, so it is still compared and shown but not carried out
(`ALLOW_REROUTE` in `corridor/response.py`).

The decision, the police car and the jam appear on the map and in the "AI deadlock watch"
card; the MCP tool `get_deadlock_watch` lets an AI agent read it. Rebuild with
`python -m ai.deadlock_experiments`, `python -m ai.train_deadlock`, and measure the effect with
`python -m ai.response_experiments`.

### MCP server (tools for AI agents)

The backend also serves an MCP server at **`http://127.0.0.1:8000/mcp`** (streamable HTTP),
exposing the live simulation to AI agents (the planned LangGraph agent, Claude Desktop,
Claude Code, …):

| Tool | What it does |
|---|---|
| `start_simulation` | Start the simulation |
| `get_simulation_status` | Running? Simulated time, vehicles, ambulance waiting / driving / arrived |
| `get_ambulance_state` | Position, speed, distance left, AI and formula ETA, signals passed |
| `get_route_traffic` | Roads ahead with traffic level `free` / `slow` / `jammed` |
| `get_upcoming_signals` | Next signals: distance, time to reach, light for the ambulance, queue, role |
| `request_signal_priority` | **Ask** for early green at a signal ahead (checked by safety rules) |
| `release_signal_priority` | Cancel a request |
| `get_corridor_events` | Recent decisions and requests, with reasons |
| `get_safety_rules` | The rules requests are checked against |

The agent can only *request* green. `corridor/safety.py` decides: only signals within
1000 m, at most 2 junctions held green at once (the next one plus one extra), and every
request is released after 90 s or once the ambulance has passed.

Connect Claude Code to it:

```bash
claude mcp add --transport http emergency-corridor http://127.0.0.1:8000/mcp
```

Test it end to end (backend must be running): `python -m scripts.tests.test_mcp`

### AI supervisor agent (LangGraph + Claude)

`agent/` is an AI agent that supervises the trip through the MCP server. It is a LangGraph
state machine:

```
observe ──► assess ──► think (Claude) ──► wait ──► observe ...
   │           └── nothing new ──────────────┘
   └── arrived: trip summary ──► end
```

- **observe / assess** are plain code: every 3 s they read the ambulance, the signals ahead and
  the traffic ahead, and decide whether anything needs a decision (cars queued at a signal
  ahead, a jam ahead, the start of the trip, a status update each simulated minute, arrival).
- **think** calls Claude (`claude-opus-5`, low effort, via the Anthropic SDK) only for those
  triggers. Claude uses the MCP tools to look closer, request early green where a queue would
  slow the ambulance, and post a one-line explanation to the dashboard's **AI agent** panel.

Run it with the backend running and a Claude API key in `.env` (`ANTHROPIC_API_KEY=...`):

```bash
python -m agent.run            # starts the simulation and supervises the trip
python -m agent.run --no-start # supervise a simulation that is already running
```

It prints every decision and, at the end, the number of Claude calls, tokens and approximate
cost. `AGENT_MODEL` / `AGENT_EFFORT` in `.env` change the model and effort.

### Live Pune traffic (digital twin)

With a `TOMTOM_API_KEY` in `.env`, every run copies **today's real Pune traffic**:

- Before starting, the backend asks TomTom for the live speed on ~35 stretches of road (the main
  roads of the area and the ambulance's route), cached for 5 minutes to save requests.
- The overall congestion picks light / normal / heavy traffic, and every simulated road under a
  TomTom stretch gets a speed limit that makes simulated cars drive at the **real live speed**.
- It refreshes every 10 minutes while running.
- The dashboard draws the real traffic on the map (green / orange / red, closures dashed) and
  shows TomTom's live travel time for a normal car on the same trip, a direct check of
  the simulation.

Individual real cars are not available from any public source, so the vehicles on the map are
simulated vehicles calibrated to live traffic data. Signals and the ambulance are simulated.

### How realistic is the traffic?

| Setting | Value | Why |
|---|---|---|
| Road speed limit | capped at 50 km/h | OSM import gave main roads 100 km/h |
| Driver speed | ~55% of the limit on average (about 27 km/h), at most 75% (37.5 km/h) | Mixed traffic (two-wheelers, autos, pedestrians) |
| Ambulance speed | cruises ~40 km/h (35–45 km/h), never above 45 km/h | With the siren it is faster than every normal car, so cars never overtake it; two-wheelers, autos, pedestrians and parked vehicles still keep it well below the limit |
| Where traffic goes | weighted towards trunk / primary / secondary roads | Real traffic concentrates on arterials |
| Re-routing | 50% of drivers re-route around jams | Navigation-app behaviour |
| Traffic levels | light / normal / heavy = 14 / 24 / 34 vehicles per hour per lane-km | Heavier levels make longer queues at signals |

**To calibrate further,** check the real Google Maps travel time for the route
(Shukrawar Peth → Ruby Hall Clinic) at a few times of day and adjust `TRAFFIC_LEVELS` in
`ai/scenarios.py` and `speedFactor` in `pune_vtypes.add.xml` until the simulated normal-car
times match.

---

## Known limitations

- **One ambulance at a time.** Any start and hospital inside the simulated area (about
  5 × 4 km of central Pune, the outline highlighted on the map) can be chosen; the fastest route uses speed limits, not live
  traffic.
- **The ETA model was trained on the demo route only.** It works on other routes but is less
  accurate there until it is retrained on many routes.
- **Rule-based signal control.** Which signal goes green, and when, still follows fixed rules;
  only the ETA is machine-learned so far.
- **Signal timings are SUMO's defaults,** not measured Pune timings.
- **Two halves not yet connected.** The database/OSRM features (nearest hospital, saved
  emergencies) and the live SUMO simulation run independently.
- **Public services.** Route endpoints depend on the public OSRM and Overpass servers. If those
  are down, the endpoints return a `502` error.
- **No authentication** on the API. It's intended for local demos only.

## Roadmap

- Human approval step for agent requests (LangGraph interrupts)
- Jam prediction and clearing queues ahead of the ambulance
- Delay caused to other traffic in the with/without comparison
- Fastest route using live (simulated or real) traffic
- Multiple ambulances with priority handling
