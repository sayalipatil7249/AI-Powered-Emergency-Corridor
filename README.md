# AI-Powered Emergency Corridor 🚑

A traffic simulation where an ambulance gets a **green corridor**: traffic lights ahead of it
turn green *before* it arrives, so it never has to stop at a red light.

The system runs on a real road network of **Pune, India** (from OpenStreetMap), simulated in
**SUMO**. A **FastAPI** backend watches the ambulance every second and controls the traffic
lights, and a **React** dashboard shows it all live on a map.

> **Current scope:** one ambulance (`ambulance_01`) at a time, on any start point and hospital
> inside a ~5 × 4 km area of central Pune, through realistic traffic (hundreds to thousands of
> vehicles, optionally copied from live TomTom data). The default demo trip is Shukrawar Peth →
> **Ruby Hall Clinic** (about 3.7 km). Machine-learning models predict the arrival time, when
> each signal must switch, the trip time before departure and deadlocks ahead
> (see [AI layer](#ai-layer)). Police stations along the route are alerted (and phoned) for
> jams on roads without signals, and every trip is recorded for the
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
│       ├── trip_recorder.py       Records each trip's events and driven track
│       ├── admin_service.py       KPIs, delay reasons, problem junctions, grievances
│       ├── police_station_service.py  Police stations and their contact numbers
│       ├── police_notifier.py     Twilio voice calls to police stations
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
│   ├── engine.py                Finds signals ahead and switches each one just in time
│   ├── safety.py                Rules for early-green requests from AI agents
│   ├── response.py              Deadlock response: police or re-route; stuck re-routing
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
│       ├── components/          Trip planner, live status cards (corridor, AI response, police,
│       │                        live traffic, drivers making room, AI agent feed), moving markers
│       └── admin/               Admin dashboard: KPIs, charts, route performance,
│                                problem junctions, grievances, trip detail page
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

While it runs you can:

- switch between the **2D map** and the **3D chase view** (camera behind the ambulance);
- change the **playback speed** (1×, 2×, 5×, 10×) to watch the trip faster. The simulated
  speeds do not change;
- click **Simulate accident ahead** to test the police response (see
  [Police for roads without signals](#police-for-roads-without-signals-phone-call-alerts)).

The **admin dashboard** is at http://localhost:5173/#/admin (see
[Admin dashboard](#admin-dashboard)).

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
| GET | `/admin/kpis`, `/admin/delays`, `/admin/trend`, `/admin/route-performance` | Admin analytics (`?days=` for the last N days) |
| GET / PATCH | `/admin/requests`, `/admin/requests/{request_id}` | Recorded trips; correct a trip's status, delay reason or notes |
| GET | `/admin/junctions` | Problem junctions (where ambulances stood still longest) |
| GET / POST / PATCH | `/admin/grievances…` | Grievance tickets and their summary |
| GET | `/admin/options` | Allowed values for the admin filters and forms |
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

The live dashboard uses the `/simulation/*`, `/plan/*` and `/police-stations/*` endpoints; the
admin dashboard uses `/admin/*`. The older `/ambulances`, `/hospitals`, `/traffic-signals`,
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
| `python -m scripts.tests.test_admin_data_quality` | Admin KPIs: trips without an AI plan are not counted as on time |
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

**Stuck re-routing:** when the ambulance has stood still for 40 s, or an accident blocks the road
ahead, it takes a way round from its current road if the trip-time model says it saves at least
30 s (at most 3 times per trip). In tests it made most trips slower; set `MAX_STUCK_REROUTES = 0`
in `corridor/response.py` to switch it off.

The decision, the police car and the jam appear on the map and in the "AI deadlock watch"
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
   wave stuck vehicles through until the ambulance has passed.

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
- **Simulate accident ahead** (button, while the ambulance drives): crashed vehicles block
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
| Driver reaction time | 1.0 s (`tau`) | Not below the 1 s simulation step; at 0.9 s SUMO could not keep cars apart and reported thousands of collisions in heavy traffic |
| Ambulance speed | cruises ~40 km/h (35–45 km/h), never above 45 km/h | With the siren it is faster than every normal car, so cars never overtake it; two-wheelers, autos, pedestrians and parked vehicles still keep it well below the limit |
| Where traffic goes | weighted towards trunk / primary / secondary roads | Real traffic concentrates on arterials |
| Re-routing | 50% of drivers re-route around jams | Navigation-app behaviour |
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
| `ambulance_requests` | One row per trip: start, hospital, status (`IN_PROGRESS / COMPLETED / CANCELLED / FAILED`), dispatch and arrival, response time, AI planned time and `plan_status`, delay and its reason |
| `route_optimization_logs` | Signals cleared, police alerts and police on scene, re-routes, accidents, fast arrival |
| `trip_events` | Stops, signals, police, accidents and re-routes in time order, plus the planned route and the driven track |
| `grievances` | Complaint tickets from users, drivers, hospitals or police |

What the dashboard shows:

- **KPIs:** total trips, success rate, on-time rate, average response time and delay, open
  grievances. Trips without an AI planned time are counted separately, never as "on time".
- **Delay reasons** (`TRAFFIC`, `BAD_ROUTE`, `ACCIDENT`, set automatically; `VEHICLE_ISSUE`,
  `DRIVER_DELAY`, `OTHER` set by an admin) and **requests / response time per day** (charts).
- **Request log** with filters; an admin can correct a trip's delay reason, status or notes.
- **Problem junctions:** where ambulances stood still longest.
- **Trip page** (`#/admin/trip/<id>`): map with the planned route, the driven path and the
  events, plus a timeline.
- **Grievances:** create, filter, search, change status and priority, add a resolution note.

Filter by period with the last N days. Trips left `IN_PROGRESS` by a stopped backend are marked
`FAILED` at the next start-up.

---

## Known limitations

- **One ambulance at a time.** Any start and hospital inside the simulated area (about
  5 × 4 km of central Pune, the outline highlighted on the map) can be chosen; the fastest
  route uses speed limits, not live traffic.
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
- Multiple ambulances with priority handling
