import SvgIcon from "./SvgIcon";
import {
  AMBULANCE_ICON,
  CHECK_ICON,
  HOSPITAL_ICON,
  SIGNAL_ICON,
  START_ICON,
} from "../icons";
import {
  formatDistance,
  formatDuration,
  signalLabel,
  SIGNAL_STATUS,
} from "../routeStatus";

// Start, every signal on the route, and the hospital, top to bottom,
// with the ambulance shown between the last passed and the next signal.
function RouteTimeline({ routeStatuses, ambulance, hospitalName }) {
  const completed = ambulance?.status === "COMPLETED";
  const nextIndex = routeStatuses.findIndex(
    (signal) => signal.status !== "PASSED"
  );
  const ambulanceSlot =
    nextIndex === -1 ? routeStatuses.length : nextIndex;

  const ambulanceRow = (
    <li key="ambulance" className="timeline-row timeline-ambulance">
      <SvgIcon svg={AMBULANCE_ICON} className="timeline-marker ambulance-marker" />
      <span className="timeline-label">Ambulance</span>
      <span className="timeline-value">
        {ambulance?.speed != null
          ? `${(ambulance.speed * 3.6).toFixed(0)} km/h`
          : ""}
      </span>
    </li>
  );

  const rows = [];

  rows.push(
    <li
      key="start"
      className={`timeline-row ${ambulance ? "done" : ""}`}
    >
      <SvgIcon svg={START_ICON} className="timeline-marker" />
      <span className="timeline-label">Start</span>
      <span className="timeline-value muted">
        {ambulance ? "Departed" : ""}
      </span>
    </li>
  );

  routeStatuses.forEach((signal, index) => {
    if (ambulance && !completed && index === ambulanceSlot) {
      rows.push(ambulanceRow);
    }

    const status = SIGNAL_STATUS[signal.status];
    const isNext = index === nextIndex && !completed;

    rows.push(
      <li
        key={`signal-${signal.number}`}
        className={`timeline-row ${
          signal.status === "PASSED" ? "done" : ""
        } ${isNext ? "next" : ""}`}
      >
        <span className={`timeline-marker signal-dot ${status.className}`}>
          {signal.status === "PASSED" ? (
            <SvgIcon svg={CHECK_ICON} className="check" />
          ) : (
            signal.number
          )}
        </span>
        <span className="timeline-label" title={signalLabel(signal)}>
          <SvgIcon svg={SIGNAL_ICON} className="label-signal-icon" />
          {signalLabel(signal)}
        </span>
        <span className={`timeline-value status-text ${status.className}`}>
          {status.label}
          {isNext && signal.distanceMeters != null && (
            <small>
              {formatDistance(signal.distanceMeters)} ahead
              {signal.status === "READY" && signal.switchInSeconds != null
                ? ` · green in ${formatDuration(signal.switchInSeconds)}`
                : ""}
            </small>
          )}
        </span>
      </li>
    );
  });

  if (ambulance && !completed && ambulanceSlot === routeStatuses.length) {
    rows.push(ambulanceRow);
  }

  rows.push(
    <li key="hospital" className={`timeline-row ${completed ? "done" : ""}`}>
      <SvgIcon svg={HOSPITAL_ICON} className="timeline-marker" />
      <span className="timeline-label">{hospitalName}</span>
      <span className="timeline-value">
        {completed
          ? "Arrived"
          : ambulance?.eta_seconds != null
            ? `~${formatDuration(ambulance.eta_seconds)}`
            : ""}
      </span>
    </li>
  );

  return (
    <section className="panel-section timeline-section">
      <div className="section-heading">
        <h2>Route</h2>
        <span className="muted">
          {routeStatuses.length > 0
            ? `${routeStatuses.length} signals`
            : "Loads when the trip starts"}
        </span>
      </div>

      <ol className="timeline">{rows}</ol>
    </section>
  );
}

export default RouteTimeline;
