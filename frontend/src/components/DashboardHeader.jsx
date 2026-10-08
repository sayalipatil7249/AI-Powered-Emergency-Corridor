import ThemeToggle from "./ThemeToggle";
import SvgIcon from "./SvgIcon";
import { HOSPITAL_ICON } from "../icons";

function DashboardHeader({
  connected,
  onStartSimulation,
  onStopSimulation,
  starting,
  startDisabled = false,
  stopping,
  running,
  tripLabel,
  playbackSpeed,
  onPlaybackSpeedChange,
  onReportProblem,
}) {
  return (
    <header className="header">
      <div className="brand">
        <SvgIcon svg={HOSPITAL_ICON} className="brand-mark" />
        <div>
          <h1>Emergency Corridor</h1>
          <p>{tripLabel}</p>
        </div>
      </div>

      <div className="header-actions">
        <span className={`connection ${connected ? "online" : "offline"}`}>
          <i />
          {connected ? "Live" : "Connecting"}
        </span>

        <ThemeToggle />

        {onReportProblem && (
          <button
            type="button"
            className="button button-secondary"
            onClick={onReportProblem}
            title="Complain about a delay, the route, a hospital, the police…"
          >
            Report a problem
          </button>
        )}

        <a
          className="button button-secondary"
          href="#/admin"
          target="_blank"
          rel="noreferrer"
          title="Admin: all ambulances, past trips and complaints"
        >
          Admin
        </a>

        {/* How fast the simulation plays on screen (like fast-forward);
            the ambulance's simulated speed is not affected. */}
        <div
          className="playback"
          role="group"
          aria-label="Speed"
          title="Play the simulation faster. The ambulance itself does not drive faster."
        >
          <span>Speed</span>
          {[1, 2, 5, 10].map((speed) => (
            <button
              key={speed}
              className={speed === playbackSpeed ? "active" : ""}
              onClick={() => onPlaybackSpeedChange(speed)}
              title={
                speed === 1
                  ? "Real time"
                  : `${speed} times faster`
              }
            >
              {speed}×
            </button>
          ))}
        </div>

        <button
          className="button button-primary"
          onClick={onStartSimulation}
          disabled={starting || startDisabled}
        >
          {starting ? "Starting…" : "Start"}
        </button>

        <button
          className="button button-secondary"
          onClick={onStopSimulation}
          disabled={stopping || !running}
        >
          {stopping ? "Stopping…" : "Stop"}
        </button>
      </div>
    </header>
  );
}

export default DashboardHeader;
