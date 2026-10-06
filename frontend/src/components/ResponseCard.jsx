import { formatDuration } from "../routeStatus";

const CHOICE_LABELS = {
  police: "called police",
  reroute: "changed the route",
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
        <strong>Police coming</strong> to {jam?.name}, in about{" "}
        {formatDuration(police.eta_seconds)}.
      </>
    );
  } else if (status === "police_clearing" && police) {
    headline = (
      <>
        <strong>Police clearing traffic</strong> at {jam?.name}.
      </>
    );
  } else if (risk && risk.probability >= 0.3) {
    headline = (
      <>
        <strong>Traffic jam likely ahead</strong> ({Math.round(risk.probability * 100)}% chance).
      </>
    );
  } else {
    headline = (
      <>
        <strong>Road ahead is clear</strong>
      </>
    );
  }

  return (
    <section className="live-card response-card" aria-label="Jam warning">
      <div className="live-card-heading">
        <span className={`response-dot status-${status}`} />
        Jam warning
      </div>
      <p>{headline}</p>
      {decision && decision.choice && (
        <p className="muted">
          Last step: {CHOICE_LABELS[decision.choice]}
        </p>
      )}
    </section>
  );
}

export default ResponseCard;
