import { useLiveState } from "./api";
import SvgIcon from "./components/SvgIcon";
import ThemeToggle from "./components/ThemeToggle";
import { HOSPITAL_ICON } from "./icons";

// Start page: who is using this screen. Each role gets only
// its own page (a real system would ask for a login here):
//   108 control room   takes calls, sends ambulances, sees all of them
//   ambulance crew     one ambulance's tablet: only that ambulance's job
//   admin              records, complaints, performance
function RolePicker() {
  const { state, connected } = useLiveState();
  const units = state?.units || [];

  return (
    <div className="app nl-app">
      <header className="header nl-header">
        <div className="brand">
          <SvgIcon svg={HOSPITAL_ICON} className="brand-mark" />
          <div>
            <h1>Emergency Corridor</h1>
            <p>Choose your role</p>
          </div>
        </div>
        <div className="header-actions">
          <span className={`connection ${connected ? "online" : "offline"}`}>
            <i />
            {connected ? "Live" : "Connecting"}
          </span>
          <ThemeToggle />
        </div>
      </header>

      <main className="nl-roles">
        <h2 className="nl-roles-title">Who is using this screen?</h2>
        <p className="muted nl-roles-note">
          Each role sees only its own page. In a real system this would be a login.
        </p>

        <div className="nl-role-grid">
          <a className="nl-role-card" href="#/control">
            <strong>108 Control room</strong>
            <span>Take emergency calls (with AI help), send ambulances, watch every ambulance.</span>
            <span className="nl-role-go">Open the control room</span>
          </a>

          <div className="nl-role-card">
            <strong>Ambulance crew</strong>
            <span>The tablet in one ambulance: only that ambulance&apos;s job. Pick your ambulance.</span>
            <div className="nl-unit-list">
              {units.map((unit) => (
                <a key={unit.id} className="nl-unit" href={`#/crew/${encodeURIComponent(unit.id)}`}>
                  <b>{unit.id}</b>
                  <span>{unit.kind === "ALS" ? "Advanced" : "Basic"} · {unit.station}</span>
                </a>
              ))}
              <a className="nl-unit demo" href="#/crew/1">
                <b>Ambulance 1</b>
                <span>Demo and test trips (no 108 unit)</span>
              </a>
            </div>
            {units.length === 0 && (
              <span className="field-hint">Loading the 108 ambulances… (is the backend running?)</span>
            )}
          </div>

          <a className="nl-role-card" href="#/admin">
            <strong>Admin</strong>
            <span>Dashboard, complaints, past trips and police performance.</span>
            <span className="nl-role-go">Open admin</span>
          </a>
        </div>
      </main>
    </div>
  );
}

export default RolePicker;
