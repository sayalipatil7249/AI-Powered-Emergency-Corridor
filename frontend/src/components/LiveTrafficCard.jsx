const LEVEL_LABELS = {
  light: "Light",
  normal: "Normal",
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
        Pune traffic now · {live.fetched_at}
      </div>
      <p>
        <strong>{LEVEL_LABELS[live.level] || live.level} traffic</strong>
        {live.closures > 0 &&
          ` · ${live.closures} ${live.closures === 1 ? "road" : "roads"} closed`}
      </p>
      {tripRunning && live.car_minutes != null && (
        <p className="muted">
          A normal car would take about {Math.round(live.car_minutes)} min for
          this trip now
        </p>
      )}
      <span className="live-source">From TomTom</span>
    </section>
  );
}

export default LiveTrafficCard;
