# AI-Powered Emergency Corridor 🚑

A traffic simulation where an ambulance gets a **green corridor**: traffic lights ahead of it
turn green *before* it arrives, so it never has to stop at a red light.

The system runs on a real road network of **Pune, India** (from OpenStreetMap), simulated in
**SUMO**. A **FastAPI** backend watches the ambulance every second and controls the traffic
lights, and a **React** dashboard shows it all live on a map.

> **Current scope:** this is a demo built around **one fixed, tested route**. `ambulance_01`
> drives about 3.7 km (the fastest route) to **Ruby Hall Clinic** through realistic Pune traffic (hundreds to thousands of
> vehicles). Signal control is rule-based; the arrival time (ETA) comes from a machine-learning
> model trained on simulated trips (see [AI layer](#ai-layer)). Up to ten ambulances can drive
> at once; a junction referee decides who gets a shared signal, by patient priority
> (see [Several ambulances](#several-ambulances-patient-priority-and-the-junction-referee)).

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
   | Turns with only a give-way green | If the ambulance's movement never has a protected green (e.g. a right turn across oncoming traffic), it gets an emergency green instead: every movement from its own approach road green, every other direction red |

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
│       ├── ambulance_run.py       One ambulance's trip: its corridor, ETA, police watch
│       ├── priority_log.py        Saves every patient-priority setting (priority_changes)
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
│   ├── referee.py               Several ambulances: who gets a shared signal first
│   ├── priority.py              Patient conditions → priority levels and weights
│   ├── police_watch.py          Police alerts for jams on stretches without signals
│   └── feed.py                  Builds the live state for the dashboard
│
├── simulation/sumo/
│   ├── adapters.py              SUMO versions of the 3 connectors (the only code using traci)
│   ├── route_planner.py         Fastest ambulance route; stretches without signals + police cover
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
| POST | `/simulation/start` | Start the SUMO simulation (optional body: `start` and `hospital` points and the patient's `condition`; default: demo trip; `?condition=` for the demo trip) |
| GET | `/fleet/` | Every ambulance of the run, give-way instructions, referee decisions |
| GET | `/fleet/conditions` | Patient conditions the crew can choose, with their priority level |
| POST | `/fleet/ambulances` | Send another ambulance on a planned trip (`start`, `hospital`, `condition`) |
| POST | `/fleet/ambulances/crossing` | Demo: another ambulance that crosses Ambulance 1's route at a signal at about the same time |
| PUT | `/fleet/ambulances/{vehicle_id}/condition` | The crew sets its patient's condition (applies at once, logged) |
| GET | `/fleet/priority-log` | Every priority setting, newest first |
| GET | `/admin/kpis`, `/admin/delays`, `/admin/trend`, `/admin/route-performance` | Admin dashboard figures for recorded trips (`?days=`) |
| GET / PATCH | `/admin/requests`, `/admin/requests/{request_id}` | Recorded trips; one trip with its path and events; correct a delay reason |
| GET | `/admin/junctions` | Junctions where ambulances stood still most |
| GET / POST / PATCH | `/admin/grievances` | Grievance tickets |
| GET | `/plan/area` | The simulated area (trips must start and end inside it) |
| GET | `/plan/hospitals` | Hospitals inside the area |
| GET | `/plan/search?q=` | Find places in the area (OpenStreetMap Nominatim) |
| POST | `/plan/route` | Fastest ambulance route between a start point and a hospital |
| POST | `/simulation/stop` | Stop the simulation |
| POST | `/simulation/incident` | Demo: a crash blocks a road without signals ahead of the ambulance |
| GET | `/police-stations/` | Police stations with their contact numbers (partly hidden) |
| PUT | `/police-stations/{station_id}/phone` | Save or remove a station's contact number |
| GET | `/police-stations/calls` | Log of police alerts and phone calls |
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
   by road). "Find fastest route" lists the stations that cover the route.
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
- **Simulate accident ahead** (Ambulances sidebar demo control, while Ambulance 1 drives): crashed vehicles block
  every lane of a road without signals, far enough ahead that police can get there first.
  A reported accident is alerted **at once** (no waiting for the queue to build up) and the
  station is phoned; officers stuck in the queue within 250 m park and walk, and clear the
  crash after 45 s. Tested: alert 0 s after the crash, officers on scene after 96 s, crash
  cleared after 141 s, ambulance reached the spot about 5 minutes later on a clear road.
  API: `POST /simulation/incident`.

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
/ `FAILED`. Grievances (`grievances`) can be raised by users, drivers, hospitals or police and
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

## Known limitations

- **Up to ten independent ambulance requests.** Any start and hospital inside the
  simulated area (about 6 × 5 km of central Pune; see *Simulated area*) can be chosen. Coordination uses estimated
  arrivals and a bounded set of alternatives, not a globally optimal fleet search or live
  congestion forecasts. Already accepted routes are not automatically re-optimized when a
  new request arrives or a patient's condition changes; the live referee adapts priority.
- **The referee only handles shared signals.** One ambulance's held green also makes cars
  queue on crossing roads that another ambulance may use later; that is not yet taken into
  account. The police watch and deadlock response of each ambulance do not coordinate with
  the other ambulances' (two could ask the same station).
- The map, route timeline and chase camera can focus on any ambulance. The
  traffic-wide AI feed and accident simulation still report Ambulance 1's run.
  the others appear on the map and in the Ambulances panel.
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
- **Police cars get stuck in the jam they are sent to.** In the simulation a police car
  reaching a jam from behind waits in the same queue (one test: 398 s instead of ~100 s).
  Real officers would use the other lane or the footpath.
- **Short jams are caught late.** In heavy traffic most jams on stretches without signals
  form and clear within seconds; the ones that last often form just ahead of the ambulance,
  so their alert is marked "police may arrive after the ambulance".

## Roadmap

- Human approval step for agent requests (LangGraph interrupts)
- Jam prediction and clearing queues ahead of the ambulance
- Delay caused to other traffic in the with/without comparison
- Fastest route using live (simulated or real) traffic
- Admin dashboard: every ambulance, the referee's decisions and the priority log, with
  manual overrides
- Measure coordinated routing and the referee's effect over many paired runs
