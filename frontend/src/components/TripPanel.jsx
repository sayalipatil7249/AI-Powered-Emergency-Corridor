import Collapsible from "./Collapsible";
import RouteTimeline from "./RouteTimeline";
import { formatDistance, formatDuration } from "../routeStatus";

function TripPanel({
  ambulance,
  ambulanceLabel,
  routeStatuses,
  hospitalName,
  planner,
  fleet,
  live,
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
      {fleet}

      <section className="panel-section eta">
        <span className="eta-label">
          {ambulanceLabel && `${ambulanceLabel} · `}
          {arrived ? "Reached hospital in" : "Arrives in"}
          {!arrived && ambulance?.eta_source === "ai" && (
            <span className="eta-badge">AI</span>
          )}
        </span>
        <strong className="eta-value">{etaValue}</strong>
        <span className="eta-sub">
          {!arrived && ambulance?.trip_time_seconds != null
            ? `Driving for ${formatDuration(ambulance.trip_time_seconds)}`
            : arrived
              ? "Trip done"
              : "Not started yet"}
        </span>
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
      </section>

      <RouteTimeline
        routeStatuses={routeStatuses}
        ambulance={ambulance}
        hospitalName={hospitalName}
      />

      {/* Traffic, jam prediction, police and AI messages: on demand. */}
      {live && (
        <Collapsible
          title="Updates"
          hint="traffic, police, messages"
          className="panel-section"
        >
          {live}
        </Collapsible>
      )}
    </aside>
  );
}

export default TripPanel;
