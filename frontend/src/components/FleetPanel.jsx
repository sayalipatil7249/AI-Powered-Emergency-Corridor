import Collapsible from "./Collapsible";
import ConditionSelect from "./ConditionSelect";
import { ambulanceColor, legLabel, LEVEL_CLASS } from "../fleet";
import { formatDuration } from "../routeStatus";

function statusText(ambulance) {
  if (ambulance.status === "arrived") {
    if (ambulance.stage === "handover") {
      return `At hospital · handing patient to doctors (${formatDuration(ambulance.handover_left_seconds)} left)`;
    }
    const arrived = `Reached hospital in ${formatDuration(ambulance.trip_time_seconds)}`;
    if (ambulance.stage === "returning") return `${arrived} · now free, driving back`;
    if (ambulance.stage === "available") return `${arrived} · back at base`;
    return arrived;
  }
  if (ambulance.status === "waiting") {
    return "Not left yet";
  }
  const stage = legLabel(ambulance.leg, Boolean(ambulance.pickup_point));
  const leg = stage ? `${stage} · ` : "";
  return ambulance.eta_seconds != null
    ? `${leg}ETA ${formatDuration(ambulance.eta_seconds)}`
    : `${leg}Driving`;
}

// The hospital's answer to the 108 pre-alert, and any diversion.
function hospitalLine(ambulance) {
  const alert = ambulance.pre_alert;
  const diverted = ambulance.diverted_from
    ? `Changed hospital (was ${ambulance.diverted_from}). `
    : "";
  if (alert?.status === "ready") {
    return `${diverted}${alert.hospital} is ready for the patient`;
  }
  if (alert?.status === "declined") {
    return `${alert.hospital} can't take the patient. Finding another hospital…`;
  }
  return diverted || null;
}

// Every ambulance on the road, one compact row each: priority, stage,
// ETA and what it is waiting for. Details (view buttons, the patient's
// condition, the route note) fold out on demand; the referee's decisions
// are on the Admin page; test tools are in their own panel.
function FleetPanel({
  ambulances = [],
  selectedAmbulanceId,
  onViewRoute,
  onChase,
  conditions,
  running,
  onConditionChange,
  onReportProblem,
  onCallPolice,
  // crewHref(ambulance) -> link to that ambulance's crew screen, shown
  // on each row once it has been sent.
  crewHref = null,
  busy,
  error,
}) {

  if (ambulances.length === 0) {
    return null;
  }


  // One short line for what an ambulance is waiting for, if anything.
  const waitingFor = (ambulance) => {
    if (ambulance.give_way) {
      return `Letting ${ambulance.give_way.give_way_to} go first at ${ambulance.give_way.signal_name}`;
    }
    return ambulance.delay_reason ? `Stopped: ${ambulance.delay_reason}` : null;
  };

  return (
    <section className="panel-section fleet">
      <div className="section-heading">
        <h2>Ambulances</h2>
      </div>

      <ul className="fleet-list">
        {ambulances.map((ambulance) => (
          <li
            key={ambulance.vehicle_id}
            className={`fleet-item status-${ambulance.status} ${selectedAmbulanceId === ambulance.vehicle_id ? "selected" : ""}`}
            onClick={() => onViewRoute(ambulance.vehicle_id)}
            title="Show this ambulance on the map"
          >
            <div className="fleet-item-top">
              <span
                className="fleet-dot"
                style={{ background: ambulanceColor(ambulance.number) }}
              />
              <strong>{ambulance.label}</strong>
              {ambulance.unit && (
                <span className={`unit-badge unit-${ambulance.unit.kind}`}
                  title={`${ambulance.unit.id}: ${ambulance.unit.kind_label} from ${ambulance.unit.station}`}>
                  {ambulance.unit.kind}
                </span>
              )}
              <span className={`level-badge ${LEVEL_CLASS[ambulance.level]}`}>
                {ambulance.level_name}
              </span>
              <span className="fleet-status">{statusText(ambulance)}</span>
            </div>

            {crewHref && (
              <a
                className="crew-screen-link"
                href={crewHref(ambulance)}
                target="_blank"
                rel="noreferrer"
                onClick={(event) => event.stopPropagation()}
                title="The tablet in this ambulance: what its crew sees"
              >
                Open crew screen for {ambulance.unit?.id || ambulance.label}
              </a>
            )}

            {hospitalLine(ambulance) && (
              <span className="fleet-line hospital-line">{hospitalLine(ambulance)}</span>
            )}

            {waitingFor(ambulance) && (
              <span className="fleet-line waiting" role="status">
                {waitingFor(ambulance)}
              </span>
            )}

            {running && ambulance.status === "driving" && onCallPolice && (
              <button
                type="button"
                className="button button-secondary police-call-button"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  onCallPolice(ambulance.vehicle_id);
                }}
                title="Stuck? Call the police station that can reach you fastest (at most once every 2 min)"
              >
                Call police: we&apos;re stuck
              </button>
            )}

            {/* Clicks inside the details must not re-select the row. */}
            <div onClick={(event) => event.stopPropagation()}>
              <Collapsible title="Details">
                <span className="fleet-route">
                  {ambulance.start_name} → {ambulance.hospital_name}
                </span>
                <div className="fleet-view-actions">
                  <button type="button" className="button button-secondary"
                    onClick={() => onViewRoute(ambulance.vehicle_id)}>
                    Show on map
                  </button>
                  <button type="button" className="button button-secondary"
                    onClick={() => onChase(ambulance.vehicle_id)}>
                    Follow in 3D
                  </button>
                </div>
                <label className="fleet-condition">
                  <span>Patient</span>
                  <ConditionSelect
                    conditions={conditions}
                    value={ambulance.condition}
                    disabled={!running || ambulance.status === "arrived" || busy}
                    onChange={(condition) =>
                      onConditionChange(ambulance.vehicle_id, condition)
                    }
                  />
                </label>
                {ambulance.unit && (
                  <span className="fleet-line">
                    108 {ambulance.unit.id} · {ambulance.unit.kind_label} · from {ambulance.unit.station}
                  </span>
                )}
                {onReportProblem && ambulance.request_id && (
                  <button type="button" className="button button-secondary"
                    onClick={() => onReportProblem(ambulance.request_id)}>
                    Report a problem with this trip
                  </button>
                )}
                {ambulance.routing_decision && (
                  <span className="fleet-line">{ambulance.routing_decision.explanation}</span>
                )}
              </Collapsible>
            </div>
          </li>
        ))}
      </ul>


      {error && <p className="field-error">{error}</p>}
    </section>
  );
}

export default FleetPanel;
