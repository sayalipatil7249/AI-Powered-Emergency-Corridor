import { formatDuration } from "../routeStatus";

const STATUS_LABELS = {
  ALERTED: "Alert sent",
  EN_ROUTE: "Police on the way",
  ON_SCENE: "Police clearing traffic",
  PASSED: "Ambulance through",
  CANCELLED: "Stood down",
};

// What happened to the phone call (backend/services/police_notifier.py;
// the rest are Twilio's call statuses).
const CALL_LABELS = {
  calling: "calling…",
  queued: "calling…",
  initiated: "calling…",
  ringing: "ringing",
  "in-progress": "answered, message playing",
  completed: "answered",
  busy: "busy",
  "no-answer": "no answer",
  failed: "call failed",
  canceled: "call cancelled",
  off: "calls are off",
  no_number: "no test phone set",
  limit: "call limit reached",
};

const ACTIVE = ["ALERTED", "EN_ROUTE", "ON_SCENE"];

// Where the number came from (backend/services/police_notifier.py).
const SOURCE_LABELS = {
  station: "station's number",
  default: "default test phone",
};

function callText(call) {
  if (!call) return null;
  const label = CALL_LABELS[call.status] || call.status;
  const source = SOURCE_LABELS[call.source];
  return call.to
    ? `📞 ${call.to}${source ? ` (${source})` : ""} · ${label}`
    : `📞 ${label}`;
}

// One card for the police: alerts for the stretches of the route
// without signals (corridor/police_watch.py) and their phone calls, then
// the stations that could reach the ambulance first right now
// (corridor/police_board.py).
function PoliceAlertCard({ policeWatch, board, stations = [] }) {
  const alerts = policeWatch?.alerts || [];
  const stretches = policeWatch?.stretches || [];
  const nearby = board?.nearby || [];

  if (stretches.length === 0 && nearby.length === 0) {
    return null;
  }

  // Alerts still being handled first, then the latest finished one.
  const active = alerts.filter((alert) => ACTIVE.includes(alert.status));
  const finished = alerts.filter((alert) => !ACTIVE.includes(alert.status));
  const shown = [...active.reverse(), ...finished.slice(-1)].slice(0, 2);
  const jammed = stretches.filter((stretch) => stretch.state === "jammed").length;

  const live = Object.fromEntries(
    (board?.stations || []).map((station) => [station.name, station])
  );
  const kinds = Object.fromEntries(
    stations.map((station) => [station.name, station.kind])
  );

  return (
    <section className="live-card police-card" aria-label="Police">
      <div className="live-card-heading">
        <span
          className={`response-dot ${active.length ? "status-police_en_route" : ""}`}
        />
        Police
      </div>

      {shown.length === 0 ? (
        <p className="police-quiet">
          {jammed > 0 ? "Checking a jam" : "No jams"} on the{" "}
          {stretches.length} roads without signals.
        </p>
      ) : (
        <ul className="police-alerts">
          {shown.map((alert) => (
            <li key={alert.alert_id} className={`police-alert status-${alert.status}`}>
              <strong>{STATUS_LABELS[alert.status]}</strong>
              <span className="police-road">{alert.road}</span>
              <span className="muted">
                {alert.station}
                {(alert.status === "ALERTED" || alert.status === "EN_ROUTE") &&
                  ` · police ~${formatDuration(alert.police_eta_seconds)}`}
                {alert.status === "ON_SCENE" &&
                  ` · ${alert.vehicles_waved} vehicles waved through`}
                {alert.closed_reason && ` · ${alert.closed_reason}`}
              </span>
              {alert.call && (
                <span className="police-call">{callText(alert.call)}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {nearby.length > 0 && (
        <>
          <div className="police-subheading">Nearest to the ambulance</div>
          <ul className="nearby-police">
            {nearby.map((name) => {
              const station = live[name];
              return (
                <li key={name}>
                  <span className={`station-status status-${station.status}`} />
                  <span className="nearby-name">
                    {name}
                    <span className="muted">
                      {kinds[name] === "Police chowki" ? " (chowki)" : ""}
                      {station.status !== "available" && ` · ${station.status_text}`}
                    </span>
                  </span>
                  <span className="nearby-time">
                    {formatDuration(station.reach_seconds)}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

export default PoliceAlertCard;
