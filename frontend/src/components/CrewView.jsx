import { LEVEL_CLASS } from "../fleet";
import { formatDuration, signalLabel } from "../routeStatus";

// What the crew needs right now, in a few big words: the stage of the
// trip, where to, whether the hospital is ready, and what (if anything)
// is holding them up. tone: "idle", "ok", "warn" or "bad".
function stageCard(run) {
  if (!run) {
    return {
      tone: "idle",
      kicker: "No trip",
      title: "Waiting for a call",
      detail: "The control room sends your next patient to this screen.",
    };
  }
  const hospital = run.hospital_name;
  if (run.status === "waiting") {
    return { tone: "idle", kicker: "Getting ready", title: "Leaving soon", detail: `Then to ${run.start_name}` };
  }
  if (run.status === "arrived") {
    if (run.stage === "handover") {
      return {
        tone: "ok",
        kicker: "At the hospital",
        title: "Handing over the patient",
        detail: `${hospital} · ${formatDuration(run.handover_left_seconds)} left`,
      };
    }
    if (run.stage === "returning") {
      return { tone: "idle", kicker: "Patient handed over", title: "Driving back to base", detail: "Free for a new call" };
    }
    if (run.stage === "available") {
      return { tone: "idle", kicker: "Back at base", title: "Free for a new call", detail: run.unit?.station };
    }
    return { tone: "ok", kicker: "Reached the hospital", title: hospital };
  }

  const hasPickup = Boolean(run.pickup_point);
  if (hasPickup && run.leg === "to_patient") {
    return { tone: "idle", kicker: "Going to the patient", title: run.start_name, detail: `Then to ${hospital}` };
  }

  // The hospital's answer to the 108 pre-alert.
  const alert = run.pre_alert;
  let tone = "idle";
  let detail = "Telling the hospital you are coming…";
  if (alert?.status === "ready") {
    tone = "ok";
    detail = `${alert.need_label || "Doctors"} ready`;
  } else if (alert?.status === "declined") {
    tone = "bad";
    detail = `${alert.hospital} can't take the patient. Finding another hospital…`;
  }
  if (run.diverted_from) detail = `Changed hospital (was ${run.diverted_from}) · ${detail}`;

  if (hasPickup && run.leg === "at_patient") {
    return { tone, kicker: "At the patient", title: "Loading the patient", detail: `Then to ${hospital} · ${detail}` };
  }
  return { tone, kicker: "Patient on board", title: hospital, detail };
}

// The next signal, in one line (the corridor turns them green ahead).
function signalLine(routeStatuses) {
  if (routeStatuses.length === 0) return null;
  const ahead = routeStatuses.filter((signal) => signal.status !== "PASSED");
  const passed = routeStatuses.length - ahead.length;
  const count = `${passed} of ${routeStatuses.length} passed`;
  if (ahead.length === 0) return { light: "passed", text: "All signals passed", count };
  const next = ahead[0];
  const name = signalLabel(next);
  const text = {
    GREEN: ["green", `Next signal green · ${name}`],
    TURNING: ["green", `Next signal turning green · ${name}`],
    READY: ["ready", `Next signal getting ready · ${name}`],
  }[next.status] || ["waiting", `Next signal · ${name}`];
  return { light: text[0], text: text[1], count };
}

// What is holding the ambulance up right now, if anything.
function holdUp(run) {
  if (!run || run.status !== "driving") return null;
  if (run.give_way) {
    return { tone: "warn", text: `Letting ${run.give_way.give_way_to} go first at ${run.give_way.signal_name}` };
  }
  if (run.delay_reason) return { tone: "bad", text: `Stopped: ${run.delay_reason}` };
  return null;
}

// New layout, Crew page: the tablet in one ambulance. Big map, one card
// that changes with the trip, two big buttons; only this ambulance's job
// (planning, other ambulances and test tools are the control room's).
function CrewView({
  run,
  routeStatuses = [],
  running,
  onCallPolice,
  onReportProblem,
  busy = false,
  message = "",
  map,
  // The big "Arrives in" card, beside the trip card.
  eta = null,
}) {
  const card = stageCard(run);
  const signals = run?.status === "driving" ? signalLine(routeStatuses) : null;
  const stuck = holdUp(run);
  const driving = running && run?.status === "driving";

  return (
    <main className="nl-crew">

      <div className="nl-crew-top">
      <section className={`nl-card tone-${stuck?.tone === "bad" ? "bad" : card.tone}`} aria-live="polite">
        <div className="nl-card-main">
          <span className="nl-card-kicker">{card.kicker}</span>
          <strong className="nl-card-title">{card.title}</strong>
          {card.detail && <span className="nl-card-detail">{card.detail}</span>}
        </div>
        <div className="nl-card-side">
          {run && (
            <span className={`level-badge ${LEVEL_CLASS[run.level]}`}>
              {run.condition_label} · {run.level_name}
            </span>
          )}
          {run?.unit && (
            <span className="nl-card-unit">
              {run.unit.id} · {run.unit.kind === "ALS" ? "Advanced" : "Basic"}
            </span>
          )}
        </div>
        {(stuck || signals) && (
          <div className="nl-card-strip">
            {stuck ? (
              <span className={`nl-hold tone-${stuck.tone}`} role="status">{stuck.text}</span>
            ) : (
              <span className="nl-signal">
                <i className={`nl-light ${signals.light}`} aria-hidden="true" />
                {signals.text}
              </span>
            )}
            {signals && <span className="nl-card-count">{signals.count}</span>}
          </div>
        )}
      </section>
      {eta}
      </div>

      <div className="nl-crew-map">{map}</div>

      <div className="nl-crew-actions">
        <button
          type="button"
          className={`nl-big-button police ${stuck?.tone === "bad" ? "urgent" : ""}`}
          disabled={!driving || busy}
          onClick={() => onCallPolice(run.vehicle_id)}
          title="Stuck? The police station that can reach you fastest is called (at most once every 2 min)"
        >
          Call police
          <small>{driving ? "We're stuck" : "While driving"}</small>
        </button>
        <button
          type="button"
          className="nl-big-button"
          onClick={() => onReportProblem(run?.request_id || "")}
        >
          Report a problem
          <small>Delay, hospital, police…</small>
        </button>
      </div>
      {message && <p className="nl-crew-message" role="status">{message}</p>}
    </main>
  );
}

export default CrewView;
