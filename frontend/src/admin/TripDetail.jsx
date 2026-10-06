import { useEffect, useMemo, useState } from "react";
import AdminHeader from "./AdminHeader";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

import { formatDistance, formatDuration } from "../routeStatus";
import { adminApi, formatDateTime, label, planProblem } from "./adminApi";
import "./admin.css";

// What each event kind looks like. Colours carry identity only; the
// legend, tooltips and timeline always name the kind as well.
const KINDS = {
  DISPATCH: { label: "Dispatch", color: "#8b95a7" },
  STOP: { label: "Stood still", color: "#d03b3b" },
  SIGNAL: { label: "Signal green", color: "#0ca30c" },
  POLICE: { label: "Police", color: "#9085e9" },
  ACCIDENT: { label: "Accident", color: "#c98500" },
  REROUTE: { label: "Re-route", color: "#d95926" },
  AI: { label: "AI watch", color: "#8b95a7" },
  GIVE_WAY: { label: "Gave way", color: "#d9a400" },
  PICKUP: { label: "Patient", color: "#ec4899" },
  PRE_ALERT: { label: "Hospital pre-alert", color: "#14b8a6" },
  DIVERT: { label: "Diverted", color: "#d95926" },
  ARRIVAL: { label: "Arrival", color: "#0ca30c" },
  HANDOVER: { label: "Handover", color: "#14b8a6" },
  END: { label: "Ended", color: "#8b95a7" },
};

const TRACK_COLOR = "#3987e5";
const PLANNED_COLOR = "#8b95a7";
const REROUTE_COLOR = "#d95926";

function since(seconds) {
  return `+${formatDuration(seconds)}`;
}

// Fit the map to the trip once, then fly to the clicked event.
function MapFocus({ bounds, focus }) {
  const map = useMap();
  useEffect(() => {
    if (bounds.length > 1) map.fitBounds(bounds, { padding: [30, 30] });
  }, [map, bounds]);
  useEffect(() => {
    if (focus) map.flyTo(focus, Math.max(map.getZoom(), 17), { duration: 0.6 });
  }, [map, focus]);
  return null;
}

function TripMap({ trip, focus }) {
  const planned = trip.route_geometry;
  const track = trip.track.map((point) => [point[0], point[1]]);
  const reroutes = trip.events.filter(
    (event) => ["REROUTE", "DIVERT"].includes(event.kind) && event.data?.route?.length > 1
  );
  const markers = trip.events.filter(
    (event) =>
      event.latitude != null &&
      ["STOP", "SIGNAL", "POLICE", "ACCIDENT", "GIVE_WAY", "PICKUP", "DIVERT"].includes(event.kind)
  );
  const bounds = useMemo(
    () => (track.length > 1 ? track : planned),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [trip.request_id]
  );

  if (planned.length < 2 && track.length < 2) {
    return <p className="admin-empty">No map data for this trip.</p>;
  }

  const start = track[0] || planned[0];
  const end = track[track.length - 1] || planned[planned.length - 1];

  return (
    <div className="trip-map">
      <MapContainer
        center={start}
        zoom={15}
        className="map"
        zoomControl
        preferCanvas
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />
        <MapFocus bounds={bounds} focus={focus} />

        {planned.length > 1 && (
          <Polyline
            positions={planned}
            pathOptions={{ color: PLANNED_COLOR, weight: 3, opacity: 0.7, dashArray: "6 8" }}
          >
            <Tooltip sticky>Planned route</Tooltip>
          </Polyline>
        )}
        {reroutes.map((event, index) => (
          <Polyline
            key={`reroute-${index}`}
            positions={event.data.route}
            pathOptions={{ color: REROUTE_COLOR, weight: 3, opacity: 0.8, dashArray: "2 6" }}
          >
            <Tooltip sticky>New route after re-route at {since(event.seconds)}</Tooltip>
          </Polyline>
        ))}
        {track.length > 1 && (
          <Polyline positions={track} pathOptions={{ color: TRACK_COLOR, weight: 4, opacity: 0.95 }}>
            <Tooltip sticky>Path driven</Tooltip>
          </Polyline>
        )}

        {markers.map((event, index) => {
          const kind = KINDS[event.kind];
          const radius =
            event.kind === "STOP"
              ? Math.min(16, 6 + (event.duration_seconds || 0) / 20)
              : 6;
          return (
            <CircleMarker
              key={`marker-${index}`}
              center={[event.latitude, event.longitude]}
              radius={radius}
              pathOptions={{
                color: "#ffffff",
                weight: 2,
                fillColor: kind.color,
                fillOpacity: 0.9,
              }}
            >
              <Tooltip direction="top" offset={[0, -6]}>
                {kind.label} · {since(event.seconds)}
                <br />
                {event.title}
                {event.junction_name && event.kind === "STOP" && (
                  <>
                    <br />
                    {event.junction_name}
                  </>
                )}
              </Tooltip>
            </CircleMarker>
          );
        })}

        {start && (
          <CircleMarker center={start} radius={7} pathOptions={{ color: "#ffffff", weight: 2, fillColor: "#8b95a7", fillOpacity: 1 }}>
            <Tooltip direction="top" offset={[0, -6]}>Start: {trip.start_name}</Tooltip>
          </CircleMarker>
        )}
        {end && trip.status === "COMPLETED" && (
          <CircleMarker center={end} radius={8} pathOptions={{ color: "#ffffff", weight: 2, fillColor: "#dc2626", fillOpacity: 1 }}>
            <Tooltip direction="top" offset={[0, -6]}>Hospital: {trip.hospital_name}</Tooltip>
          </CircleMarker>
        )}
      </MapContainer>

      <ul className="trip-legend" aria-label="Map legend">
        <li><span className="trip-line" style={{ background: TRACK_COLOR }} />Path driven</li>
        <li><span className="trip-line dashed" style={{ color: PLANNED_COLOR }} />Planned route</li>
        {reroutes.length > 0 && (
          <li><span className="trip-line dashed" style={{ color: REROUTE_COLOR }} />New route</li>
        )}
        {["STOP", "SIGNAL", "POLICE", "ACCIDENT", "GIVE_WAY", "PICKUP", "DIVERT"].map((kind) => (
          <li key={kind}>
            <span className="admin-swatch round" style={{ background: KINDS[kind].color }} />
            {KINDS[kind].label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Fact({ label: title, value, sub }) {
  return (
    <div className="admin-tile">
      <span className="admin-tile-label">{title}</span>
      <strong className="admin-tile-value small">{value}</strong>
      {sub && <span className="admin-tile-sub">{sub}</span>}
    </div>
  );
}

// One trip: summary, map and everything that happened, in time order.
// Opened at #/admin/trip/<request id>.
function TripDetail({ requestId }) {
  const [trip, setTrip] = useState(null);
  const [error, setError] = useState("");
  const [focus, setFocus] = useState(null);
  const [kindFilter, setKindFilter] = useState("");

  useEffect(() => {
    let current = true;
    adminApi
      .trip(requestId)
      .then((result) => current && setTrip(result))
      .catch((problem) => current && setError(problem.message));
    return () => {
      current = false;
    };
  }, [requestId]);

  const events = (trip?.events || []).filter(
    (event) => !kindFilter || event.kind === kindFilter
  );
  const kinds = [...new Set((trip?.events || []).map((event) => event.kind))];
  const stopsTotal = (trip?.events || [])
    .filter((event) => event.kind === "STOP")
    .reduce((sum, event) => sum + (event.duration_seconds || 0), 0);

  return (
    <div className="admin-page">
      <AdminHeader
        title={requestId}
        subtitle={
          trip
            ? `${trip.start_name || "--"} to ${trip.hospital_name || "--"} · ${formatDateTime(trip.dispatched_at)}`
            : "Trip details"
        }
      />

      <main className="admin-content">
        {/* Back to the trip list, top left where people look first. */}
        <a className="button button-secondary trip-back" href="#/admin?s=trips">
          ← All trips
        </a>
        {error && <p className="admin-error">{error}</p>}
        {!trip && !error && <p className="admin-empty">Loading…</p>}

        {trip && (
          <>
            <div className="admin-tiles">
              <Fact label="Status" value={label(trip.status)} sub={trip.traffic_level && `${trip.traffic_level} traffic`} />
              <Fact
                label="Response time"
                value={formatDuration(trip.response_seconds)}
                sub={
                  trip.planned_seconds != null
                    ? `Planned ${formatDuration(trip.planned_seconds)}`
                    : `No plan: ${planProblem(trip.plan_status)}`
                }
              />
              <Fact
                label="Delay"
                value={trip.planned_seconds != null ? formatDuration(trip.delay_seconds) : "Unknown"}
                sub={
                  trip.delay_reason
                    ? `Reason: ${label(trip.delay_reason)}`
                    : trip.planned_seconds == null
                      ? "No planned time to compare with"
                      : null
                }
              />
              <Fact
                label="Stood still"
                value={formatDuration(trip.stopped_seconds)}
                sub={`${trip.events.filter((e) => e.kind === "STOP").length} stops of 10 s+ (${formatDuration(stopsTotal)})`}
              />
              <Fact
                label="Signals green"
                value={trip.route ? `${trip.route.signals_cleared}/${trip.route.signals_total}` : "--"}
                sub={trip.route?.optimal_route_used === false ? `Re-routed ×${trip.route.reroutes}` : "Optimal route"}
              />
              <Fact
                label="Distance"
                value={formatDistance(trip.distance_meters)}
                sub={trip.route ? `Police ${trip.route.police_on_scene}/${trip.route.police_alerts} on scene` : null}
              />
            </div>

            {trip.transport_seconds != null && (
              <div className="admin-tiles">
                <Fact
                  label="108 ambulance"
                  value={trip.unit_id ? `${trip.unit_id} (${trip.unit_kind})` : "--"}
                  sub={trip.unit_id ? null : "Started with the patient on board"}
                />
                <Fact label="To the patient" value={formatDuration(trip.to_patient_seconds)} />
                <Fact label="At the scene" value={formatDuration(trip.scene_seconds)} />
                <Fact
                  label="To hospital"
                  value={formatDuration(trip.transport_seconds)}
                  sub={trip.diverted ? "Diverted on the way" : null}
                />
                <Fact
                  label="Handover"
                  value={formatDuration(trip.handover_seconds)}
                  sub={trip.pre_alerted ? "Pre-alerted: team waiting" : "Not pre-alerted"}
                />
                <Fact
                  label="Dispatch to handover"
                  value={formatDuration(
                    (trip.to_patient_seconds || 0) + (trip.scene_seconds || 0)
                    + trip.transport_seconds + (trip.handover_seconds || 0)
                  )}
                  sub="Then back in service"
                />
              </div>
            )}

            <div className="trip-layout">
              <section className="admin-card">
                <h2>Route</h2>
                <TripMap trip={trip} focus={focus} />
              </section>

              <section className="admin-card trip-timeline-card">
                <div className="admin-card-heading">
                  <h2>Timeline</h2>
                  <select
                    value={kindFilter}
                    onChange={(event) => setKindFilter(event.target.value)}
                    aria-label="Show events of kind"
                  >
                    <option value="">All events</option>
                    {kinds.map((kind) => (
                      <option key={kind} value={kind}>
                        {KINDS[kind]?.label || label(kind)}
                      </option>
                    ))}
                  </select>
                </div>
                {events.length === 0 ? (
                  <p className="admin-empty">
                    No events recorded (trips from before this feature only
                    have their totals).
                  </p>
                ) : (
                  <ol className="trip-timeline">
                    {events.map((event, index) => {
                      const kind = KINDS[event.kind] || { label: event.kind, color: "#8b95a7" };
                      const located = event.latitude != null && event.longitude != null;
                      return (
                        <li key={index}>
                          <span className="trip-time">{since(event.seconds)}</span>
                          <span className="trip-dot" style={{ background: kind.color }} />
                          <div>
                            <span className="trip-kind">{kind.label}</span>
                            {located ? (
                              <button
                                className="trip-title"
                                onClick={() => setFocus([event.latitude, event.longitude])}
                                title="Show on the map"
                              >
                                {event.title}
                              </button>
                            ) : (
                              <strong className="trip-title">{event.title}</strong>
                            )}
                            {event.detail && <p className="muted">{event.detail}</p>}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

export default TripDetail;
