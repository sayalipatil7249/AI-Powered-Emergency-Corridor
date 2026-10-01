import { useState } from "react";

import { formatDistance, formatDuration } from "../routeStatus";
import { formatDateTime, formatPercent, label, planProblem } from "./adminApi";

function Share({ label: title, part, whole, note }) {
  const share = whole ? part / whole : null;
  return (
    <div className="admin-share">
      <div className="admin-share-top">
        <span>{title}</span>
        <strong>{formatPercent(share)}</strong>
      </div>
      <div className="admin-meter" aria-hidden="true">
        <span style={{ width: `${Math.round((share || 0) * 100)}%` }} />
      </div>
      <span className="admin-tile-sub">{note ?? `${part} of ${whole}`}</span>
    </div>
  );
}

// How often the fast-arrival measures were used and worked.
export function RouteSummary({ summary }) {
  if (!summary || !summary.trips) {
    return (
      <section className="admin-card">
        <h2>Fast response measures</h2>
        <p className="admin-empty">
          No completed trips yet. Run a trip on the live map: every trip is
          recorded here when the ambulance arrives.
        </p>
      </section>
    );
  }

  return (
    <section className="admin-card">
      <h2>Fast response measures</h2>
      <p className="muted">{summary.trips} completed trips with a route log</p>
      <div className="admin-shares">
        <Share
          label="Fast arrivals"
          part={summary.fast_arrivals}
          whole={summary.trips_with_plan}
          note={
            `${summary.fast_arrivals} of ${summary.trips_with_plan} planned trips on or ahead of plan` +
            (summary.trips > summary.trips_with_plan
              ? ` · ${summary.trips - summary.trips_with_plan} without a plan left out`
              : "")
          }
        />
        <Share
          label="Optimal route kept"
          part={summary.optimal_route_used}
          whole={summary.trips}
          note={`${summary.optimal_route_used} of ${summary.trips} trips without re-routing`}
        />
        <Share
          label="Full corridor cleared"
          part={summary.corridor_cleared}
          whole={summary.trips}
          note={`${summary.corridor_cleared} of ${summary.trips} trips with every signal turned green`}
        />
        <Share
          label="Signals turned green"
          part={summary.signals_cleared}
          whole={summary.signals_total}
          note={`${summary.signals_cleared} of ${summary.signals_total} signals on the routes`}
        />
        <Share
          label="Police reached the road"
          part={summary.police_on_scene}
          whole={summary.police_alerts}
          note={`${summary.police_on_scene} of ${summary.police_alerts} police alerts`}
        />
      </div>
    </section>
  );
}

const STATUS_TONES = {
  COMPLETED: "good",
  IN_PROGRESS: "neutral",
  CANCELLED: "warning",
  FAILED: "critical",
};

// Every request with its route log; the delay reason can be corrected.
export function RequestsTable({ requests, delayReasons, onChangeReason, busyId }) {
  const [fastOnly, setFastOnly] = useState(false);
  const rows = fastOnly
    ? requests.filter((row) => row.route?.fast_arrival)
    : requests;

  return (
    <section className="admin-card">
      <div className="admin-card-heading">
        <div>
          <h2>Request &amp; route log</h2>
          <p className="muted">
            Newest first · open a request for its map and timeline · the delay
            reason can be corrected
          </p>
        </div>
        <label className="admin-check">
          <input
            type="checkbox"
            checked={fastOnly}
            onChange={(event) => setFastOnly(event.target.checked)}
          />
          Fast arrivals only
        </label>
      </div>

      {rows.length === 0 ? (
        <p className="admin-empty">No requests to show.</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Request</th>
                <th>Trip</th>
                <th>Status</th>
                <th>Response</th>
                <th>Delay</th>
                <th>Delay reason</th>
                <th>Route</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.request_id}>
                  <td>
                    <a
                      className="admin-request-link"
                      href={`#/admin/trip/${encodeURIComponent(row.request_id)}`}
                      title="Open the trip: map and timeline"
                    >
                      {row.request_id}
                    </a>
                    <span className="muted">{formatDateTime(row.dispatched_at)}</span>
                  </td>
                  <td>
                    {row.start_name || "--"} → {row.hospital_name || "--"}
                    <span className="muted">
                      {formatDistance(row.distance_meters)}
                      {row.traffic_level && ` · ${row.traffic_level} traffic`}
                    </span>
                  </td>
                  <td>
                    <span className={`admin-status tone-${STATUS_TONES[row.status] || "neutral"}`}>
                      {label(row.status)}
                    </span>
                  </td>
                  <td>
                    {formatDuration(row.response_seconds)}
                    {row.planned_seconds != null ? (
                      <span className="muted">
                        plan {formatDuration(row.planned_seconds)}
                      </span>
                    ) : (
                      <span
                        className="admin-status tone-warning"
                        title={planProblem(row.plan_status)}
                      >
                        No plan
                      </span>
                    )}
                  </td>
                  <td>
                    {formatDuration(row.delay_seconds)}
                    {row.stopped_seconds != null && (
                      <span className="muted">
                        stood still {formatDuration(row.stopped_seconds)}
                      </span>
                    )}
                  </td>
                  <td>
                    {row.status === "COMPLETED" ? (
                      <select
                        value={row.delay_reason || ""}
                        disabled={busyId === row.request_id}
                        onChange={(event) =>
                          onChangeReason(row.request_id, event.target.value)
                        }
                        aria-label={`Delay reason for ${row.request_id}`}
                      >
                        {!row.delay_reason && (
                          <option value="" disabled>
                            Unknown (no plan)
                          </option>
                        )}
                        {delayReasons.map((reason) => (
                          <option key={reason} value={reason}>
                            {reason === "NONE" ? "None (on time)" : label(reason)}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="muted">--</span>
                    )}
                    {row.delay_reason_manual && (
                      <span className="muted">set by admin</span>
                    )}
                  </td>
                  <td>
                    {row.route ? (
                      <ul className="admin-route-facts">
                        <li>
                          Signals green {row.route.signals_cleared}/{row.route.signals_total}
                        </li>
                        <li>
                          {row.route.optimal_route_used
                            ? "Optimal route"
                            : `Re-routed ×${row.route.reroutes}`}
                        </li>
                        {row.route.police_alerts > 0 && (
                          <li>
                            Police {row.route.police_on_scene}/{row.route.police_alerts} on scene
                          </li>
                        )}
                        {row.route.incidents > 0 && (
                          <li>{row.route.incidents} accident(s)</li>
                        )}
                        <li className={row.route.fast_arrival ? "fast" : ""}>
                          {row.route.fast_arrival ? "Fast arrival" : "Slower than plan"}
                        </li>
                      </ul>
                    ) : (
                      <span className="muted">--</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
