import { formatDuration } from "../routeStatus";

// The big red card: when the ambulance reaches the hospital (or how long
// the trip took), and how long it has been driving.
function EtaCard({ ambulance, ambulanceLabel }) {
  const arrived = ambulance?.status === "COMPLETED";

  let etaValue = "--";

  if (arrived) {
    etaValue = formatDuration(ambulance.trip_time_seconds);
  } else if (ambulance?.eta_seconds != null) {
    etaValue = formatDuration(ambulance.eta_seconds);
  }

  return (
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
  );
}

export default EtaCard;
