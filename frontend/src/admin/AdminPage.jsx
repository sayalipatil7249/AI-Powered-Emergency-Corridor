import { useCallback, useEffect, useState } from "react";
import AdminHeader from "./AdminHeader";

import AdminMap from "./AdminMap";
import "./admin.css";
import AdminSidebar, { SectionHeading } from "./AdminSidebar";
import { useAdminSection } from "./adminSections";
import ConditionSelect from "../components/ConditionSelect";
import { API_URL, useLiveState } from "../api";
import { ambulanceColor, ambulanceStage, DEFAULT_CONDITIONS, LEVEL_CLASS } from "../fleet";
import { formatDuration } from "../routeStatus";
import { cleanPhone, PHONE_EXAMPLE, showPhone } from "../phone";

const LOG_REFRESH_MS = 5000;

const STATUS_LABELS = {
  waiting: "Waiting to set off",
  driving: "Driving",
  arrived: "Arrived",
};

const SOURCE_LABELS = { dispatch: "Dispatch", crew: "Crew", admin: "Control room" };

// A crew that raises its patient to Critical this often in one run is
// worth a look (changes apply at once; this log is the safeguard).
const CRITICAL_CHANGES_FLAG = 2;

function clock(value) {
  return value ? new Date(value).toLocaleTimeString() : "--";
}

// Read a JSON endpoint now and every LOG_REFRESH_MS; null while loading,
// { error } when the backend or its database does not answer.
function usePolled(path) {
  const [data, setData] = useState(null);

  const load = useCallback(() => {
    fetch(`${API_URL}${path}`)
      .then(async (response) => {
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        setData(result);
      })
      .catch((error) => setData({ error: error.message }));
  }, [path]);

  useEffect(() => {
    load();
    const timer = setInterval(load, LOG_REFRESH_MS);
    return () => clearInterval(timer);
  }, [load]);

  return [data, load];
}

// The control room: every ambulance, the junction referee's decisions,
// the priority log and the police. It watches and can override (a
// patient's priority, stopping the simulation); nothing waits for its
// approval - the crews' priority applies at once.
// Police alert status, in plain words.
const ALERT_LABELS = {
  ALERTED: "Police called",
  EN_ROUTE: "Police coming",
  ON_SCENE: "Clearing traffic",
  PASSED: "Ambulance got through",
  CANCELLED: "Not needed",
};

// embedded: only the live overview, for the dashboard (#/admin).
function AdminPage({ embedded = false }) {
  const section = useAdminSection("#/admin/live");
  // (embedded: the dashboard shows only the live overview)
  const { state, connected } = useLiveState();
  const [conditions, setConditions] = useState(DEFAULT_CONDITIONS);
  const [selectedId, setSelectedId] = useState(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const [priorityLog, reloadPriorityLog] = usePolled("/fleet/priority-log?limit=200");
  const [policeCalls] = usePolled("/police-stations/calls?limit=50");
  const [stations, reloadStations] = usePolled("/police-stations/");
  const [phoneDrafts, setPhoneDrafts] = useState({});

  useEffect(() => {
    fetch(`${API_URL}/fleet/conditions`)
      .then((response) => response.json())
      .then(setConditions)
      .catch(() => {});
  }, []);

  const ambulances = state?.ambulances || [];
  const decisions = [...(state?.referee?.decisions || [])].reverse();
  const policeAlerts = state?.police_watch?.alerts || [];
  const running = ["starting", "warming_up", "running"].includes(state?.status);
  const changes = Array.isArray(priorityLog) ? priorityLog : [];

  // Crew changes up to Critical in the current run, per ambulance.
  const currentRun = changes[0]?.trip_id;
  const criticalChanges = {};
  for (const change of changes) {
    if (change.trip_id === currentRun && change.source === "crew" && change.level === 1) {
      criticalChanges[change.ambulance_id] = (criticalChanges[change.ambulance_id] || 0) + 1;
    }
  }

  const request = async (path, options, success) => {
    try {
      setBusy(true);
      setNotice("");
      const response = await fetch(`${API_URL}${path}`, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "The request failed.");
      setNotice(success(result));
    } catch (error) {
      setNotice(error.message);
    } finally {
      setBusy(false);
    }
  };

  // The control room calls police for a stuck ambulance.
  const callPolice = (ambulance) =>
    request(
      `/fleet/ambulances/${ambulance.vehicle_id}/call-police`,
      { method: "POST", body: JSON.stringify({ called_by: "control room" }) },
      (result) => `${result.label}: ${result.message}`
    );

  const overrideCondition = (ambulance, condition) =>
    request(
      `/fleet/ambulances/${ambulance.vehicle_id}/condition`,
      {
        method: "PUT",
        body: JSON.stringify({ condition, source: "admin", changed_by: "control room" }),
      },
      (result) => {
        reloadPriorityLog();
        return `${result.label} set to ${result.condition_label} (${result.level_name}) by the control room.`;
      }
    );

  const stopSimulation = () =>
    request("/simulation/stop", { method: "POST" }, () => "Stopping the simulation.");

  // Empty: allowed (removes the number); otherwise the cleaned number.
  const phoneCheck = (text) => ((text || "").trim() ? cleanPhone(text) : {});

  const savePhone = (station) =>
    request(
      `/police-stations/${station.station_id}/phone`,
      {
        method: "PUT",
        // Saved in the form Twilio calls ("+919876543210"); empty removes it.
        body: JSON.stringify({
          phone: cleanPhone(phoneDrafts[station.station_id]).phone || null,
        }),
      },
      (result) => {
        setPhoneDrafts((drafts) => ({ ...drafts, [station.station_id]: undefined }));
        reloadStations();
        return result.message;
      }
    );

  const driving = ambulances.filter((item) => item.status === "driving");
  const givingWay = ambulances.filter((item) => item.give_way).length;
  const activeAlerts = policeAlerts.filter((alert) =>
    ["ALERTED", "EN_ROUTE", "ON_SCENE"].includes(alert.status)
  ).length;

  // Live overview: numbers, map and every ambulance (shown on the
  // admin dashboard).
  const liveBlock = (
    <>
      <section className="cr-kpis" aria-label="Overview">
        <div className="cr-kpi">
          <span>Simulation</span>
          <strong>{state?.status || "--"}</strong>
        </div>
        <div className="cr-kpi">
          <span>Ambulances driving</span>
          <strong>{driving.length}/{ambulances.length}</strong>
        </div>
        <div className="cr-kpi">
          <span>Critical patients</span>
          <strong>{ambulances.filter((item) => item.level === 1 && item.status !== "arrived").length}</strong>
        </div>
        <div className="cr-kpi">
          <span>Giving way now</span>
          <strong>{givingWay}</strong>
        </div>
        <div className="cr-kpi">
          <span>Shared-signal decisions</span>
          <strong>{decisions.length}</strong>
        </div>
        <div className="cr-kpi">
          <span>Active police alerts</span>
          <strong>{activeAlerts}</strong>
        </div>
      </section>

      <div className="cr-main">
        <section className="cr-card cr-map-card">
          <AdminMap
            ambulances={ambulances}
            policeAlerts={policeAlerts}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        </section>

        <section className="cr-card">
          <h2>Ambulances</h2>
          {ambulances.length === 0 ? (
            <p className="cr-empty">No ambulances. Start a simulation from the dashboard.</p>
          ) : (
            <ul className="cr-ambulances">
              {ambulances.map((item) => (
                <li
                  key={item.vehicle_id}
                  className={`cr-ambulance ${item.vehicle_id === selectedId ? "selected" : ""}`}
                  onClick={() => setSelectedId(item.vehicle_id)}
                >
                  <div className="cr-ambulance-top">
                    <span className="fleet-dot" style={{ background: ambulanceColor(item.number) }} />
                    <strong>{item.label}</strong>
                    <span className={`level-badge ${LEVEL_CLASS[item.level]}`}>{item.level_name}</span>
                    {criticalChanges[item.vehicle_id] >= CRITICAL_CHANGES_FLAG && (
                      <span className="cr-flag" title="The crew raised this patient to Critical several times in this run">
                        {criticalChanges[item.vehicle_id]}× raised to Critical
                      </span>
                    )}
                    <span className="cr-muted cr-right">
                      {item.status === "driving" && item.eta_seconds != null
                        ? `ETA ${formatDuration(item.eta_seconds)}`
                        : item.status === "arrived"
                          ? `Arrived in ${formatDuration(item.trip_time_seconds)}`
                          : STATUS_LABELS[item.status]}
                    </span>
                  </div>
                  {/* What it is doing right now */}
                  {ambulanceStage(item) && (
                    <span className={`ambulance-stage stage-${ambulanceStage(item).tone}`}>
                      {ambulanceStage(item).text}
                      {item.stage === "handover" && item.handover_left_seconds != null &&
                        ` (${formatDuration(item.handover_left_seconds)} left)`}
                      {item.delay_reason && item.status === "driving" && ` · stopped: ${item.delay_reason}`}
                    </span>
                  )}
                  <span className="cr-muted">
                    {item.unit && `108 ${item.unit.id} (${item.unit.kind === "ALS" ? "advanced" : "basic"}) · `}
                    {item.start_name} → {item.hospital_name}
                    {item.stops != null && ` · ${item.stops} ${item.stops === 1 ? "stop" : "stops"}`}
                  </span>
                  {item.give_way && (
                    <p className="give-way">
                      Giving way at {item.give_way.signal_name} to {item.give_way.give_way_to}
                      {item.advised_speed != null &&
                        ` · slowed to ${Math.round(item.advised_speed * 3.6)} km/h`}
                    </p>
                  )}
                  <label className="fleet-condition" onClick={(event) => event.stopPropagation()}>
                    <span>Override patient</span>
                    <ConditionSelect
                      conditions={conditions}
                      value={item.condition}
                      disabled={!running || item.status === "arrived" || busy}
                      onChange={(condition) => overrideCondition(item, condition)}
                    />
                  </label>
                  {running && item.status === "driving" && (
                    <button
                      type="button"
                      className="button button-secondary police-call-button"
                      disabled={busy}
                      onClick={(event) => {
                        event.stopPropagation();
                        callPolice(item);
                      }}
                      title="Call the police station that can reach this ambulance's jam fastest (at most once every 2 min)"
                    >
                      🚓 Call police for {item.label}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

    </>
  );

  if (embedded) {
    return (
      <div className="cr-embedded">
        {notice && <p className="cr-notice" role="status">{notice}</p>}
        {liveBlock}
      </div>
    );
  }

  return (
    <div className="admin-page">
      <AdminHeader>
        <span className={`connection ${connected ? "online" : "offline"}`}>
          <i />
          {connected ? "Live" : "Connecting"}
        </span>
        <button
          className="button button-secondary cr-stop"
          disabled={!running || busy}
          onClick={stopSimulation}
        >
          Stop simulation
        </button>
      </AdminHeader>

      <div className="admin-shell">
      <AdminSidebar />
      <main className="admin-content">
      <SectionHeading section={section} />

      {notice && <p className="cr-notice" role="status">{notice}</p>}


      <div className="cr-section">
        {section === "who-first" && (
        <section className="cr-card">
          {decisions.length === 0 ? (
            <p className="cr-empty">No shared junctions yet in this run.</p>
          ) : (
            <ul className="cr-log">
              {decisions.map((decision) => (
                <li key={`${decision.time}-${decision.signal_id}-${decision.loser}-${decision.reason}`}>
                  <span className="cr-muted">t={Math.round(decision.time)} s</span>
                  <span>
                    {decision.text}
                    {decision.waited_seconds != null && ` Waited ${decision.waited_seconds} s.`}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        )}

        {section === "priority-log" && (
        <section className="cr-card">
          {priorityLog?.error ? (
            <p className="field-error">Could not read the log: {priorityLog.error}</p>
          ) : changes.length === 0 ? (
            <p className="cr-empty">No priority settings saved yet.</p>
          ) : (
            <div className="cr-table-wrap">
              <table className="cr-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Run</th>
                    <th>Ambulance</th>
                    <th>Patient</th>
                    <th>Was</th>
                    <th>By</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((change, index) => (
                    <tr key={`${change.created_at}-${index}`}>
                      <td>{clock(change.created_at)}</td>
                      <td className="cr-muted">{change.trip_id}</td>
                      <td>{change.ambulance_label}</td>
                      <td>
                        <span className={`level-badge ${LEVEL_CLASS[change.level]}`}>
                          {change.condition_label}
                        </span>
                      </td>
                      <td className="cr-muted">
                        {conditions.find((item) => item.condition === change.previous_condition)
                          ?.condition_label || "--"}
                      </td>
                      <td>{SOURCE_LABELS[change.source] || change.source}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        )}

        {section === "police" && (
        <section className="cr-card">
          <h3 className="cr-subheading">Calls</h3>
          {policeCalls?.error ? (
            <p className="field-error">Could not read the call log: {policeCalls.error}</p>
          ) : !Array.isArray(policeCalls) || policeCalls.length === 0 ? (
            <p className="cr-empty">No police alerts yet.</p>
          ) : (
            <div className="cr-table-wrap">
              <table className="cr-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Station</th>
                    <th>Road</th>
                    <th>Alert</th>
                    <th>Call</th>
                  </tr>
                </thead>
                <tbody>
                  {policeCalls.map((call) => (
                    <tr key={call.alert_id}>
                      <td>{clock(call.created_at)}</td>
                      <td>{call.station}</td>
                      <td>{call.road}</td>
                      <td>{ALERT_LABELS[call.alert_status] || call.alert_status}</td>
                      <td className="cr-muted">
                        {call.call_status || "--"}
                        {call.phone_called && ` · ${call.phone_called}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        )}

        {section === "police" && (
        <section className="cr-card">
          <h3 className="cr-subheading">Phone numbers</h3>
          <p className="cr-warning">
            Testing: save only test phones. A call to a real police number
            would be a false emergency.
          </p>
          <p className="phone-format">
            <strong>Number format:</strong> +91 followed by the 10-digit mobile
            number, e.g. <code>{PHONE_EXAMPLE}</code>. Spaces are fine, and if
            you type only the 10 digits, +91 is added for you. On a Twilio
            trial account, verify the number in Twilio first.
          </p>
          {stations?.error ? (
            <p className="field-error">Could not read the stations: {stations.error}</p>
          ) : !Array.isArray(stations) ? (
            <p className="cr-empty">Loading…</p>
          ) : (
            <ul className="cr-stations">
              {stations.map((station) => (
                <li key={station.station_id}>
                  <div>
                    <strong>{station.name}</strong>
                    <span className="cr-muted">
                      {station.kind}
                      {station.road_name && ` · ${station.road_name}`}
                    </span>
                  </div>
                  <span className="cr-muted phone-saved">
                    {station.phone
                      ? `Saved: ${station.phone}`
                      : "No number saved (calls go to the default test phone in .env)"}
                  </span>
                  <div className="field-row">
                    <input
                      type="tel"
                      inputMode="tel"
                      aria-label={`Phone for ${station.name}`}
                      placeholder={PHONE_EXAMPLE}
                      value={phoneDrafts[station.station_id] ?? ""}
                      onChange={(event) =>
                        setPhoneDrafts((drafts) => ({
                          ...drafts,
                          [station.station_id]: event.target.value,
                        }))
                      }
                    />
                    <button
                      className="button button-secondary"
                      disabled={
                        busy || phoneDrafts[station.station_id] === undefined ||
                        Boolean(phoneCheck(phoneDrafts[station.station_id]).error)
                      }
                      onClick={() => savePhone(station)}
                    >
                      Save
                    </button>
                  </div>
                  {phoneDrafts[station.station_id] !== undefined && (
                    phoneCheck(phoneDrafts[station.station_id]).error ? (
                      <span className="field-error">{phoneCheck(phoneDrafts[station.station_id]).error}</span>
                    ) : (
                      <span className="phone-ok">
                        {phoneDrafts[station.station_id].trim()
                          ? `✓ Will be saved as ${showPhone(phoneCheck(phoneDrafts[station.station_id]).phone)}`
                          : "Saving an empty box removes the number."}
                      </span>
                    )
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
        )}
      </div>
      </main>
      </div>
    </div>
  );
}

export default AdminPage;
