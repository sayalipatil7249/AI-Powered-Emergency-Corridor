import { formatDuration } from "../routeStatus";

const KIND_LABELS = {
  decision: "Decision",
  warning: "Warning",
  note: "Update",
  response: "Deadlock response",
  police: "Police alert",
};

// Latest messages from the AI supervisor agent (agent/), newest first.
function AgentFeed({ messages = [], departTime }) {
  if (messages.length === 0) {
    return null;
  }

  const latest = messages.slice(-3).reverse();

  return (
    <section className="agent-feed" aria-label="AI agent messages">
      <div className="agent-feed-heading">
        <span className="agent-dot" />
        AI agent
      </div>

      <ul>
        {latest.map((message, index) => (
          <li
            key={`${message.simulation_time}-${index}`}
            className={`agent-message kind-${message.kind}`}
          >
            <div className="agent-message-meta">
              <span>{KIND_LABELS[message.kind] || "Update"}</span>
              {departTime != null && message.simulation_time != null && (
                <span>
                  {formatDuration(
                    Math.max(0, message.simulation_time - departTime)
                  )}{" "}
                  into trip
                </span>
              )}
            </div>
            <p>{message.text}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default AgentFeed;
