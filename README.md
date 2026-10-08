# AI-Powered Emergency Corridor 🚑

A traffic simulation where an ambulance gets a **green corridor**: traffic lights ahead of it
turn green *before* it arrives, so it never has to stop at a red light.

The system runs on a real road network of **Pune, India** (from OpenStreetMap), simulated in
**SUMO**. A **FastAPI** backend watches the ambulance every second and controls the traffic
lights, and a **React** dashboard shows it all live on a map.

> **Current scope:** up to ten ambulances at once (a junction referee decides who gets a
> shared signal, by patient priority; see
> [Several ambulances](#several-ambulances-patient-priority-and-the-junction-referee)), on any
> start point and hospital inside a ~6 × 5 km area of central Pune, through realistic traffic
> (hundreds to thousands of vehicles, optionally copied from live TomTom data). The default demo
> trip is Shukrawar Peth → **Ruby Hall Clinic** (about 3.7 km). Machine-learning models predict
> the arrival time, when each signal must switch, the trip time before departure and deadlocks
> ahead (see [AI layer](#ai-layer)). Police stations along the route are alerted (and phoned)
> for jams on roads without signals, and every trip is recorded for the
> [admin dashboard](#admin-dashboard).

---

## How it works

```
 ┌──────────────┐   TraCI    ┌───────────────────────┐  WebSocket  ┌──────────────────┐
 │  SUMO        │ ◄────────► │  FastAPI backend      │ ──────────► │  React dashboard │
 │  (Pune roads,│  control   │  simulation_service   │  every 0.5s │  2D map, 3D view │
 │  cars, lights│  & read    │  corridor logic       │             │  admin dashboard │
 └──────────────┘            └──────────┬────────────┘             └──────────────────┘
                                        │
                  ┌─────────────────────┼──────────────────────┐
          PostgreSQL + PostGIS     TomTom (live traffic)   Twilio (police calls)
     (police stations, call log,
      recorded trips, grievances,
      hospitals, signals)
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
   | Turns with only a give-way green | If the ambulance's movement never has a protected green (e.g. a right turn across oncoming traffic), it gets an emergency green instead: every movement from its own approach road green, every other direction red |

   Turning every light green as soon as it is "next" would stop cross traffic for minutes (on
   the demo route the third light is almost 2 km after the second), and block other emergency
   vehicles on crossing roads. The AI agent can still ask for one extra early green further
   ahead (safety rules: within 1 km, at most 2 held lights, released after 90 s).

4. Once the ambulance passes a light, that light goes back to its normal program (through its
   yellow phase). When the
   ambulance reaches the hospital, every light is restored.
5. The dashboard receives the live state over a WebSocket and draws the ambulance, the cars,
   the lights and the corridor, on a 2D map or in a 3D chase view.
6. When the trip ends, its timeline (stops, signals, police, accidents, re-routes, driven
   track) is saved to PostgreSQL for the admin dashboard.

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
| Backend | Python 3.11, FastAPI, Uvicorn, WebSockets, Pydantic, MCP server |
| Database | PostgreSQL + PostGIS, SQLAlchemy, GeoAlchemy2 |
| AI / ML | scikit-learn (gradient-boosted trees), joblib; LangGraph + Claude (Anthropic SDK) for the supervisor agent |
| Maps & routing | OpenStreetMap, Nominatim (place search), OSRM, Overpass API, pyproj, rtree |
| Live data & alerts | TomTom Traffic Flow + Routing API, Twilio Voice |
| Frontend | React 19, Vite, Leaflet / React-Leaflet (2D map), MapLibre GL (3D chase view), Recharts (admin charts) |

---

## Project structure

```
.
├── backend/                     FastAPI application
│   ├── main.py                  App entry point: CORS, table creation, routers
│   ├── database.py              PostgreSQL connection (reads DATABASE_URL from .env)
│   ├── mcp_server.py            MCP tools for AI agents, served at /mcp
│   ├── models/                  Database tables: ambulances, hospitals, traffic_signals,
│   │                            emergencies, police_stations, police_calls,
│   │                            ambulance_requests, route_optimization_logs, trip_events, grievances
│   ├── schemas/                 Request/response shapes (Pydantic)
│   ├── api/routes/              HTTP + WebSocket endpoints (simulation, planning, police, admin, …)
│   └── services/
│       ├── simulation_service.py  ★ Runs the live simulation loop for the dashboard
│       ├── ambulance_run.py       One ambulance's trip: its corridor, ETA, police watch
│       ├── assistant_service.py   "Ask AI" chat and "Describe the emergency" (Claude)
│       ├── trip_recorder.py       Records each trip's events and driven track
│       ├── admin_service.py       KPIs, delay reasons, problem junctions, grievances
│       ├── police_station_service.py  Police stations and their contact numbers
│       ├── police_notifier.py     Twilio voice calls to police stations
│       ├── priority_log.py        Saves every patient-priority setting (priority_changes)
│       ├── route_service.py       Road route from OSRM
│       ├── junction_service.py    Finds junctions and traffic lights on a route
│       ├── eta_service.py         Time-to-reach estimates for each signal
│       ├── signal_service.py      Same state machine for a moving ambulance
│       ├── emergency_service.py   Creates an emergency trip and saves its route
│       └── ambulance_ / hospital_ / traffic_signal_service.py   Database records
│
├── corridor/                    ★ The corridor "brain" (no SUMO code; see "Architecture")
│   ├── interfaces.py            The 3 connectors: AmbulanceTracker, TrafficSource, SignalController
│   ├── engine.py                Finds signals ahead and switches each one just in time
│   ├── safety.py                Rules for early-green requests from AI agents
│   ├── response.py              Deadlock response: police, or a detour for a stuck critical patient
│   ├── referee.py               Several ambulances: who gets a shared signal first
│   ├── priority.py              Patient conditions → priority levels and weights
│   ├── routing.py               Several ambulances: plans routes before departure so they clash less
│   ├── dispatch.py              108 dispatch: which ambulance (ALS / BLS) goes to the patient
│   ├── hospital_care.py         Which hospitals can treat which condition (cath lab, ICU…)
│   ├── police_watch.py          Police alerts for jams on stretches without signals
│   ├── police_board.py          Live status of every police station
│   └── feed.py                  Builds the live state for the dashboard
│
├── simulation/
│   ├── live_traffic.py          Live TomTom traffic for the digital twin
│   └── sumo/
│       ├── adapters.py          SUMO versions of the connectors (the only code using traci):
│       │                        tracker, signals, traffic, police responders, accidents, give-way
│       ├── route_planner.py     Fastest ambulance route; stretches without signals + police cover
│       ├── sumo_bridge.py       SUMO start command, Pune speed limits, x/y → lat/lon
│       └── pune_network_v2/     SUMO network and route files
│           ├── pune_vtypes.add.xml          ← Pune driving profile (speeds, gaps, reaction time)
│           ├── edge_type_weights.txt        ← traffic concentrates on main roads
│           ├── ambulance_vtype.add.xml      ← the ambulance vehicle type
│           ├── hospitals.json               ← hospitals in the area (from OpenStreetMap)
│           ├── police_stations.json         ← 11 police stations / chowkis (from OpenStreetMap)
│           ├── scenarios/                   ← demo traffic + ambulance used by the dashboard
│           ├── area_2x/                     ← the bigger area (SIM_AREA=2x): network,
│           │                                  hospitals, police stations, demo traffic
│           └── expanded_network/
│               ├── expanded.net.xml.gz      ← road network used by the demo
│               └── ambulance_hospital.rou.xml   ← ambulance route of the demo trip
│
├── frontend/                    React dashboard
│   └── src/
│       ├── Root.jsx             Pages: live map (#/), admin (#/admin), trip page (#/admin/trip/<id>)
│       ├── App.jsx              Live page: trip planner, start/stop, WebSocket, playback speed
│       ├── MapView.jsx          Live 2D Leaflet map
│       ├── ChaseView.jsx        3D view with the camera following the ambulance (MapLibre GL)
│       ├── components/          Trip planner, emergency intake, ambulances (fleet) panel,
│       │                        live status cards (corridor, AI response, police, live traffic,
│       │                        drivers making room, AI agent feed), Ask AI chat, Report a problem
│       └── admin/               Admin pages (sidebar): dashboard, complaints, trips, who went
│                                first, priority changes, police; trip detail page
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
│   ├── run_experiments.py / train_eta.py / eta_model.py        Arrival-time (ETA) models
│   ├── clearance*.py / train_clearance.py                      When a signal must switch
│   ├── pretrip*.py / train_pretrip.py                          Trip time before departure
│   ├── deadlock*.py / train_deadlock.py / response_experiments.py   Deadlock prediction + response
│   ├── compare_corridor.py      With vs without corridor results
│   └── models/                  Trained models (.joblib) and their scores (*_metrics.json)
│
├── database/
│   ├── emergency_corridor_database.sql   PostGIS setup and checking queries
│   └── admin_tables.sql                  Admin tables (created automatically; for reference)
│
├── scripts/
│   ├── set_police_phones.py     Saves police_contacts.json numbers to the database
│   ├── route_building/          Finding signals and hospital edges, generating the ambulance route
│   ├── traffic_repair/          Fixing normal-traffic routes after the network was expanded
│   ├── signal_inspection/       Inspecting SUMO traffic lights
│   └── tests/                   Checks: route, WebSocket, MCP, police call, admin data quality,
│                                and regression_check.py (refactor safety net)
│
├── tests/                       Unit tests: routing, critical rescue, fleet planning,
│                                junction priority (python -m unittest discover -s tests)
│
├── police_contacts.example.json Template for per-station TEST phone numbers
├── see_police_stations.sql      Query to look at the police tables
├── requirements.txt             Python dependencies
└── .env.example                 Template for your .env file
```

`data/` (not tracked by git) holds the training data collected by the experiment scripts and the
live-traffic cache.

`_archive/` (not tracked by git) holds old files that are no longer used: the first Pune
network, a backup of v2, logs and a zip backup. It can be deleted once you no longer need them.

---

## Setup

### 1. Prerequisites

- **Python 3.11**
- **Node.js 20+**
- **PostgreSQL** with the **PostGIS** extension
- **SUMO 1.27**: installed by `pip install -r requirements.txt` (the `eclipse-sumo` package),
  so there is nothing else to install. To use your own SUMO install instead, set `SUMO_HOME`
  in `.env` (for example `C:\Program Files (x86)\Eclipse\Sumo` on Windows).

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

### 5. Settings (`.env`)

Only `DATABASE_URL` is required; everything else is optional. `.env` is never committed
(only `.env.example`).

| Setting | Default | What it does |
|---|---|---|
| `DATABASE_URL` | (required) | PostgreSQL + PostGIS database, e.g. `postgresql://USER:PASSWORD@localhost:5432/emergency_corridor` |
| `SUMO_GUI` | `0` | `1` also opens SUMO's own window (needs a screen; can hang on Apple Silicon Macs). The dashboard works either way. |
| `SUMO_HOME` | found by itself | SUMO's install folder, only if you use your own SUMO instead of the pip package |
| `GIVE_WAY` | `1` | `0` turns off drivers making room for the siren |
| `SIM_AREA` | `original` | `2x` for the larger 5.9 × 4.7 km map |
| `SIM_TRAFFIC_LEVEL` | live / normal | `light`, `normal` or `heavy`: always use this traffic level (handy for demos) |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Websites allowed to call the API (set to the dashboard's address on a server) |
| `ANTHROPIC_API_KEY` | none | Claude: "Ask AI", "Describe the emergency" and the AI agent. Without it those features say they are off. |
| `ASSISTANT_MODEL`, `ASSISTANT_EFFORT` | `claude-opus-5-5`, `low` | Model and effort for "Ask AI" and the AI intake |
| `AGENT_MODEL`, `AGENT_EFFORT` | `claude-opus-5`, `low` | Model and effort for the AI supervisor agent |
| `TOMTOM_API_KEY` | none | Live Pune traffic (digital twin) |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | none | Real phone calls to police (test phones only) |
| `POLICE_ALERT_PHONE` | none | The **test** phone that stands in for every police station (`+91` and 10 digits) |
| `POLICE_CALLS` | `1` | `0` writes calls to the log instead of phoning |

### 6. Running the tests

From the project root, with the virtual environment active:

```bash
python -m unittest discover -s tests            # unit tests: routing, rescue, fleet, priority
python -m scripts.tests.test_admin_data_quality  # admin figures (uses a throwaway database)
```

Neither needs SUMO, the backend or the real database. More checks are listed under
[Helper scripts](#helper-scripts).

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
**Pick on map**) and choose a hospital. **Start** then runs that trip; **Find route** first
shows the route and its signals, with its length and time. With nothing chosen, Start runs
the tested demo trip (Shukrawar Peth → Ruby Hall Clinic, along the fastest route).

Open http://localhost:5173 and click **Start**. Traffic first builds up for 10
simulated minutes (fast-forwarded, a few seconds of real time), then the ambulance departs.
Watch the route panel and the map as signals ahead switch to green. The simulation stops by
itself shortly after the ambulance arrives, or click **Stop**.

While it runs you can:

- switch between the **2D map** and the **3D chase view** (camera behind the ambulance);
- change the **playback speed** (1×, 2×, 5×, 10×) to watch the trip faster. The simulated
  speeds do not change;
- click **Accident ahead of Ambulance 1** (Ambulances → Test tools) to test the police
  response (see
  [Police for roads without signals](#police-for-roads-without-signals-phone-call-alerts)).

The **admin dashboard** is at http://localhost:5173/#/admin (see
[Admin dashboard](#admin-dashboard)).

SUMO runs without its own window by default; set `SUMO_GUI=1` in `.env` to also watch it
there (not on Apple Silicon Macs, where the SUMO window can hang).

---

## Deploying on a server

The steps above run the project on a laptop. On a server (Linux), the same backend and
frontend run like this:

**1. Install** Python 3.11, Node.js 20+, PostgreSQL with PostGIS, and SUMO 1.27
(`pip install eclipse-sumo==1.27.1` inside the virtual environment is the simplest way;
otherwise the system package and `SUMO_HOME`). Then the database, backend and frontend
steps of [Setup](#setup).

**2. `.env`** (copy `.env.example`):

- `DATABASE_URL`: the server's PostgreSQL database.
- `CORS_ORIGINS`: the address the dashboard is opened at, e.g.
  `CORS_ORIGINS=https://corridor.example.org`. Without it the browser blocks the dashboard
  from calling the API.
- SUMO runs without its window by default (`SUMO_GUI=0`), which a server needs.
- Time zone: the app saves times in the server's local time, so set it to India time
  (e.g. `sudo timedatectl set-timezone Asia/Kolkata`, or `TZ=Asia/Kolkata` for the backend
  process); otherwise the admin pages show UTC.
- Optional keys: `ANTHROPIC_API_KEY` (AI chat and intake), `TOMTOM_API_KEY` (live traffic),
  Twilio settings (police phone calls; `POLICE_CALLS=0` only logs them).

**3. Backend:** run it **without `--reload`** and with **exactly one worker**. The live
simulation runs inside the backend process; a second worker would start a second,
separate simulation.

```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 1
```

**4. Frontend:** build it with the backend's public address, then serve `frontend/dist/`
as static files (e.g. nginx or any static web host):

```bash
cd frontend
VITE_API_URL=https://api.corridor.example.org npm run build
```

The dashboard uses a WebSocket (`/simulation/ws`) on the same address, so a reverse proxy
in front of the backend must pass WebSocket upgrades through.

**5. Access control.** The project has **no login**: anyone who can open the dashboard can
also open the admin page (change police phone numbers, close complaints), start trips that
make real Twilio calls, and use the AI chat on your Anthropic key. Run it on a private
network (VPN / office network), or put a password in front of it (e.g. HTTP basic auth in
the reverse proxy), before exposing it to the internet.

---

## API overview

Full interactive docs: http://localhost:8000/docs

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | Health check |
| GET | `/db-check` | Database connection check |
| POST | `/simulation/start` | Start the SUMO simulation (optional body: `start` and `hospital` points and the patient's `condition`; default: demo trip; `?condition=` for the demo trip) |
| GET | `/fleet/` | Every ambulance of the run, give-way instructions, referee decisions |
| GET | `/fleet/conditions` | Patient conditions the crew can choose, with their priority level |
| POST | `/fleet/ambulances` | Send another ambulance on a planned trip (`start`, `hospital`, `condition`) |
| POST | `/fleet/ambulances/crossing` | Demo: another ambulance that crosses Ambulance 1's route at a signal at about the same time |
| PUT | `/fleet/ambulances/{vehicle_id}/condition` | The crew sets its patient's condition (applies at once, logged) |
| GET | `/fleet/priority-log` | Every priority setting, newest first |
| GET | `/admin/kpis`, `/admin/delays`, `/admin/trend`, `/admin/route-performance` | Admin dashboard figures for recorded trips (`?days=` for the last N days) |
| GET / PATCH | `/admin/requests`, `/admin/requests/{request_id}` | Recorded trips; one trip with its path and events; correct a trip's status, delay reason or notes |
| GET | `/admin/junctions` | Junctions where ambulances stood still most |
| GET / POST / PATCH | `/admin/grievances`, `/admin/grievances/{id}` | Complaint (grievance) tickets |
| GET | `/plan/area` | The simulated area (trips must start and end inside it) |
| GET | `/plan/hospitals` | Hospitals inside the area |
| GET | `/plan/police` | Police stations inside the area |
| GET | `/plan/search?q=` | Find places in the area (OpenStreetMap Nominatim) |
| POST | `/plan/route` | Fastest ambulance route between a start point and a hospital |
| POST | `/simulation/stop` | Stop the simulation |
| POST | `/simulation/incident` | Demo: a crash blocks a road without signals ahead of the ambulance |
| POST | `/simulation/playback-speed?speed=` | Watch faster: 1, 2, 5 or 10 simulated seconds per second |
| GET | `/police-stations/` | Police stations with their contact numbers (partly hidden) |
| GET | `/police-stations/{station_id}` | One police station |
| PUT | `/police-stations/{station_id}/phone` | Save or remove a station's contact number |
| GET | `/police-stations/calls` | Log of police alerts and phone calls |
| GET | `/admin/stuck-spots` | Where ambulances stood still, grouped by PostGIS |
| GET | `/admin/grievances/summary` | Complaint counts by status, for the sidebar badge |
| GET | `/admin/options` | Allowed values for the admin filters and forms |
| POST | `/complaints` | "Report a problem" from the main screen (crew, 108 control room, hospital, police) |
| POST | `/fleet/preview`, `/fleet/start` | Several ambulance requests: preview their routes, then dispatch them together |
| POST | `/fleet/ambulances/{vehicle_id}/call-police` | The crew is stuck: call the fastest police station now (at most once every 2 min) |
| POST | `/fleet/ambulances/{vehicle_id}/hospital-declines` | Test: the hospital can't take the patient any more; the ambulance is diverted |
| POST | `/plan/call-hospitals` | 108 call centre pre-alerts the nearest hospitals that can treat the patient |
| GET | `/plan/where?latitude=&longitude=` | What is at a point: a hospital, police station or the nearest street |
| POST | `/assistant/intake` | "Describe the emergency": the AI fills in the trip planner (Claude) |
| POST | `/assistant/ask` | "Ask AI" chat about the live simulation (Claude) |
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

The live dashboard uses the `/simulation/*`, `/fleet/*`, `/plan/*`, `/assistant/*`,
`/complaints` and `/police-stations/*` endpoints; the admin dashboard uses `/admin/*`. The older `/ambulances`, `/hospitals`, `/traffic-signals`,
`/routes`, `/emergencies` and `/corridors` endpoints (OSRM based) work alongside them but are
not connected to the SUMO simulation.

---

## Helper scripts

Scripts in `scripts/` were used to build and check the demo route. Run them from the project
root as modules so their imports and relative paths resolve:

```bash
python -m scripts.tests.test_route
python -m scripts.route_building.find_route_signals
```

Checks in `scripts/tests/`:

| Command | What it checks |
|---|---|
| `python -m scripts.tests.regression_check --save/--compare <file>` | Four fixed simulations give the same results after a refactor |
| `python -m scripts.tests.test_admin_data_quality` | Admin KPIs: trips without an AI plan are not counted as on time; interrupted trips stay out of the success rate; delay of late trips |
| `python -m unittest discover -s tests` | Unit tests (`tests/`): route selection, critical rescue, fleet planning, junction priority |
| `python -m scripts.tests.test_mcp` | MCP server end to end (backend must be running) |
| `python -m scripts.tests.test_police_call` | One Twilio test call |
| `python -m scripts.tests.test_simulation_websocket` | Live WebSocket feed (backend must be running) |

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

"Find route" shows how long an ambulance will really take in the current live
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

**Stuck ambulances:** a **critical** patient's ambulance that has stood still for 45 s may
take a detour, if one is still possible and saves at least 90 s (police already close by get
the chance to clear the jam first). Other ambulances wait for the police. Any ambulance
standing still for 2 min files a complaint ticket for the admin automatically
(`backend/services/ambulance_run.py`). The crew can also press **Call police: we're stuck**.

The decision, the police car and the jam appear on the map and in the "Jam prediction (AI)"
card; the MCP tool `get_deadlock_watch` lets an AI agent read it. Rebuild with
`python -m ai.deadlock_experiments`, `python -m ai.train_deadlock`, and measure the effect with
`python -m ai.response_experiments`.

### Police for roads without signals (phone-call alerts)

The corridor clears queues at signals by turning them green. On the parts of the route with
**no signal** nothing can, so the police do it:

1. **When the route is planned** (`route_planner.signalless_stretches` / `police_cover`), the
   route is split into *stretches without signals* (dashed purple on the map). For each
   stretch the police stations are ranked by **driving time on the road network**, not
   straight-line distance (a station across the river can be close as the crow flies but far
   by road). "Find route" lists the stations that cover the route.
2. **While the ambulance drives** (`corridor/police_watch.py`), every 5 s each stretch up to
   3 km ahead is measured: how full of stopped vehicles its roads are and how slowly traffic
   moves. Accidents, roadwork and heavy traffic all show up this way.
3. When a stretch has been **jammed for 15 s**, and the ambulance is close enough that police
   must leave now (within 5 min, or earlier if the police need longer), the fastest station
   gets an **automatic phone call** (Twilio voice, Indian English, read twice):
   *"An ambulance is approaching Ganesh Path in about 4 minutes. Traffic there is heavy.
   Please send officers to clear the road."*
4. A police car drives there in the simulation; on arrival the officers hold side traffic and
   wave the stopped queue through until the ambulance has passed.

**Ambulance stuck anywhere.** When an ambulance has been stopped for most of the last 30 s,
on any road (with or without signals), the watch follows its queue to the car at the front and
sends the nearest police unit to the junction that car is stuck at: often past the start of a
jammed stretch, or at a green signal whose road beyond is full. Police time is estimated at
siren speed (free-flow time × 1.3), not the live speed of the jammed roads.

Alert: `ALERTED → EN_ROUTE → ON_SCENE → PASSED`, or `CANCELLED` when the jam clears by
itself first. The "Police · roads without signals" card shows each alert and its call
(ringing / answered / no answer); the MCP tool `get_police_alerts` lets an AI agent read it.

**Tested:** with a crash placed 1.5 km ahead, the alert went out 16 s later while the
ambulance was still about 2 minutes away.

**Set up calls** (optional; without them alerts still appear on the dashboard):

1. Create a Twilio account, verify your own phone, and enable **India** under
   *Voice → Settings → Geo permissions*.
2. In `.env`: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` and
   `POLICE_ALERT_PHONE` (your **test** phone). Per-station test phones go in
   `police_contacts.json` (see `police_contacts.example.json`; not committed).
3. Test one call: `python -m scripts.tests.test_police_call`. On a trial account, press any
   key when the call starts to hear the message.

**Never use real police numbers for testing:** a test call would be a false emergency. At
most 3 calls are made per trip; `POLICE_CALLS=0` switches calls off.

**Police contacts and the call log are stored in PostgreSQL:**

| Table | What it holds |
|---|---|
| `police_stations` | The 11 stations of the area (from OpenStreetMap, synced at start-up): name, Marathi name, type (station / chowki), street, location (PostGIS point) and **`phone`**, the number called for alerts |
| `police_calls` | Every police alert and its phone call: station, road, ambulance and police arrival times, alert status, number called (partly hidden) and where it came from, call status (ringing / answered / no answer...), stopped vehicles when officers arrived and when they left, vehicles waved through |

Save the stations' (test) numbers either one at a time with
`PUT /police-stations/{station_id}/phone` (e.g. from http://127.0.0.1:8000/docs), or all
at once: copy `police_contacts.example.json` to `police_contacts.json`, fill it in and run
`python -m scripts.set_police_phones`. A station without a number falls back to
`POLICE_ALERT_PHONE`. `GET /police-stations/calls` lists the call log.

**On the map:**

- **Police stations** (click one for details): type (station or chowki), Marathi name, the
  street it is on, what it is doing now (*Available / Alert received / Unit on the way /
  Officers directing traffic*) and how fast its officers could reach the ambulance with live
  traffic (`corridor/police_board.py`). The **Nearby police** card lists the three that could
  reach it first, refreshed every 10 s.
- **Police-managed traffic:** a pulsing blue band over the roads officers are controlling;
  afterwards a teal **"Cleared by police"** band with a before/after, e.g. *stopped cars
  74% → 0% · 25 vehicles waved through · 2 min 44 s on scene*.
- **Chase view (3D):** police cars with a flashing light bar and their station's name,
  station towers coloured by status, the managed (blue) and cleared (teal) roads with
  labels, and a red beacon over a simulated accident.
- **Moving markers:** the ambulance and police cars glide between updates, the ambulance
  turns to face where it drives, police cars flash red/blue, and both leave a short trail.
- **Accident ahead of Ambulance 1** (Ambulances → Test tools, while Ambulance 1 drives): crashed vehicles block
  every lane of a road without signals, far enough ahead that police can get there first.
  A reported accident is alerted **at once** (no waiting for the queue to build up) and the
  station is phoned; officers stuck in the queue within 250 m park and walk, and clear the
  crash after 45 s. Tested: alert 0 s after the crash, officers on scene after 96 s, crash
  cleared after 141 s, ambulance reached the spot about 5 minutes later on a clear road.
  API: `POST /simulation/incident`.

### Drivers giving way to the siren

Drivers ahead of the ambulance, and of police cars on their way to a jam, make room as Indian
law requires (Motor Vehicles Act, section 194E). `SumoGiveWay` in `simulation/sumo/adapters.py`
looks at the vehicles in the siren vehicle's path within 100 m:

| Driver | What they do |
|---|---|
| Moving, road with 2+ lanes | Change to another lane (if there is a gap) |
| Stuck in a queue on a main road, not near the junction | Pull over to the roadside, rejoin once the siren has passed (60 s at most) |
| Stuck inside a junction just ahead | Push on into any gap, keeping a safe distance |
| Otherwise | Stay (no room; police clear such jams) |

Only 75% of drivers react (always the same ones, so runs are repeatable), 2 s after first
hearing the siren. Pulled-over cars are drawn pink at the kerb on the map and in the chase
view, and the **Siren · drivers making room** card counts lane changes, pull-overs and drivers
who did not react. `GIVE_WAY=0` in `.env` switches it off.

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
| `get_police_alerts` | Stretches without signals, police alerts and their phone calls |
| `get_fleet` | Every ambulance, its patient priority, give-way instructions and the referee's decisions |
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

### AI emergency intake ("Describe the emergency")

Above the trip planner, the operator can type the call in their own words, e.g. *"Man collapsed holding his chest near Kasba Peth, family wants a government hospital"*. Claude reads it (structured output: place, patient condition from the app's list, hospital wish, government only, patient already on board, a one-line summary and the reason). The deterministic planner then finds the place on the map, calls the suitable hospitals and plans the route. The operator checks the filled-in trip and presses Start. As the requirements ask, the AI only understands and assists; it never controls signals. `POST /assistant/intake {"text": ...}`.

### Junction status: ACTIVE / PREPARING / STANDBY / NORMAL

The route list shows every junction as J1, J2, … with its corridor role and the time until the ambulance gets there, e.g. "J4 · ACTIVE · 20 s", "J5 · PREPARING · 45 s", "J6 · STANDBY · 70 s". After the ambulance passes, a junction shows "NORMAL" (back to its normal cycle). The roles move forward with the ambulance, as in the requirements' "dynamic emergency corridor".

### Where ambulances get stuck (PostGIS)

Every trip event with a position (stops, signals, police) is also stored as a PostGIS point (`trip_events.location`, a column the database fills in from latitude and longitude, with a spatial index). Admin → Trips shows a map of the spots where ambulances stood still. PostGIS groups stops within 80 m of each other (`ST_ClusterDBSCAN` in metres), and bigger, redder circles lost more time. `GET /admin/stuck-spots?days=`.

### "Ask AI" chat

The **Ask AI** button (bottom right of the main screen) opens a small chat about the running simulation: "Why is Ambulance 2 stopped?", "Which ambulance arrives first?", "Where are the police?". Each question is sent to Claude (`POST /assistant/ask`) with a trimmed snapshot of the live state (every ambulance's stage, ETA, delay reason and next signal, police alerts, recent messages, priority changes). Claude answers from that snapshot only, in one to three blunt sentences, and says so when the snapshot doesn't show the answer.

Needs `ANTHROPIC_API_KEY` in `.env` and `pip install anthropic`. Without a key, the chat shows "AI chat is off". The model is `claude-opus-5-5` at low effort; override with `ASSISTANT_MODEL` / `ASSISTANT_EFFORT`.

### Several ambulances: patient priority and the junction referee

Up to ten ambulances can drive at once. Each has its own corridor engine, ETA, deadlock
response and police watch (`backend/services/ambulance_run.py`). When two of them need the
same signal from crossing roads, one ambulance's green is the other's red: cars queue in
front of the second one, and two ambulances entering a junction together could collide.

**Priority is set by the medic crew,** who know the patient best. They pick the patient's
condition (`corridor/priority.py`), which gives the level:

| Level | Conditions |
|---|---|
| Critical | Cardiac arrest, heart attack, stroke, severe breathing difficulty, unconscious, major trauma / road accident, severe bleeding, severe burns, pregnancy emergency, poisoning / overdose, seizure (ongoing) |
| Urgent | Chest pain, serious injury or illness, fracture, labour (normal delivery), allergic reaction, diabetic emergency, severe abdominal pain, high fever / infection, sick child |
| Stable | Minor injury, stable patient, routine transfer, discharge / going home |

Each crew sets it in its corridor request ("Patient" in the trip planner); the crew changes
it at any time in the **Ambulances** panel. It applies at once, with no approval step. Every
setting is logged (`priority_changes` table and `GET /fleet/priority-log`), so misuse can be
spotted afterwards. A condition rather than a bare "critical" button is specific and
checkable, and tells the other crew why they give way.

**The junction referee** (`corridor/referee.py`) sits between the corridor engines and the
signals. Every second each engine reports the signals ahead: when its ambulance arrives, how
early the signal must switch, and which movements it needs green. Before an engine takes over
a signal it asks the referee:

- **go:** nobody else needs it first; the signal switches for this ambulance.
- **share:** another ambulance holds it, but that green is also green for this one (same
  direction), so both use it.
- **wait (give way):** the signal is, or should first be, green for another ambulance. The
  ambulance is slowed so it reaches the stop line only once the signal can turn green for it
  (in the simulation its top speed is lowered; in reality this is a message to the driver).

*Who goes first* at an uncommitted shared signal follows patient severity when the
green windows overlap: Critical, then Urgent, then Stable. Within a level the
referee minimizes **weighted delay**. Each ambulance needs
the signal green from *arrival − lead* (at once if it is stuck in the queue before the
signal, which only its green can clear) until it has passed. Delays count by priority weight
(Critical 3, Urgent 2, Stable 1, plus up to 0.9 for time already stood still).
Waiting cannot promote a Stable patient above an Urgent or Critical one. When the
ambulances are far apart, the earlier one uses the signal without delaying the other.

A signal is never taken away while it is changing, or when its ambulance is within 20 s of it.
But an ambulance that took a signal to clear the queue in front of it can still be minutes
away once the queue has gone. Another ambulance at the stop line may then **cut in** and cross
first, if that costs clearly less weighted delay (1.5× margin, so decisions do not flip back
and forth). The first ambulance asks for the signal back at once, so its queue keeps
draining.

This priority applies where ambulance routes compete for the same signal. It does
not promise that a Critical ambulance will reach a different hospital before
other crews. A shorter trip or a traffic queue on a road without a signal can
change arrival order. When an ambulance stops, the fleet card and map status
show whether it is giving way at a shared signal, waiting for a signal change,
or caught in a road queue.

For a Critical crew stopped by a road queue, the deadlock responder also
checks for a legal detour around any jammed road still ahead. It diverts only
after 45 s stopped and an estimated 90 s saving; police already close to or
working at the jam get time to clear it first. A jam on the ambulance's
current road cannot be bypassed from that road, so police remain the remedy.

Referee decisions appear in the Ambulances panel, in the AI agent feed and on the map, and the
MCP tool `get_fleet` lets an agent read them.

**Try it:** choose a start, hospital and patient condition, then click **+ Add as Ambulance 1** (then 2, 3…)
in **Several ambulances**. Repeat for another crew. Every accepted route appears in its own
colour before Start; gold markers identify shared signal junctions and show predicted arrivals
and give-way times. The separate **+ Add crossing ambulance** test button creates
a test request. The planner may route it away from the intended crossing.

`POST /fleet/preview` and `POST /fleet/start` use the same deterministic planner. Requests are
accepted in order: each new route is compared with already accepted corridors. Alternatives
include the original route, the fastest route on the same endpoint roads, and routes avoiding
up to six shared signals individually or together. The score is total priority-weighted travel
and waiting time across the fleet, including delays imposed on earlier crews. A long detour
can lose to a short give-way. Each request explains the selected route and estimated wait.

Arrival estimates use capped road speeds, a 1.3 traffic factor, partial endpoint distances,
and a conservative 20-second junction occupancy window. Waits propagate to later junctions;
identical movements can share a green. These estimates are advisory: the live referee retains
control of actual signals and adapts to traffic. Weighted seconds saved are a fleet score,
not a claim of equivalent wall-clock time saved by one ambulance.

During a run, choose a new crew's start and hospital in the planner and use **Trip planned above**
(under "Add an ambulance now"). The backend compares it with the remaining routes and current conditions of active
ambulances. **Crossing ambulance** adds a test ambulance during a run.

Validation: `.venv/bin/python -m unittest discover -s tests -v` covers route selection,
priority, downstream waits, real-network movement legality, shared markers and matching preview
and dispatch routes. The historical live-referee results below predate coordinated routing.

**Tested** (headless, demo route, normal traffic), with the crossing ambulance meeting
Ambulance 1 (Urgent) at Babasaheb Ambedkar Marg × Jawaharlal Nehru Marg:

- The single-ambulance trip is unchanged by the referee (784 s, 4 stops, the same as before).
- Crossing ambulance *Stable*: Ambulance 1 was stuck in a queue 800 m back and took the signal
  first. Once its queue had cleared it was still ~150 s away, so the stable ambulance at the
  stop line cut in and crossed.
- Crossing ambulance *Cardiac arrest*: Ambulance 1 gave way to it ("has the higher
  priority").

These are single runs, not a measured comparison: adding an ambulance changes the whole
traffic, so trip times vary a lot from run to run. Measuring the effect needs many paired
runs, as in `ai/response_experiments.py`.

### Dispatch: the 108 workflow

Built around how a 108 emergency call runs in India: call → the fastest free ambulance →
corridor to the patient → time at the scene → pre-alerted hospital → corridor to the hospital.
Most real delay is outside the drive itself: picking the wrong ambulance, and hospitals that turn
the patient away at the door. These steps target both. All the data below is simulated
(the fleet, hospital capabilities and availability); it is not real 108 or hospital data.

**108 dispatch** (`corridor/dispatch.py`, on by default: "108 dispatch" in the planner). Nine
ambulances wait at stations across the area: 3 **ALS** (Advanced Life Support: paramedic,
ventilator) and 6 **BLS** (basic). The call centre sends the free ambulance that is fastest by
driving time (AI trip-time estimate for the current traffic level), not the nearest one in a
straight line. A Critical patient gets an ALS ambulance, unless the nearest BLS is more than
3 min faster. Other patients get the fastest BLS, so the ALS ambulances stay free, unless an
ALS is more than 2 min faster. Ambulances on a call are skipped, including ones booked
earlier in the list. The plan shows the ambulance chosen and, under *Why this ambulance*,
every one considered. The ambulance drives to the patient, stays 3 min, then drives to the
hospital, with the corridor, police and priority on both legs.

**What the patient needs, not "a bed"** (`corridor/hospital_care.py`). Each condition needs one
resource: heart attack → cath lab, stroke or seizure → CT scan + neurologist, road accident or
severe bleeding → trauma team, cardiac arrest or breathing problems → ICU bed, fracture →
orthopaedic surgeon, labour → labour room, sick child → paediatric team, otherwise an emergency
doctor. Hospital types are worked out from their names (OpenStreetMap has no capability data).
Each type has some of each resource, about 30% already in use at random when a run starts, and
each ambulance sent there holds one. Each hospital is also **Government** or **Private**:
108's default is a government hospital (free treatment). Tick *Government only* to limit the
list to those; untick it for the family's choice.

**Hospital pre-alert.** In the planner, the 108 call centre calls the nearest suitable hospitals
by driving time until one has what the patient needs ("Joshi Hospital: ✗ cath lab busy →
Surya Sahyadri: ✓ cath lab ready"). During the trip, once the crew has the patient (or when a
straight trip sets off), the call centre pre-alerts that hospital ("108 → Ruby Hall Clinic:
Ambulance 1's patient in ~6 min. Cath lab ready.").

**Diverting instead of being turned away.** If the hospital can't take the patient, the
ambulance is diverted **at once, while driving**, to the nearest hospital that can (by live
driving time), keeping the pickup if the patient isn't on board yet. This happens when:
- the crew changes the condition (chest pain → heart attack: now needs a cath lab), or
- the hospital declines (test: *Details → Test: hospital can't take patient*).

The message gives the time difference ("Ruby Hall Clinic: no cath lab free. Ambulance 1 →
Jehangir Hospital (+1 min)"). The trip record has *Hospital pre-alert* and *Diverted* events.
API: `POST /fleet/ambulances/{id}/hospital-declines`, `GET /plan/hospitals?condition=&government_only=`,
`POST /plan/call-hospitals`.

**Handover at the hospital.** After arrival the patient is handed over: about 2 min when the
hospital was pre-alerted (the team is waiting at the door), about 8 min when it wasn't (the staff
have to be found first). These two times are assumptions for the simulation, not measured
values. The ambulance card shows *At hospital · handover, 1 min 20 s left*.

**Back in service.** After the handover the 108 ambulance is free again and drives back to its
station (map: small ambulance markers, ALS red and BLS blue, ringed while handing over or
returning). It can take a new call on the way: 108 dispatch counts it from where it is
("returning, sent from where it is"). The simulation stops by itself once every ambulance has
handed over its patient and is back at its station.

**108 timings (Admin page).** Each trip record has the 108 ambulance and its timeline:
- **to the patient:** dispatch → reaching the patient
- **at the scene:** time spent at the patient
- **to hospital:** patient on board → hospital door
- **handover**
- **dispatch to handover:** the total

It also notes whether the hospital was pre-alerted and whether the ambulance was diverted.
*Admin → Trips* shows the averages (with the share of trips that were pre-alerted and the number
diverted); each trip's page shows its own timeline, with *Handover* in the event list. Older trips,
recorded before this existed, have no timeline.

**Critical patients:** police are sent after 10 s stuck (20 s for others), and police never
hold back a road any ambulance still needs (holding a side road for one ambulance used to
block another one on it).

### Simulated area

The default area is the first one again (`SIM_AREA=original`, 4.2 × 3.3 km): trips are short now that
patients go to the nearest suitable hospital, and it is lighter to run. The bigger area
(`SIM_AREA=2x`, `simulation/sumo/pune_network_v2/area_2x/`) covers about
5.9 × 4.7 km of central Pune: about 16,300 roads, 65 signal junctions, 106 hospitals and 15
police stations. It was built on 2026-10-01 from OpenStreetMap (downloaded in tiles from the
OSM API, merged into `osm_bbox.osm.xml.gz`) with the same netconvert options as the first
area, and traffic regenerated with `ai.scenarios` (seed 1000).

The same traffic density as the first area gridlocked the bigger one: more through-traffic
reaches the centre. Its densities are two-thirds of the first area's (light 9, normal 16,
heavy 23 vehicles per hour per lane-km instead of 14 / 24 / 34), chosen so that normal traffic
matches the first area: about 10–12% of vehicles stopped and 18.5 km/h on average. The
simulation still needs more computing time than the first area (about 1.7× the vehicles). The AI models were
trained on the first area; their inputs (speeds, queues, distances) carry over, but they have
not been retrained here. `SIM_AREA=original` in `.env` switches back to the first
4.2 × 3.3 km area.

### Pages, trip records and grievances

The frontend has four pages:

| Page | Address | What it is for |
|---|---|---|
| Live map | `#/` | Plan, start and watch trips |
| Control room | `#/control-room` | Every ambulance live, the referee's decisions, the priority log, police contacts; override a patient's priority or stop the simulation (logged as "Control room") |
| Admin dashboard | `#/admin` | Recorded trips over 7 days / 30 days / all time: KPIs, delay reasons, trends, route performance, problem junctions, and grievance tickets |
| Trip page | `#/admin/trip/<request id>` | One trip on a map (planned route, path driven, stops) with its timeline |

**Every ambulance's trip is recorded** (`backend/services/trip_recorder.py`,
`backend/services/admin_service.py`): a request row when it sets off (`ambulance_requests`,
one per ambulance, e.g. `REQ-20261001-115338-A2`), and when it ends its path, stops of 10 s or
more, signals switched green, police alerts, accidents on its route, re-routes and give-ways to
other ambulances (`trip_events`), plus a route summary (`route_optimization_logs`). A trip that
arrives is `COMPLETED`, with its delay against the AI trip-time estimate and a delay reason
(the admin can correct it); a simulation stopped or failing before arrival records `CANCELLED`
/ `FAILED`, and a trip cut off because the backend itself stopped is marked `INTERRUPTED` at the
next start-up. Grievances (`grievances`) can be raised by users, drivers, hospitals or police and
linked to a trip. The tables are created at start-up (`database/admin_tables.sql` has the SQL).

**Camera** (top right of the map): **Follow** keeps the selected ambulance centred, **All** keeps
every active ambulance in view (the default when several are driving), **Free** leaves the map
where you put it.

The live traffic, deadlock and police cards and the AI agent's messages are in the side panel
("Live status"), so they never cover the map.

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
| Driver reaction time | 1.0 s (`tau`) | Not below the 1 s simulation step; at 0.9 s SUMO could not keep cars apart and reported thousands of collisions in heavy traffic |
| Ambulance speed | cruises ~40 km/h (35–45 km/h), never above 45 km/h | With the siren it is faster than every normal car, so cars never overtake it; two-wheelers, autos, pedestrians and parked vehicles still keep it well below the limit |
| Where traffic goes | weighted towards trunk / primary / secondary roads | Real traffic concentrates on arterials |
| Re-routing | 50% of drivers re-route around jams | Navigation-app behaviour |
| Blocked junctions | A car standing 20 s inside a junction no longer blocks others | Drivers squeeze past a car stuck in the box; without it gridlocks held the ambulance for up to 5 min |
| Traffic levels | light / normal / heavy = 14 / 24 / 34 vehicles per hour per lane-km | Heavier levels make longer queues at signals |

**To calibrate further,** check the real Google Maps travel time for the route
(Shukrawar Peth → Ruby Hall Clinic) at a few times of day and adjust `TRAFFIC_LEVELS` in
`ai/scenarios.py` and `speedFactor` in `pune_vtypes.add.xml` until the simulated normal-car
times match.

---

## Admin dashboard

Open http://localhost:5173/#/admin. It is for **oversight**: the corridor never waits for a
person to approve anything; the admin watches, reviews and corrects afterwards.

Every simulated trip is recorded automatically (`backend/services/trip_recorder.py`, saved once
when the trip ends so the simulation never waits for the database):

| Table | What it holds |
|---|---|
| `ambulance_requests` | One row per trip: start, hospital, status (`IN_PROGRESS / COMPLETED / CANCELLED / FAILED / INTERRUPTED`), dispatch and arrival, response time, AI planned time and `plan_status`, delay and its reason |
| `route_optimization_logs` | Signals cleared, police alerts and police on scene, re-routes, accidents, fast arrival |
| `trip_events` | Stops, signals, police, accidents and re-routes in time order, plus the planned route and the driven track |
| `grievances` | Complaint tickets from users, drivers, hospitals or police |

The admin page has a sidebar with these sections:

- **Dashboard:** every ambulance right now on a map, the key figures and charts.
- **Complaints:** tickets from "Report a problem" (crew, 108 control room, hospitals, police)
  and the ones the app files itself (e.g. an ambulance stuck for 2 min); set the status and add
  a note when solved.
- **Trips:** every trip (click one for its map and timeline) and the junctions and spots where
  ambulances lost the most time (stuck spots grouped with PostGIS).
- **Who went first:** when two ambulances needed the same signal, who got the green, who
  waited, and why (see [Several ambulances](#several-ambulances-patient-priority-and-the-junction-referee)).
- **Priority changes:** every change of a patient's condition, from what, to what and by whom.
- **Police:** police called to clear roads, what happened, and the stations' phone numbers
  (editable).

What the figures show:

- **KPIs:** total trips, success rate, on-time rate, average time to hospital, **average delay
  of late trips** (how late the trips over 1 min late were; "On time" says how many), open
  complaints, and for 108 trips where the time goes (to the patient, at the scene, to the
  hospital, handover). Trips without an AI planned time are counted separately, never as
  "on time" or in the delay. **Interrupted** trips (backend stopped mid-trip) are shown but
  left out of the success rate.
- **Charts:** delay reasons (`TRAFFIC`, `BAD_ROUTE`, `ACCIDENT`, set automatically;
  `VEHICLE_ISSUE`, `DRIVER_DELAY`, `OTHER` set by an admin), signals and police, time by traffic
  level, busiest hospitals, and recent trips (on time or late).
- **Request log** with filters; an admin can correct a trip's delay reason, status or notes.
- **Problem junctions:** where ambulances stood still longest.
- **Trip page** (`#/admin/trip/<id>`): map with the planned route, the driven path and the
  events, plus a timeline.
- **Complaints:** filter, search, change status and priority, add a resolution note; each
  links to its trip.

Filter by period with the last N days. Trips left `IN_PROGRESS` by a stopped backend are marked
`INTERRUPTED` at the next start-up (not counted in the success rate).

---

## Known limitations

- **Up to ten independent ambulance requests.** Any start and hospital inside the
  simulated area (4.2 × 3.3 km of central Pune by default, 5.9 × 4.7 km with
  `SIM_AREA=2x`; see *Simulated area*) can be chosen. Coordination uses estimated
  arrivals and a bounded set of alternatives, not a globally optimal fleet search or live
  congestion forecasts. Already accepted routes are not automatically re-optimized when a
  new request arrives or a patient's condition changes; the live referee adapts priority.
- **The referee only handles shared signals.** One ambulance's held green also makes cars
  queue on crossing roads that another ambulance may use later; that is not yet taken into
  account. The police watch and deadlock response of each ambulance do not coordinate with
  the other ambulances' (two could ask the same station).
- The map, route timeline and chase camera can focus on any ambulance. The
  traffic-wide AI feed and accident simulation still report Ambulance 1's run;
  the others appear on the map and in the Ambulances panel.
- **Vehicles can briefly overlap inside junctions.** SUMO's simplified junction model lets
  vehicles pass through each other there for 1–2 seconds (measured: 1–2 vehicles per
  ambulance trip in heavy traffic), mostly when traffic squeezes past a vehicle standing in
  the junction (`--ignore-junction-blocker`) or the ambulance does not wait for a car already
  inside one. Real crashes are not simulated. Making everyone wait instead made the
  ambulance up to 55 s slower without removing the overlaps, so it was not changed.
- **Patients next to the map's edge.** Some roads at the edge of the simulated map only lead
  off it, or only come in. The ambulance then stops on the nearest road it can reach and
  leave, up to 300 m away (the crew carries the patient there); further away, planning says
  so and asks for a pin a little further inside the area.
- **The ETA model was trained mostly on the demo route.** It works on other routes but is less
  accurate there until it is retrained on many routes.
- **Signal timings are SUMO's defaults,** not measured Pune timings. Some OpenStreetMap
  signals were dropped when the network was built.
- **Simulated traffic, not real cars.** Live TomTom data sets the speeds and traffic level, but
  the individual vehicles, signals and the ambulance are simulated.
- **Older database endpoints are separate.** `/ambulances`, `/hospitals/nearest`, `/routes`,
  `/emergencies` and `/corridors` (OSRM based) are not connected to the SUMO simulation.
- **Public services.** Place search and the older route endpoints depend on the public
  Nominatim, OSRM and Overpass servers. If those are down, the endpoints return a `502` error.
- **No authentication** on the API or the admin dashboard. It's intended for local demos only.
- **Police cars can get stuck in the jam they are sent to.** Drivers give way to their siren
  and officers stuck within 250 m park and walk, but a police car reaching a long jam from
  behind can still lose minutes in the queue.
- **Short jams are caught late.** In heavy traffic most jams on stretches without signals
  form and clear within seconds; the ones that last often form just ahead of the ambulance,
  so their alert is marked "police may arrive after the ambulance".
- **Re-routing rarely helps.** Both the deadlock re-route and the stuck re-route made most
  test trips slower (see [Deadlock response](#deadlock-response-re-route-or-send-police)).

## Roadmap

- Admin: live system status panel and a log of every AI decision
- Jam prediction and clearing queues ahead of the ambulance
- Delay caused to other traffic in the with/without comparison
- Fastest route using live (simulated or real) traffic
- Retrain the ETA model on many routes
- Admin dashboard: every ambulance, the referee's decisions and the priority log, with
  manual overrides
- Measure coordinated routing and the referee's effect over many paired runs
