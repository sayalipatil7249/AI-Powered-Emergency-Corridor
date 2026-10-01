import { formatDuration } from "../routeStatus";
import { formatPercent } from "./adminApi";

// Finished requests by outcome. Status colours always come with the
// label and the count, never alone.
const OUTCOMES = [
  { key: "COMPLETED", label: "Completed", tone: "good" },
  { key: "CANCELLED", label: "Cancelled", tone: "warning" },
  { key: "FAILED", label: "Failed", tone: "critical" },
];

function Tile({ label, value, sub }) {
  return (
    <div className="admin-tile">
      <span className="admin-tile-label">{label}</span>
      <strong className="admin-tile-value">{value}</strong>
      {sub && <span className="admin-tile-sub">{sub}</span>}
    </div>
  );
}

function KpiTiles({ kpis }) {
  if (!kpis) return null;

  const byStatus = kpis.by_status || {};
  const finished = OUTCOMES.reduce((sum, item) => sum + (byStatus[item.key] || 0), 0);
  // Trips the AI pre-trip model gave no planned time: on time and delay
  // are unknown for them, so they are left out of those figures.
  const withoutPlan = kpis.completed_without_plan || 0;
  const withoutPlanNote = withoutPlan
    ? ` · ${withoutPlan} without a plan left out`
    : "";

  return (
    <section className="admin-section" aria-label="Key figures">
      <div className="admin-tiles">
        <Tile
          label="Total requests"
          value={kpis.total_requests}
          sub={
            byStatus.IN_PROGRESS
              ? `${byStatus.IN_PROGRESS} in progress`
              : "All finished"
          }
        />
        <Tile
          label="Success rate"
          value={formatPercent(kpis.success_rate)}
          sub={`${byStatus.COMPLETED || 0} of ${finished} finished requests`}
        />
        <Tile
          label="Avg response time"
          value={formatDuration(kpis.avg_response_seconds)}
          sub={
            kpis.avg_planned_seconds != null
              ? `Planned ${formatDuration(kpis.avg_planned_seconds)}`
              : "Dispatch to arrival"
          }
        />
        <Tile
          label="On time"
          value={formatPercent(kpis.on_time_rate)}
          sub={`Within 1 min of plan, of ${kpis.completed_with_plan ?? 0} planned trips${withoutPlanNote}`}
        />
        <Tile
          label="Avg delay"
          value={formatDuration(kpis.avg_delay_seconds)}
          sub={`Beyond the planned time${withoutPlanNote}`}
        />
        <Tile
          label="Open grievances"
          value={kpis.open_grievances}
          sub="Open or in progress"
        />
      </div>

      {finished > 0 && (
        <div className="admin-outcomes">
          <div
            className="admin-outcome-bar"
            role="img"
            aria-label={OUTCOMES.map(
              (item) => `${item.label} ${byStatus[item.key] || 0}`
            ).join(", ")}
          >
            {OUTCOMES.map((item) =>
              byStatus[item.key] ? (
                <span
                  key={item.key}
                  className={`tone-${item.tone}`}
                  style={{ flexGrow: byStatus[item.key] }}
                  title={`${item.label}: ${byStatus[item.key]}`}
                />
              ) : null
            )}
          </div>
          <ul className="admin-outcome-legend">
            {OUTCOMES.map((item) => (
              <li key={item.key}>
                <span className={`admin-swatch tone-${item.tone}`} />
                {item.label}
                <strong>{byStatus[item.key] || 0}</strong>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

export default KpiTiles;
