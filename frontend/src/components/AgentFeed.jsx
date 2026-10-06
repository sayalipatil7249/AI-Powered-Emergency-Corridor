import { formatDuration } from "../routeStatus";

const KIND_LABELS = {
  decision: "Update",
  warning: "Problem",
  note: "Update",
  response: "Jam warning",
  police: "Police",
  referee: "Who goes first",
  priority: "Patient",
  hospital: "Hospital",
};

// Latest messages (simulation and AI agent), newest first.
function AgentFeed({ messages = [], departTime }) {
  if (messages.length === 0) {
    return null;
  }

  const latest = messages.slice(-3).reverse();

  return (
    <section className="agent-feed" aria-label="Latest updates">
      <div className="agent-feed-heading">
        <span className="agent-dot" />
        Latest
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
                  after leaving
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
