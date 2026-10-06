// Turns the raw simulation state into plain-language status
// for each traffic signal on the ambulance route.

// Hospital of the tested demo trip (used until a trip is planned).
export const DEMO_HOSPITAL_NAME = "Ruby Hall Clinic";

export const SIGNAL_STATUS = {
  GREEN: { label: "Green", className: "status-green" },
  TURNING: { label: "Turning green", className: "status-turning" },
  READY: { label: "Next", className: "status-ready" },
  WAITING: { label: "Ahead", className: "status-waiting" },
  PASSED: { label: "Passed", className: "status-passed" },
};

// A signal's name: the streets meeting there, else "Signal 3".
export function signalLabel(signal) {
  return signal.name || `Signal ${signal.number}`;
}

export function formatDistance(meters) {
  if (meters == null) return "--";
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

export function formatDuration(seconds) {
  if (seconds == null) return "--";

  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const secs = total % 60;

  if (minutes === 0) return `${secs} s`;
  return `${minutes} min ${String(secs).padStart(2, "0")} s`;
}

// Status for every signal on the route, in route order.
export function getRouteSignalStatuses({
  routeSignals = [],
  corridor = [],
  signals = [],
  ambulance,
}) {
  const completed = ambulance?.status === "COMPLETED";
  const currentRouteIndex = ambulance?.route_index ?? -1;

  const corridorByKey = new Map(
    corridor.map((junction) => [
      `${junction.signal_id}|${junction.route_index}`,
      junction,
    ])
  );

  // For corridor signals the backend reports the colour of
  // the ambulance's own lane.
  const liveStateById = new Map(
    signals.map((signal) => [signal.signal_id, signal.state])
  );

  const crossings = routeSignals.map((routeSignal) => {
    const junction = corridorByKey.get(
      `${routeSignal.signal_id}|${routeSignal.route_index}`
    );

    let status = "WAITING";

    if (
      completed ||
      (ambulance && routeSignal.route_index < currentRouteIndex)
    ) {
      status = "PASSED";
    } else if (junction?.stage === "green") {
      status =
        liveStateById.get(routeSignal.signal_id) === "GREEN"
          ? "GREEN"
          : "TURNING";
    } else if (junction?.stage === "yellow" || junction?.stage === "all_red") {
      // Cross traffic is getting yellow / all red first.
      status = "TURNING";
    } else if (junction?.state === "ACTIVE") {
      // Next signal, still on its normal cycle until just in time.
      status = "READY";
    }

    // The corridor role from the requirements: ACTIVE (next junction,
    // getting priority), PREPARING (the one after), STANDBY (third),
    // UPCOMING (further), and NORMAL again once the ambulance passed.
    const role = status === "PASSED" ? "NORMAL" : junction?.state || null;

    return {
      ...routeSignal,
      status,
      role,
      etaSeconds: status === "PASSED" ? null : junction?.eta_seconds ?? null,
      distanceMeters: junction?.distance_meters ?? null,
      switchInSeconds: junction?.switch_in_seconds ?? null,
      queuedCars: junction?.queued_cars ?? null,
    };
  });

  // A big junction run by one signal can be crossed several times in a
  // row; the backend gives those crossings the same number. Show it as one
  // signal, with the status of the first crossing not yet passed.
  const junctions = [];
  for (const crossing of crossings) {
    const last = junctions[junctions.length - 1];
    if (last && last.number === crossing.number) {
      if (last.status === "PASSED") junctions[junctions.length - 1] = crossing;
      continue;
    }
    junctions.push(crossing);
  }
  return junctions;
}

// One short line describing what is happening now.
export function getStory({
  connected,
  simulationState,
  routeStatuses,
  hospitalName = DEMO_HOSPITAL_NAME,
  ambulanceLabel = "The ambulance",
  planPending = false,
  needsHospital = false,
}) {
  const status = simulationState?.status;
  const ambulance = simulationState?.ambulance;

  if (!connected) {
    return {
      tone: "muted",
      text: "Connecting…",
    };
  }

  if (status === "error") {
    return {
      tone: "error",
      text: `Error: ${simulationState.error || "unknown"}`,
    };
  }

  if (ambulance?.status === "COMPLETED") {
    return {
      tone: "success",
      text: `${ambulanceLabel} reached ${hospitalName}.`,
    };
  }

  // The whole journey: say which part the ambulance is on.
  const hasPickup = Boolean(ambulance?.pickup_point);
  if (status === "running" && hasPickup && ambulance?.leg === "at_patient") {
    return { tone: "ready", text: `${ambulanceLabel} is picking up the patient (about 3 min)` };
  }
  const stage =
    status === "running" && hasPickup
      ? ambulance?.leg === "to_patient"
        ? "is going to the patient"
        : `has the patient, going to ${hospitalName}`
      : null;

  if (status === "running" && ambulance?.delay_reason) {
    return {
      tone: "muted",
      text: `${ambulanceLabel} is stopped: ${ambulance.delay_reason}`,
    };
  }

  if (status === "warming_up") {
    const left = Math.max(
      0,
      (simulationState.warmup_seconds || 0) - simulationState.simulation_time
    );
    return {
      tone: "muted",
      text: `Filling the roads with traffic. Ambulance leaves in ${formatDuration(left)}`,
    };
  }

  if (status === "starting" || (status === "running" && !ambulance)) {
    return {
      tone: "muted",
      text: simulationState?.message || "Starting…",
    };
  }

  if (status === "running" && ambulance) {
    const story = drivingStory();
    return stage ? { ...story, text: `${ambulanceLabel} ${stage} · ${story.text}` } : story;
  }

  function drivingStory() {
    const next = routeStatuses.find((signal) => signal.status !== "PASSED");

    if (!next) {
      return {
        tone: "success",
        text: `No more signals. ${formatDistance(
          ambulance.distance_left_meters
        )} to ${hospitalName}`,
      };
    }

    const name = signalLabel(next);
    const away =
      next.distanceMeters != null ? ` · ${formatDistance(next.distanceMeters)}` : "";

    if (next.status === "GREEN") {
      return {
        tone: "go",
        text: `Green: ${name}${away}`,
      };
    }

    if (next.status === "TURNING") {
      return {
        tone: "ready",
        text: `Turning green: ${name}${away}`,
      };
    }

    if (next.status === "READY" && next.switchInSeconds != null) {
      return {
        tone: "muted",
        text: `Next signal: ${name}${away} · green in ${formatDuration(next.switchInSeconds)}`,
      };
    }

    return {
      tone: "muted",
      text: `Next signal: ${name}${away}`,
    };
  }

  if (status === "stopped" && ambulance) {
    return {
      tone: "muted",
      text: "Stopped. Press Start to run again.",
    };
  }

  if (planPending) {
    return {
      tone: "muted",
      text: needsHospital
        ? "Choose the hospital, then press Start."
        : "Press Start (top right) to begin this trip, or Find route to see the route first.",
    };
  }

  return {
    tone: "muted",
    text: `Plan a trip, or press Start for the demo trip (Shukrawar Peth → ${hospitalName}).`,
  };
}
