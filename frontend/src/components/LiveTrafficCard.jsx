const LEVEL_LABELS = {
  light: "Light",
  normal: "Normal",
  heavy: "Heavy",
};

// Summary of the live TomTom traffic the simulation is copying.
// ambulance: the running trip. The car-time line is about it, so it is
// hidden before a trip starts; the ambulance's own driving time (without
// the stop at the patient) is shown next to it for a fair comparison.
function LiveTrafficCard({ live, ambulance = null }) {
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
      {ambulance && live.car_minutes != null && (
        <p className="muted">
          A normal car would take about {Math.round(live.car_minutes)} min for
          this drive now
          {ambulance.driving_seconds != null &&
            ` · ambulance so far ${Math.round(ambulance.driving_seconds / 60)} min driving (not counting time at the patient)`}
        </p>
      )}
      <span className="live-source">From TomTom</span>
    </section>
  );
}

export default LiveTrafficCard;
