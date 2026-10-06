import { useEffect, useState } from "react";

import MapView from "./MapView";
import AskChat from "./components/AskChat";
import ReportProblem from "./components/ReportProblem";
import EmergencyIntake from "./components/EmergencyIntake";
import BookingList from "./components/BookingList";
import DashboardHeader from "./components/DashboardHeader";
import LiveStatusPanel from "./components/LiveStatusPanel";
import FleetPanel from "./components/FleetPanel";
import StoryBar from "./components/StoryBar";
import TripPanel from "./components/TripPanel";
import TripPlanner from "./components/TripPlanner";
import {
  DEMO_HOSPITAL_NAME,
  getRouteSignalStatuses,
  getStory,
} from "./routeStatus";
import { DEFAULT_CONDITION, DEFAULT_CONDITIONS } from "./fleet";
import { API_URL, RECONNECT_DELAY_MS, useLiveState } from "./api";


// Fixed data (area, hospitals, police, conditions) is loaded once, but
// the page is often opened before the backend has finished starting:
// keep trying until it answers. Returns a cleanup for useEffect.
function loadWhenReady(path, onData) {
  let timer = null;
  let cancelled = false;

  const load = () => {
    fetch(`${API_URL}${path}`)
      .then((response) => {
        if (!response.ok) throw new Error(`${path}: ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (!cancelled) onData(data);
      })
      .catch(() => {
        if (!cancelled) timer = setTimeout(load, RECONNECT_DELAY_MS);
      });
  };

  load();
  return () => {
    cancelled = true;
    clearTimeout(timer);
  };
}


// "X, Pune" unless the name already says Pune.
function withCity(name) {
  return /pune/i.test(name || "") ? name : `${name}, Pune`;
}

// The tested demo trip's start (see backend demo_trip()). "demo" keeps
// the planner from swapping its hospital for the nearest one.
const DEMO_START = {
  name: "Shukrawar Peth",
  latitude: 18.5160848,
  longitude: 73.8538128,
  demo: true,
};

function App() {
  const { state: simulationState, connected } = useLiveState();
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  // Only used for problems with the Start / Stop buttons.
  const [notice, setNotice] = useState("");
  // "Report a problem" form: null closed, "" open, or a trip's request id.
  const [reportFor, setReportFor] = useState(null);

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
  // The whole journey: the ambulance leaves the nearest base, picks the
  // patient up at "From", then drives to "To".
  const [fromBase, setFromBase] = useState(true);

  // Patients' conditions (priority): the first ambulance's is set by
  // each crew before requesting a corridor and during its trip.
  const [conditions, setConditions] = useState(DEFAULT_CONDITIONS);
  const [startCondition, setStartCondition] = useState(DEFAULT_CONDITION);
  const [fleetBusy, setFleetBusy] = useState(false);
  const [fleetError, setFleetError] = useState("");
  const [selectedAmbulanceId, setSelectedAmbulanceId] = useState("ambulance_01");
  const [mapView, setMapView] = useState("map");
  // Camera: "follow" the selected ambulance, show "all" active ones, or
  // "free". "auto" until the user picks: all when several are driving.
  const [cameraMode, setCameraMode] = useState("auto");
  // Expanded: the map takes the full width (side panel hidden).
  const [mapExpanded, setMapExpanded] = useState(false);
  const [previewFocus, setPreviewFocus] = useState(false);

  const focusAmbulance = (vehicleId, view = "map") => {
    setSelectedAmbulanceId(vehicleId);
    setMapView(view);
    setCameraMode(view === "chase" ? "follow" : "free");
    setPreviewFocus(true);
  };

  // Independent crew requests, submitted together for the demo.
  const [bookings, setBookings] = useState([]);
  const [fleetPreview, setFleetPreview] = useState(null);
  const [previewFailure, setPreviewFailure] = useState(null);
  const [previewAttempt, setPreviewAttempt] = useState(0);
  const requestKey = JSON.stringify(bookings.map(({ kind, start, hospital, condition, from_base }) => ({
    kind, start, hospital, condition, from_base,
  })));
  const previewReady = fleetPreview?.key === requestKey;
  const previewError = previewFailure?.key === requestKey ? previewFailure.message : "";

  useEffect(() => {
    if (requestKey === "[]") return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      setPreviewFailure(null);
      try {
        const response = await fetch(`${API_URL}/fleet/preview`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ambulances: JSON.parse(requestKey) }),
          signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Could not check the routes.");
        if (!controller.signal.aborted) setFleetPreview({ ...result, key: requestKey });
      } catch (error) {
        if (!controller.signal.aborted) setPreviewFailure({ key: requestKey, message: error.message });
      }
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [requestKey, previewAttempt]);

  const bookTrip = () => {
    setPreviewFocus(false);
    const booking = tripStart
      ? {
          kind: "trip",
          start: tripStart,
          hospital: tripHospital,
          from_base: fromBase,
          label: `${tripStart.name} → ${tripHospital.name}`,
        }
      : {
          kind: "demo",
          label: `Shukrawar Peth → ${DEMO_HOSPITAL_NAME} (demo trip)`,
        };
    setPlanStarted(false);
    setBookings((list) => [
      ...list,
      { ...booking, condition: startCondition, id: `${Date.now()}-${list.length}` },
    ]);
  };

  const bookCrossing = (condition) => {
    setPreviewFocus(false);
    setPlanStarted(false);
    setBookings((list) => [
      ...list,
      {
        kind: "crossing",
        condition,
        label: "From a side road, crossing Ambulance 1's route at a signal",
        id: `${Date.now()}-${list.length}`,
      },
    ]);

  };

  const removeBooking = (id) => {
    setPreviewFocus(false);
    setPlanStarted(false);
    setBookings((list) => {
      const remaining = list.filter((booking) => booking.id !== id);
      // A generated crossing request needs a real primary trip.
      return remaining[0]?.kind === "crossing"
        ? remaining.filter((booking) => booking.kind !== "crossing") : remaining;
    });
  };

  const simulationStatus = simulationState?.status;
  const running = ["starting", "warming_up", "running"].includes(
    simulationStatus
  );


  // The simulated part of Pune: trips must start and end inside it.
  useEffect(() => loadWhenReady("/plan/area", setArea), []);

  // Patient conditions the crew can choose from.
  useEffect(() => loadWhenReady("/fleet/conditions", setConditions), []);

  // Police stations that can be sent to clear a jam.
  useEffect(() => loadWhenReady("/plan/police", setPoliceStations), []);

  // Hospitals in the simulated area; Ruby Hall Clinic (demo) by default.
  useEffect(
    () =>
      loadWhenReady("/plan/hospitals", (list) => {
        setHospitals(list);
        setTripHospital(
          list.find((item) => item.name === DEMO_HOSPITAL_NAME) || list[0]
        );
      }),
    []
  );

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

  // "Demo route": fill in the tested demo trip (Shukrawar Peth -> Ruby
  // Hall Clinic, the ambulance already with the patient) and plan it at
  // once, so it shows on the map and Start runs it.
  const useDemoRoute = () => {
    const hospital =
      hospitals.find((item) => item.name === DEMO_HOSPITAL_NAME) || null;
    setTripStart(DEMO_START);
    setTripHospital(hospital);
    setFromBase(false);
    setPlanStarted(false);
    setPickMode(false);
    if (hospital) {
      findRoute({ start: DEMO_START, hospital, fromBase: false });
    }
  };

  const tripBody = (condition) =>
    JSON.stringify({ start: tripStart, hospital: tripHospital, condition, from_base: fromBase });

  // The AI read a typed emergency request: fill in the trip and plan it.
  // keepHospital: the planner keeps the AI's hospital (already called).
  const fillFromAi = (result) => {
    const start = { ...result.start, keepHospital: true };
    const hospital =
      hospitals.find((item) => item.name === result.hospital.name) || result.hospital;
    setTripStart(start);
    setTripHospital(hospital);
    setStartCondition(result.condition);
    setFromBase(result.from_base);
    setPlanStarted(false);
    setPickMode(false);
    findRoute({ start, hospital, fromBase: result.from_base, condition: result.condition });
  };

  // Plan the route of the trip in the planner (or of the trip given).
  const findRoute = async (trip = null) => {
    try {
      setPlanning(true);
      setPlanError("");

      const response = await fetch(`${API_URL}/plan/route`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // The condition decides which 108 ambulance goes (ALS or BLS).
        body: trip
          ? JSON.stringify({
              start: trip.start, hospital: trip.hospital,
              condition: trip.condition || startCondition, from_base: trip.fromBase,
            })
          : tripBody(startCondition),
      });
      const result = await response.json();

      if (!response.ok) {
        throw new Error(result.detail || "Could not find a route.");
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
    if (bookings.length > 0) {
      return startBookedFleet();
    }
    try {
      setStarting(true);
      setNotice("");

      // With a planned trip the ambulance takes that route;
      // otherwise the tested demo route.
      const response = await fetch(
        `${API_URL}/simulation/start?condition=${startCondition}`,
        {
          method: "POST",
          ...(plan && tripStart
            ? {
                headers: { "Content-Type": "application/json" },
                body: tripBody(startCondition),
              }
            : {}),
        }
      );

      if (!response.ok) {
        throw new Error("Failed to start simulation");
      }

      const result = await response.json();

      if (result.status === "stopping") {
        setNotice(
          "Still stopping the last run. Try again in a moment."
        );
      } else if (plan && tripStart) {
        setPlanStarted(true);
      }
    } catch (error) {
      console.error("Simulation start error:", error);
      setNotice("Could not start. Is the backend running?");
    } finally {
      setStarting(false);
    }
  };


  // Separate requests, dispatched together for this simulation.
  const startBookedFleet = async () => {
    if (!previewReady) {
      setNotice(previewError || "Wait a moment, the routes are still being planned.");
      return;
    }
    try {
      setStarting(true);
      setNotice("");
      const response = await fetch(`${API_URL}/fleet/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ambulances: bookings.map(({ kind, start, hospital, condition, from_base }) => ({
            kind,
            start,
            hospital,
            condition,
            from_base,
          })),
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(result.detail || "Could not start the ambulances.");
      }
      if (result.status === "stopping") {
        setNotice(
          "Still stopping the last run. Try again in a moment."
        );
      } else {
        // Show the run, not the plan preview.
        setPlanStarted(true);
      }
    } catch (error) {
      setNotice(error.message);
    } finally {
      setStarting(false);
    }
  };

  // More ambulances and each crew's patient condition (/fleet).
  const fleetRequest = async (path, method, body) => {
    try {
      setFleetBusy(true);
      setFleetError("");
      const response = await fetch(`${API_URL}${path}`, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "That did not work. Try again.");
      }
    } catch (error) {
      setFleetError(error.message);
    } finally {
      setFleetBusy(false);
    }
  };

  // Test: the ambulance's hospital can't take the patient any more.
  // The crew says the ambulance is stuck: call the nearest police now.
  const callPolice = async (vehicleId) => {
    try {
      setFleetBusy(true);
      setFleetError("");
      const response = await fetch(`${API_URL}/fleet/ambulances/${vehicleId}/call-police`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ called_by: "crew" }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Could not call the police.");
      setNotice(result.message);
    } catch (error) {
      setFleetError(error.message);
    } finally {
      setFleetBusy(false);
    }
  };

  const hospitalDeclines = (vehicleId) =>
    fleetRequest(`/fleet/ambulances/${vehicleId}/hospital-declines`, "POST", {});

  const changeCondition = (vehicleId, condition) =>
    fleetRequest(`/fleet/ambulances/${vehicleId}/condition`, "PUT", {
      condition,
      changed_by: "crew",
    });

  const addCrossingAmbulance = (condition) =>
    fleetRequest("/fleet/ambulances/crossing", "POST", { condition });

  const addPlannedAmbulance = (condition) =>
    fleetRequest("/fleet/ambulances", "POST", {
      start: tripStart,
      hospital: tripHospital,
      condition,
      from_base: fromBase,
    });

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
        throw new Error(result.detail || "Could not make a test accident.");
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
      setNotice("Could not stop. Is the backend running?");
    } finally {
      setStopping(false);
    }
  };




  const ambulance = simulationState?.ambulance;
  const ambulances = simulationState?.ambulances || [];
  const selectedRun = ambulances.find((item) => item.vehicle_id === selectedAmbulanceId)
    || ambulances[0];
  const selectedId = selectedRun?.vehicle_id || selectedAmbulanceId;
  const selectedDetails = simulationState?.ambulance_details?.[selectedId];
  const selectedSnapshot = selectedRun?.number === 1
    ? ambulance
    : selectedDetails?.snapshot || (selectedRun && {
        ...selectedRun,
        status: selectedRun.status === "arrived" ? "COMPLETED" : "WAITING",
      });
  const vehicles = simulationState?.vehicles || [];

  const routeStatuses = getRouteSignalStatuses({
    routeSignals: selectedDetails?.route_signals ||
      (selectedRun?.number === 1 ? simulationState?.route_signals : []) || [],
    corridor: selectedDetails?.corridor ||
      (selectedRun?.number === 1 ? simulationState?.corridor : []) || [],
    signals: selectedDetails?.signals ||
      (selectedRun?.number === 1 ? simulationState?.signals : []) || [],
    ambulance: selectedSnapshot,
  });

  // A new plan (not yet started) replaces the last trip on screen.
  const showPlan = !running && plan && !planStarted;

  // Names come from the running / last trip, otherwise from the planner.
  const liveTrip =
    !showPlan && (running || ambulance)
      ? selectedRun || simulationState?.trip : null;
  // Without a planned route, Start runs the demo trip: name that one,
  // not the hospital picked in the planner.
  const hospitalName =
    liveTrip?.hospital_name || (plan && tripHospital?.name) || DEMO_HOSPITAL_NAME;
  // A start or hospital picked, but no route found yet.
  const planPending = !liveTrip && !plan && Boolean(tripStart);
  const startName =
    liveTrip?.start_name || (plan && tripStart?.name) || "Shukrawar Peth";

  const liveRoute = simulationState?.route || [];
  // Where the trip starts and ends, shown before and during the run.
  // The running / finished trip's own points; otherwise the planner's
  // choice (not the last trip's, or every hospital would appear there).
  // With the whole journey: "start" is the ambulance's base and
  // "pickup" the patient.
  const tripPoints = liveTrip
    ? {
        start: liveTrip.start_point,
        pickup: liveTrip.pickup_point,
        hospital: liveTrip.hospital_point,
      }
    : {
        start: plan?.base
          ? [plan.base.latitude, plan.base.longitude]
          : tripStart && [tripStart.latitude, tripStart.longitude],
        pickup: plan?.pickup ? [plan.pickup.latitude, plan.pickup.longitude] : null,
        hospital:
          tripHospital && [tripHospital.latitude, tripHospital.longitude],
      };
  const showFleetPreview = !running && !planStarted && bookings.length > 0;
  const previewRoutes = showFleetPreview && previewReady ? fleetPreview.ambulances : [];
  const previewNumber = Number(selectedAmbulanceId.slice(-2)) || 1;
  const selectedPreview = previewRoutes.find((item) => item.number === previewNumber);
  const story = showFleetPreview && previewReady
    ? { tone: "muted", text: `${previewRoutes.length} routes ready. Click one to see it.` }
    : getStory({
        connected,
        simulationState: {
          ...simulationState,
          ambulance: selectedSnapshot && {
            ...selectedSnapshot,
            delay_reason: selectedRun?.delay_reason,
            pickup_point: selectedRun?.pickup_point,
          },
        },
        routeStatuses,
        hospitalName,
        ambulanceLabel: selectedRun?.label,
        planPending,
      });
  const previewPlan = showPlan && !showFleetPreview ? plan : null;
  const shownAmbulance = showPlan || showFleetPreview ? null : selectedSnapshot;
  const shownStatuses = showPlan || showFleetPreview ? [] : routeStatuses;

  // The selected ambulance's police watch and deadlock response, for
  // the map and the side panel's live status.
  const shownPoliceWatch = showPlan || showFleetPreview ? null
    : selectedRun?.number === 1 ? simulationState?.police_watch
      : selectedDetails?.police_watch;
  const shownResponse = showPlan || showFleetPreview ? null
    : selectedRun?.number === 1 ? simulationState?.response
      : selectedDetails?.response;

  return (
    <div className="app">
      <DashboardHeader
        connected={connected}
        onStartSimulation={startSimulation}
        onStopSimulation={stopSimulation}
        starting={starting}
        startDisabled={bookings.length > 0 && !previewReady}
        stopping={stopping}
        running={running}
        tripLabel={selectedPreview
          ? `${selectedPreview.label}: ${selectedPreview.start_name} to ${withCity(selectedPreview.hospital_name)}`
          : `${selectedRun?.label || "Ambulance"} from ${startName} to ${withCity(hospitalName)}`}
        onReportProblem={() => setReportFor("")}
        playbackSpeed={simulationState?.playback_speed}
        onPlaybackSpeedChange={changePlaybackSpeed}
      />

      <main className={`content ${mapExpanded ? "map-expanded" : ""}`}>
        <MapView
          units={simulationState?.units || []}
          expanded={mapExpanded}
          onExpandChange={setMapExpanded}
          ambulance={showFleetPreview ? null : shownAmbulance}
          selectedNumber={showFleetPreview ? previewNumber : selectedRun?.number || previewNumber}
          selectedAmbulanceId={selectedId}
          view={mapView}
          onViewChange={setMapView}
          camera={
            cameraMode !== "auto" ? cameraMode
              : ambulances.filter((item) => item.status === "driving").length > 1 ? "all" : "follow"
          }
          onCameraChange={setCameraMode}
          onSelectAmbulance={focusAmbulance}
          vehicles={vehicles}
          route={showFleetPreview ? [] : previewPlan ? previewPlan.geometry : selectedRun?.route || liveRoute}
          previewRoutes={previewRoutes}
          previewFocus={previewFocus}
          sharedJunctions={showFleetPreview && previewReady ? fleetPreview.shared_junctions : []}
          routeStatuses={showFleetPreview ? [] : shownStatuses}
          routeTraffic={showPlan || showFleetPreview ? [] : selectedRun?.number === 1
            ? simulationState?.route_traffic || [] : selectedDetails?.route_traffic || []}
          policeStations={policeStations}
          routePolice={showFleetPreview ? [] :
            (previewPlan
              ? previewPlan.police_along_route
              : selectedRun?.number === 1 ? simulationState?.trip?.police_along_route
                : selectedDetails?.police_along_route) || []
          }
          stretches={showFleetPreview ? [] :
            (previewPlan
              ? previewPlan.signalless_stretches
              : (selectedRun?.number === 1 ? simulationState?.police_watch
                : selectedDetails?.police_watch)?.stretches) || []
          }
          policeWatch={shownPoliceWatch}
          policeBoard={showPlan ? null : simulationState?.police_board}
          incidents={showPlan || showFleetPreview ? [] : simulationState?.incidents || []}
          response={shownResponse}
          startPoint={showFleetPreview ? null : tripPoints.start}
          pickupPoint={showFleetPreview ? null : tripPoints.pickup}
          hospitalPoint={showFleetPreview ? null : tripPoints.hospital}
          previewSignals={!showFleetPreview && previewPlan ? previewPlan.signals : []}
          hospitalName={selectedPreview?.hospital_name || hospitalName}
          area={area}
          liveTraffic={simulationState?.live_traffic}
          hospitals={hospitals}
          onSetStart={running ? null : changeStart}
          onSetDestination={running ? null : changeHospital}
          pickMode={pickMode}
          onPick={(point) =>
            changeStart({
              name: "Point on the map",
              ...point,
            })
          }
          overlay={<StoryBar story={story} notice={notice} />}
          otherAmbulances={showPlan || showFleetPreview ? [] : ambulances.filter(
            (item) => item.vehicle_id !== selectedId
          )}
        />

        <TripPanel
          ambulance={shownAmbulance}
          ambulanceLabel={selectedRun?.label}
          routeStatuses={shownStatuses}
          hospitalName={selectedPreview?.hospital_name || hospitalName}
          live={
            <LiveStatusPanel
              ambulance={shownAmbulance}
              liveTraffic={simulationState?.live_traffic}
              response={shownResponse}
              policeWatch={shownPoliceWatch}
              policeBoard={showPlan ? null : simulationState?.police_board}
              policeStations={policeStations}
              giveWay={showPlan ? null : simulationState?.give_way}
              agentFeed={simulationState?.agent_feed || []}
            />
          }
          planner={
            <>
            <EmergencyIntake apiUrl={API_URL} disabled={starting || running} onFill={fillFromAi} />
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
              disabled={starting}
              conditions={conditions}
              condition={startCondition}
              onConditionChange={(value) => {
                setStartCondition(value);
                // The 108 ambulance depends on the condition (ALS / BLS).
                if (fromBase) setPlan(null);
              }}
              booked={bookings.map((item) => item.hospital?.name).filter(Boolean)}
              fromBase={fromBase}
              onFromBaseChange={(value) => {
                setFromBase(value);
                setPlan(null);
              }}
            />
            </>
          }
          fleet={
            <>
            {!running && (
              <BookingList
                bookings={bookings}
                preview={previewReady ? fleetPreview : null}
                previewError={previewError}
                onRetryPreview={() => setPreviewAttempt((attempt) => attempt + 1)}
                previewBusy={bookings.length > 0 && !previewReady && !previewError}
                disabled={starting}
                onConditionChange={(id, condition) => {
                  setPlanStarted(false);
                  setBookings((list) => list.map((item) => item.id === id ? { ...item, condition } : item));
                }}
                conditions={conditions}
                maxAmbulances={simulationState?.max_ambulances}
                canBookTrip={Boolean(tripHospital)}
                tripLabel={tripStart ? "this trip" : "the demo route"}
                onBookTrip={bookTrip}
                onBookCrossing={bookCrossing}
                onRemove={removeBooking}
                onViewRoute={(index) => focusAmbulance(`ambulance_${String(index + 1).padStart(2, "0")}`)}
              />
            )}
            <FleetPanel
              ambulances={showPlan || showFleetPreview ? [] : ambulances}
              selectedAmbulanceId={selectedId}
              onViewRoute={(vehicleId) => focusAmbulance(vehicleId)}
              onChase={(vehicleId) => focusAmbulance(vehicleId, "chase")}
              referee={simulationState?.referee}
              conditions={conditions}
              running={running}
              maxAmbulances={simulationState?.max_ambulances}
              onConditionChange={changeCondition}
              onHospitalDeclines={hospitalDeclines}
              onReportProblem={(requestId) => setReportFor(requestId)}
              onCallPolice={callPolice}
              onAddCrossing={addCrossingAmbulance}
              onAddPlanned={plan && tripStart ? addPlannedAmbulance : null}
              onSimulateIncident={simulateIncident}
              incidentBusy={incidentBusy}
              busy={fleetBusy}
              error={fleetError}
            />
            </>
          }
        />
      </main>

      <AskChat apiUrl={API_URL} />

      {reportFor !== null && (
        <ReportProblem
          apiUrl={API_URL}
          ambulances={ambulances}
          requestId={reportFor}
          onClose={() => setReportFor(null)}
        />
      )}
    </div>
  );
}

export default App;
