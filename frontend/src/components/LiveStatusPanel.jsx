import AgentFeed from "./AgentFeed";
import LiveTrafficCard from "./LiveTrafficCard";
import PoliceAlertCard from "./PoliceAlertCard";
import ResponseCard from "./ResponseCard";

// Middle of the side panel: the accident demo button, live traffic, the
// AI deadlock watch, police and the AI agent's messages. They used to
// float over the map; here they never hide it.
function LiveStatusPanel({
  ambulance,
  liveTraffic,
  response,
  policeWatch,
  policeBoard,
  policeStations = [],
  agentFeed = [],
  onSimulateIncident,
  incidentBusy = false,
}) {
  const arrived = ambulance?.status === "COMPLETED";
  const driving = Boolean(ambulance) && !arrived;
  const empty = !liveTraffic && !driving && agentFeed.length === 0;

  return (
    <section className="panel-section live-status" aria-label="Live status">
      <div className="section-heading">
        <h2>Live status</h2>
      </div>

      {onSimulateIncident && (
        <button
          className="button incident-button"
          onClick={onSimulateIncident}
          disabled={incidentBusy || !driving}
          title="Block a road without signals ahead of the ambulance, to see the police alert, the phone call and the police clearing it"
        >
          {incidentBusy
            ? "Blocking a road…"
            : driving
              ? "⚠ Simulate accident ahead"
              : "⚠ Accident: after the ambulance sets off"}
        </button>
      )}

      <LiveTrafficCard live={liveTraffic} tripRunning={Boolean(ambulance)} />
      {driving && (
        <>
          <ResponseCard response={response} />
          <PoliceAlertCard
            policeWatch={policeWatch}
            board={policeBoard}
            stations={policeStations}
          />
        </>
      )}
      <AgentFeed messages={agentFeed} departTime={ambulance?.depart_time} />

      {empty && (
        <p className="live-status-empty muted">
          Live traffic, police alerts and AI agent messages appear here
          once the simulation is running.
        </p>
      )}
    </section>
  );
}

export default LiveStatusPanel;
