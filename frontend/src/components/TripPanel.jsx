import RouteTimeline from "./RouteTimeline";
import { formatDistance, formatDuration } from "../routeStatus";

function TripPanel({
  ambulance,
  routeStatuses,
  vehicleCount,
  hospitalName,
  planner,
}) {
  const passedCount = routeStatuses.filter(
    (signal) => signal.status === "PASSED"
  ).length;

  const arrived = ambulance?.status === "COMPLETED";

  let etaValue = "--";

  if (arrived) {
    etaValue = formatDuration(ambulance.trip_time_seconds);
  } else if (ambulance?.eta_seconds != null) {
    etaValue = formatDuration(ambulance.eta_seconds);
  }

  return (
    <aside className="panel">
      {planner}

      <section className="panel-section eta">
        <span className="eta-label">
          {arrived ? "Reached hospital in" : "Estimated arrival in"}
          {!arrived && ambulance?.eta_source === "ai" && (
            <span className="eta-badge">AI</span>
          )}
        </span>
        <strong className="eta-value">{etaValue}</strong>
        <span className="eta-sub">
          {!arrived && ambulance?.trip_time_seconds != null
            ? `On the road for ${formatDuration(ambulance.trip_time_seconds)}`
            : arrived
              ? "Trip complete"
              : "Waiting for the trip to start"}
        </span>
        {!arrived &&
          ambulance?.eta_source === "ai" &&
          ambulance.formula_eta_seconds != null && (
            <span className="eta-compare">
              Simple formula says {formatDuration(ambulance.formula_eta_seconds)}
            </span>
          )}
      </section>

      <section className="panel-section stats">
        <div className="stat">
          <span>Speed</span>
          <strong>
            {ambulance?.speed != null && !arrived
              ? `${Math.round(ambulance.speed * 3.6)} km/h`
              : "--"}
          </strong>
        </div>
        <div className="stat">
          <span>Distance left</span>
          <strong>{formatDistance(ambulance?.distance_left_meters)}</strong>
        </div>
        <div className="stat">
          <span>Signals passed</span>
          <strong>
            {routeStatuses.length > 0
              ? `${passedCount}/${routeStatuses.length}`
              : "--"}
          </strong>
        </div>
        <div className="stat">
          <span>Vehicles</span>
          <strong>{vehicleCount}</strong>
        </div>
      </section>

      <RouteTimeline
        routeStatuses={routeStatuses}
        ambulance={ambulance}
        hospitalName={hospitalName}
      />
    </aside>
  );
}

export default TripPanel;
