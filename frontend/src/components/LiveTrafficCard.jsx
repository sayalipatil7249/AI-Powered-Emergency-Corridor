const LEVEL_LABELS = {
  light: "Light",
  normal: "Moderate",
  heavy: "Heavy",
};

// Summary of the live TomTom traffic the simulation is copying.
// tripRunning: the car-time line is about the running trip, so it is
// hidden before a trip starts.
function LiveTrafficCard({ live, tripRunning = false }) {
  if (!live) {
    return null;
  }

  return (
    <section className="live-card" aria-label="Live Pune traffic">
      <div className="live-card-heading">
        <span className="live-dot" />
        Live Pune traffic · {live.fetched_at}
      </div>
      <p>
        <strong>{LEVEL_LABELS[live.level] || live.level}</strong> · traffic
        moving at {live.speed_percent}% of normal speed
        {live.closures > 0 &&
          ` · ${live.closures} ${live.closures === 1 ? "closure" : "closures"}`}
      </p>
      {tripRunning && live.car_minutes != null && (
        <p className="muted">
          A normal car needs ~{Math.round(live.car_minutes)} min for this trip
          right now
        </p>
      )}
      <span className="live-source">Source: TomTom</span>
    </section>
  );
}

export default LiveTrafficCard;
