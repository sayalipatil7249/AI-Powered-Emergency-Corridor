import { useEffect, useState } from "react";

import MapView from "./MapView";
import DashboardHeader from "./components/DashboardHeader";
import LiveStatusPanel from "./components/LiveStatusPanel";
import StoryBar from "./components/StoryBar";
import TripPanel from "./components/TripPanel";
import TripPlanner from "./components/TripPlanner";
import {
  DEMO_HOSPITAL_NAME,
  getRouteSignalStatuses,
  getStory,
} from "./routeStatus";

// Backend address. Override with VITE_API_URL in frontend/.env if needed.
// 127.0.0.1 rather than "localhost": the backend listens on IPv4 only, and
// "localhost" can resolve to IPv6 and reach another app on port 8000.
const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const WS_URL = API_URL.replace(/^http/, "ws");
const RECONNECT_DELAY_MS = 3000;


function App() {
  const [simulationState, setSimulationState] = useState(null);
  const [connected, setConnected] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  // Only used for problems with the Start / Stop buttons.
  const [notice, setNotice] = useState("");

  // Trip planning: start point, hospital and the planned route.
  const [hospitals, setHospitals] = useState([]);
  const [tripStart, setTripStart] = useState(null);
  const [tripHospital, setTripHospital] = useState(null);
  const [pickMode, setPickMode] = useState(false);
  const [plan, setPlan] = useState(null);
  // Once a planned trip has been run, the map shows that run (also after
  // it ends) instead of the plan preview.
  const [planStarted, setPlanStarted] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [planError, setPlanError] = useState("");
  const [area, setArea] = useState(null);
  const [policeStations, setPoliceStations] = useState([]);
  const [incidentBusy, setIncidentBusy] = useState(false);

  const simulationStatus = simulationState?.status;
  const running = ["starting", "warming_up", "running"].includes(
    simulationStatus
  );


  // The simulated part of Pune: trips must start and end inside it.
  useEffect(() => {
    fetch(`${API_URL}/plan/area`)
      .then((response) => response.json())
      .then(setArea)
      .catch(() => {});
  }, []);

  // Police stations that can be sent to clear a jam.
  useEffect(() => {
    fetch(`${API_URL}/plan/police`)
      .then((response) => response.json())
      .then(setPoliceStations)
      .catch(() => {});
  }, []);

  // Hospitals in the simulated area; Ruby Hall Clinic (demo) by default.
  useEffect(() => {
    fetch(`${API_URL}/plan/hospitals`)
      .then((response) => response.json())
      .then((list) => {
        setHospitals(list);
        setTripHospital(
          list.find((item) => item.name === DEMO_HOSPITAL_NAME) || list[0]
        );
      })
      .catch(() => setPlanError("Could not load the hospital list."));
  }, []);

  // A new start or hospital makes the old plan out of date.
  const changeStart = (place) => {
    setTripStart(place);
    setPlan(null);
    setPlanStarted(false);
    setPlanError("");
    setPickMode(false);
  };

  const changeHospital = (hospital) => {
    setTripHospital(hospital);
    setPlan(null);
    setPlanStarted(false);
    setPlanError("");
  };

  const useDemoRoute = () => {
    setTripStart(null);
    setPlan(null);
    setPlanStarted(false);
    setPlanError("");
    setPickMode(false);
    setTripHospital(
      hospitals.find((item) => item.name === DEMO_HOSPITAL_NAME) || null
    );
  };

  const tripBody = () =>
    JSON.stringify({ start: tripStart, hospital: tripHospital });

  const findRoute = async () => {
    try {
      setPlanning(true);
      setPlanError("");

      const response = await fetch(`${API_URL}/plan/route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: tripBody(),
      });
      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.detail || "Could not plan the route.");
      }
      setPlan(result);
      setPlanStarted(false);
    } catch (error) {
      setPlan(null);
      setPlanError(error.message);
    } finally {
      setPlanning(false);
    }
  };

  const startSimulation = async () => {
    try {
      setStarting(true);
      setNotice("");

      // With a planned trip the ambulance takes that route;
      // otherwise the tested demo route.
      const response = await fetch(`${API_URL}/simulation/start`, {
        method: "POST",
        ...(plan && tripStart
          ? {
              headers: { "Content-Type": "application/json" },
              body: tripBody(),
            }
          : {}),
      });

      if (!response.ok) {
        throw new Error("Failed to start simulation");
      }

      const result = await response.json();

      if (result.status === "stopping") {
        setNotice(
          "The previous simulation is still stopping. Try again in a moment."
        );
      } else if (plan && tripStart) {
        setPlanStarted(true);
      }
    } catch (error) {
      console.error("Simulation start error:", error);
      setNotice("Could not start the simulation. Is the backend running?");
    } finally {
      setStarting(false);
    }
  };


  // Demo: a crash blocks a road without signals ahead of the ambulance
  // (backend: police alert, phone call, police clearing it).
  const simulateIncident = async () => {
    try {
      setIncidentBusy(true);
      setNotice("");
      const response = await fetch(`${API_URL}/simulation/incident`, {
        method: "POST",
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "Could not simulate an accident.");
      }
    } catch (error) {
      setNotice(error.message);
    } finally {
      setIncidentBusy(false);
    }
  };

  // Watch the trip faster (the ambulance's simulated speed is unchanged).
  const changePlaybackSpeed = async (speed) => {
    try {
      await fetch(`${API_URL}/simulation/playback-speed?speed=${speed}`, {
        method: "POST",
      });
    } catch (error) {
      console.error("Playback speed error:", error);
    }
  };

  const stopSimulation = async () => {
    try {
      setStopping(true);
      setNotice("");

      const response = await fetch(`${API_URL}/simulation/stop`, {
        method: "POST",
      });

      if (!response.ok) {
        throw new Error("Failed to stop simulation");
      }
    } catch (error) {
      console.error("Simulation stop error:", error);
      setNotice("Could not stop the simulation. Is the backend running?");
    } finally {
      setStopping(false);
    }
  };


  useEffect(() => {
    let websocket = null;
    let reconnectTimer = null;
    let closedByPage = false;

    // Connect, and keep retrying every few seconds if the backend
    // is not up yet or restarts.
    const connect = () => {
      websocket = new WebSocket(`${WS_URL}/simulation/ws`);

      websocket.onopen = () => {
        setConnected(true);
      };

      websocket.onmessage = (event) => {
        setSimulationState(JSON.parse(event.data));
      };

      websocket.onerror = (error) => {
        console.error("WebSocket error:", error);
      };

      websocket.onclose = () => {
        setConnected(false);

        if (!closedByPage) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    };

    connect();

    return () => {
      closedByPage = true;
      clearTimeout(reconnectTimer);
      websocket?.close();
    };
  }, []);


  const ambulance = simulationState?.ambulance;
  const vehicles = simulationState?.vehicles || [];

  const routeStatuses = getRouteSignalStatuses({
    routeSignals: simulationState?.route_signals || [],
    corridor: simulationState?.corridor || [],
    signals: simulationState?.signals || [],
    ambulance,
  });

  // A new plan (not yet started) replaces the last trip on screen.
  const showPlan = !running && plan && !planStarted;

  // Names come from the running / last trip, otherwise from the planner.
  const liveTrip =
    !showPlan && (running || ambulance) ? simulationState?.trip : null;
  const hospitalName =
    liveTrip?.hospital_name || tripHospital?.name || DEMO_HOSPITAL_NAME;
  const startName =
    liveTrip?.start_name || (plan && tripStart?.name) || "Shukrawar Peth";

  const story = getStory({
    connected,
    simulationState,
    routeStatuses,
    hospitalName,
  });

  const liveRoute = simulationState?.route || [];
  // Where the trip starts and ends, shown before and during the run.
  // The running / finished trip's own points; otherwise the planner's
  // choice (not the last trip's, or every hospital would appear there).
  const tripPoints = liveTrip
    ? {
        start: simulationState?.trip?.start_point,
        hospital: simulationState?.trip?.hospital_point,
      }
    : {
        start: tripStart && [tripStart.latitude, tripStart.longitude],
        hospital:
          tripHospital && [tripHospital.latitude, tripHospital.longitude],
      };
  const previewPlan = showPlan ? plan : null;
  const shownAmbulance = showPlan ? null : ambulance;
  const shownStatuses = showPlan ? [] : routeStatuses;

  return (
    <div className="app">
      <DashboardHeader
        connected={connected}
        onStartSimulation={startSimulation}
        onStopSimulation={stopSimulation}
        starting={starting}
        stopping={stopping}
        running={running}
        tripLabel={`Ambulance from ${startName} to ${hospitalName}, Pune`}
        playbackSpeed={simulationState?.playback_speed}
        onPlaybackSpeedChange={changePlaybackSpeed}
      />

      <main className="content">
        <MapView
          ambulance={shownAmbulance}
          vehicles={vehicles}
          route={previewPlan ? previewPlan.geometry : liveRoute}
          routeStatuses={shownStatuses}
          routeTraffic={showPlan ? [] : simulationState?.route_traffic || []}
          policeStations={policeStations}
          routePolice={
            (previewPlan
              ? previewPlan.police_along_route
              : liveTrip?.police_along_route) || []
          }
          stretches={
            (previewPlan
              ? previewPlan.signalless_stretches
              : simulationState?.police_watch?.stretches) || []
          }
          policeWatch={showPlan ? null : simulationState?.police_watch}
          policeBoard={showPlan ? null : simulationState?.police_board}
          incidents={showPlan ? [] : simulationState?.incidents || []}
          response={showPlan ? null : simulationState?.response}
          startPoint={tripPoints.start}
          hospitalPoint={tripPoints.hospital}
          previewSignals={previewPlan ? previewPlan.signals : []}
          hospitalName={hospitalName}
          area={area}
          liveTraffic={simulationState?.live_traffic}
          pickMode={pickMode}
          onPick={(point) =>
            changeStart({
              name: `Map point (${point.latitude.toFixed(4)}, ${point.longitude.toFixed(4)})`,
              ...point,
            })
          }
          overlay={<StoryBar story={story} notice={notice} />}
        />

        <TripPanel
          ambulance={shownAmbulance}
          routeStatuses={shownStatuses}
          vehicleCount={
            simulationState?.vehicle_count ?? vehicles.length
          }
          hospitalName={hospitalName}
          live={
            <LiveStatusPanel
              ambulance={shownAmbulance}
              liveTraffic={simulationState?.live_traffic}
              response={showPlan ? null : simulationState?.response}
              policeWatch={showPlan ? null : simulationState?.police_watch}
              policeBoard={showPlan ? null : simulationState?.police_board}
              policeStations={policeStations}
              agentFeed={simulationState?.agent_feed || []}
              onSimulateIncident={running ? simulateIncident : null}
              incidentBusy={incidentBusy}
            />
          }
          planner={
            <TripPlanner
              key={tripStart?.name || "no-start"}
              apiUrl={API_URL}
              hospitals={hospitals}
              start={tripStart}
              onStartChange={changeStart}
              hospital={tripHospital}
              onHospitalChange={changeHospital}
              pickMode={pickMode}
              onPickModeChange={setPickMode}
              plan={plan}
              planning={planning}
              planError={planError}
              onFindRoute={findRoute}
              onUseDemo={useDemoRoute}
              liveLevel={simulationState?.live_traffic?.level || "normal"}
              disabled={running}
            />
          }
        />
      </main>
    </div>
  );
}

export default App;
