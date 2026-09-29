import { formatDuration } from "../routeStatus";

const CHOICE_LABELS = {
  police: "sent police",
  reroute: "re-routed the ambulance",
  monitor: "kept watching",
};

// The AI deadlock watch (corridor/response.py): risk of getting stuck
// ahead, and what it did about it (re-route or police).
function ResponseCard({ response }) {
  if (!response) {
    return null;
  }

  const { status, risk, jam, decision, police } = response;
  let headline;

  if (status === "police_en_route" && police) {
    headline = (
      <>
        <strong>Police on the way</strong> from {police.station} to{" "}
        {jam?.name}: arriving in about {formatDuration(police.eta_seconds)}.
      </>
    );
  } else if (status === "police_clearing" && police) {
    headline = (
      <>
        <strong>Police clearing traffic</strong> at {jam?.name}:{" "}
        {police.vehicles_waved} vehicles waved through so far.
      </>
    );
  } else if (risk && risk.probability >= 0.3) {
    headline = (
      <>
        <strong>Traffic building up ahead</strong>: {Math.round(risk.probability * 100)}%
        chance of getting stuck.
      </>
    );
  } else {
    headline = (
      <>
        <strong>Route ahead clear</strong>
        {risk ? ` · ${Math.round(risk.probability * 100)}% chance of getting stuck` : ""}
      </>
    );
  }

  return (
    <section className="live-card response-card" aria-label="AI deadlock watch">
      <div className="live-card-heading">
        <span className={`response-dot status-${status}`} />
        AI deadlock watch
      </div>
      <p>{headline}</p>
      {decision && decision.choice && (
        <p className="muted">
          Last decision: {CHOICE_LABELS[decision.choice]}
          {decision.choice === "reroute" &&
            decision.avoided &&
            ` around ${decision.avoided} (${decision.reason})`}
          {decision.reroute_saving_seconds != null &&
            ` · a new route was estimated at ~${formatDuration(
              Math.abs(decision.reroute_saving_seconds)
            )} ${decision.reroute_saving_seconds >= 0 ? "faster" : "slower"} (re-routing is off: it slowed trips in tests)`}
          {decision.police_saving_seconds != null &&
            ` · police would save ~${formatDuration(decision.police_saving_seconds)}`}
        </p>
      )}
    </section>
  );
}

export default ResponseCard;
