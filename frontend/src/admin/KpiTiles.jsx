import { formatDuration } from "../routeStatus";
import { formatPercent } from "./adminApi";

function Tile({ label, value, sub }) {
  return (
    <div className="admin-tile">
      <span className="admin-tile-label">{label}</span>
      <strong className="admin-tile-value">{value}</strong>
      {sub && <span className="admin-tile-sub">{sub}</span>}
    </div>
  );
}

// The 108 timeline, averaged: call -> patient -> hospital -> handover.
function TimelineTiles({ kpis }) {
  const total = [
    kpis.avg_to_patient_seconds, kpis.avg_scene_seconds,
    kpis.avg_transport_seconds, kpis.avg_handover_seconds,
  ].reduce((sum, value) => sum + (value || 0), 0);
  return (
    <>
      <h2 className="admin-subheading">108 trips: where the time goes (average)</h2>
      <div className="admin-tiles">
        <Tile label="To the patient" value={formatDuration(kpis.avg_to_patient_seconds)}
          sub="Dispatch to reaching the patient" />
        <Tile label="At the scene" value={formatDuration(kpis.avg_scene_seconds)}
          sub="Assessing and loading the patient" />
        <Tile label="To hospital" value={formatDuration(kpis.avg_transport_seconds)}
          sub="Patient on board to the hospital door" />
        <Tile label="Handover" value={formatDuration(kpis.avg_handover_seconds)}
          sub={`Pre-alerted ${formatPercent(kpis.pre_alert_rate)} of trips`} />
        <Tile label="Dispatch to handover" value={formatDuration(total)}
          sub="Back in service after this" />
        <Tile label="Diverted" value={kpis.diverted}
          sub="Sent on to another hospital on the way" />
      </div>
    </>
  );
}

function KpiTiles({ kpis }) {
  if (!kpis) return null;

  const byStatus = kpis.by_status || {};
  const stopped = (byStatus.CANCELLED || 0) + (byStatus.FAILED || 0);
  const finished = (byStatus.COMPLETED || 0) + stopped;
  // Trips the AI pre-trip model gave no planned time: on time and delay
  // are unknown for them, so they are left out of those figures.
  const withoutPlan = kpis.completed_without_plan || 0;
  const withoutPlanNote = withoutPlan
    ? ` · ${withoutPlan} without a plan left out`
    : "";

  return (
    <section className="admin-section" aria-label="Key figures">
      <h2 className="admin-subheading">All trips</h2>
      <div className="admin-tiles">
        <Tile
          label="Trips"
          value={kpis.total_requests}
          sub={`${byStatus.COMPLETED || 0} reached the hospital${stopped ? ` · ${stopped} stopped early` : ""}`}
        />
        <Tile
          label="Success rate"
          value={formatPercent(kpis.success_rate)}
          sub={`${byStatus.COMPLETED || 0} of ${finished} finished trips reached the hospital`}
        />
        <Tile
          label="Avg time to hospital"
          value={formatDuration(kpis.avg_response_seconds)}
          sub={
            kpis.avg_planned_seconds != null
              ? `Expected ${formatDuration(kpis.avg_planned_seconds)}`
              : "From leaving to the hospital door"
          }
        />
        <Tile
          label="On time"
          value={formatPercent(kpis.on_time_rate)}
          sub={`Within 1 min of the expected time, of ${kpis.completed_with_plan ?? 0} planned trips${withoutPlanNote}`}
        />
        <Tile
          label="Avg delay"
          value={formatDuration(kpis.avg_delay_seconds)}
          sub={`Beyond the expected time${withoutPlanNote}`}
        />
        <Tile
          label="Open complaints"
          value={kpis.open_grievances}
          sub="Not solved yet"
        />
      </div>

      {kpis.avg_transport_seconds != null && <TimelineTiles kpis={kpis} />}
    </section>
  );
}

export default KpiTiles;
