import ThemeToggle from "./ThemeToggle";
import SvgIcon from "./SvgIcon";
import { HOSPITAL_ICON } from "../icons";

// Header of the live app. Each role has its own page (no tabs between
// them); "Switch role" (under the title) goes back to the start page, for demos. Start /
// Stop belong to the control room (it sends ambulances); the test tools
// are in its side panel.
function NewHeader({
  tab,
  subtitle,
  connected,
  running,
  starting,
  stopping,
  startDisabled = false,
  onStart,
  onStop,
  onReportProblem,
}) {
  const control = tab === "control";

  return (
    <header className="header nl-header">
      <div className="brand">
        <SvgIcon svg={HOSPITAL_ICON} className="brand-mark" />
        <div>
          <h1>Emergency Corridor</h1>
          <p>
            {subtitle}
            {" · "}
            <a href="#/" className="nl-switch-role"
              title="Back to the start page (for demos: in real use each role has its own login)">
              Switch role
            </a>
          </p>
        </div>
      </div>

      <div className="header-actions">
        <span className={`connection ${connected ? "online" : "offline"}`}>
          <i />
          {connected ? "Live" : "Connecting"}
        </span>
        <ThemeToggle />

        {control && (
          <>
            <button type="button" className="button button-secondary"
              onClick={onReportProblem}>
              Report a problem
            </button>
            <button className="button button-primary" onClick={onStart}
              disabled={starting || startDisabled}>
              {starting ? "Starting…" : "Start"}
            </button>
            <button className="button button-secondary" onClick={onStop}
              disabled={stopping || !running}>
              {stopping ? "Stopping…" : "Stop"}
            </button>
          </>
        )}
      </div>
    </header>
  );
}

export default NewHeader;
