// Turns the raw simulation state into plain-language status
// for each traffic signal on the ambulance route.

// Hospital of the tested demo trip (used until a trip is planned).
export const DEMO_HOSPITAL_NAME = "Ruby Hall Clinic";

export const SIGNAL_STATUS = {
  GREEN: { label: "Green for ambulance", className: "status-green" },
  TURNING: { label: "Turning green", className: "status-turning" },
  READY: { label: "Next · normal cycle", className: "status-ready" },
  WAITING: { label: "Waiting", className: "status-waiting" },
  PASSED: { label: "Passed", className: "status-passed" },
};

// A signal's name: the streets meeting there, else "Signal 3".
export function signalLabel(signal) {
  return signal.name || `Signal ${signal.number}`;
}

// For sentences: "the signal at Bund Garden Road × ..." or "Signal 3".
function signalPhrase(signal) {
  return signal.name ? `the signal at ${signal.name}` : `Signal ${signal.number}`;
}

function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
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

    return {
      ...routeSignal,
      status,
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

// One plain-language sentence describing what is happening now.
export function getStory({
  connected,
  simulationState,
  routeStatuses,
  hospitalName = DEMO_HOSPITAL_NAME,
}) {
  const status = simulationState?.status;
  const ambulance = simulationState?.ambulance;

  if (!connected) {
    return {
      tone: "muted",
      text: "Connecting to the backend… make sure it is running.",
    };
  }

  if (status === "error") {
    return {
      tone: "error",
      text: `Something went wrong: ${simulationState.error || "unknown error"}`,
    };
  }

  if (ambulance?.status === "COMPLETED") {
    return {
      tone: "success",
      text: `The ambulance reached ${hospitalName}. All signals are back to normal.`,
    };
  }

  if (status === "warming_up") {
    const left = Math.max(
      0,
      (simulationState.warmup_seconds || 0) - simulationState.simulation_time
    );
    return {
      tone: "muted",
      text: `Traffic is building up across the city (${
        simulationState.vehicle_count ?? 0
      } vehicles). The ambulance leaves in ${formatDuration(left)} of simulated time.`,
    };
  }

  if (status === "starting" || (status === "running" && !ambulance)) {
    return {
      tone: "muted",
      text: simulationState?.message || "Starting the simulation…",
    };
  }

  if (status === "running" && ambulance) {
    const next = routeStatuses.find((signal) => signal.status !== "PASSED");

    if (!next) {
      return {
        tone: "success",
        text: `All signals cleared. ${formatDistance(
          ambulance.distance_left_meters
        )} left to ${hospitalName}.`,
      };
    }

    const away =
      next.distanceMeters != null
        ? `, ${formatDistance(next.distanceMeters)} ahead`
        : "";

    if (next.status === "GREEN") {
      return {
        tone: "go",
        text: `${capitalize(signalPhrase(next))} is green for the ambulance${away}.`,
      };
    }

    if (next.status === "TURNING") {
      return {
        tone: "ready",
        text: `Turning ${signalPhrase(next)} green: yellow and all red for cross traffic first${away}.`,
      };
    }

    if (next.status === "READY" && next.switchInSeconds != null) {
      return {
        tone: "muted",
        text: `${capitalize(signalPhrase(next))}${away}${away ? "," : ""} is on its normal cycle; the AI switches it green in about ${formatDuration(next.switchInSeconds)}.`,
      };
    }

    return {
      tone: "muted",
      text: `Ambulance heading to ${signalPhrase(next)}${away}.`,
    };
  }

  if (status === "stopped" && ambulance) {
    return {
      tone: "muted",
      text: "Simulation stopped. Press Start to run it again.",
    };
  }

  return {
    tone: "muted",
    text: `Plan a trip, or press Start to send the ambulance to ${hospitalName}.`,
  };
}
