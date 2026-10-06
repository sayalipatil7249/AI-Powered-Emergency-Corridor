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
    <div className="live-status" aria-label="Live status">

      {onSimulateIncident && (
        <button
          className="button incident-button"
          onClick={onSimulateIncident}
          disabled={incidentBusy || !driving}
          title="Test: block a road ahead and watch the police clear it"
        >
          {incidentBusy
            ? "Blocking a road…"
            : driving
              ? "⚠ Test: accident ahead"
              : "⚠ Test accident (once the ambulance is driving)"}
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
          Traffic, police and other updates show here once you press Start.
        </p>
      )}
    </div>
  );
}

export default LiveStatusPanel;
