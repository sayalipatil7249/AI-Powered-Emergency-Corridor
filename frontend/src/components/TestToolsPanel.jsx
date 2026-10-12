import { useState } from "react";

import ConditionSelect from "./ConditionSelect";

// New layout, Control room: the tools that only exist for the
// simulation (a real control room has none of them), in one place.
function TestToolsPanel({
  running,
  locked,
  conditions,
  bookings = 0,
  firstDriving = false,
  selectedLabel,
  selectedDriving = false,
  canAddPlanned = false,
  full = false,
  busy = false,
  incidentBusy = false,
  playbackSpeed,
  demos = [],
  onUseDemo,
  onBookCrossing,
  onAddCrossing,
  onAddPlanned,
  onSimulateIncident,
  onHospitalDeclines,
  onPlaybackSpeedChange,
}) {
  const [condition, setCondition] = useState("fracture");

  return (
    <section className="panel-section nl-test-tools">
      <div className="section-heading">
        <h2>Test tools</h2>
        <span className="muted">Only for the simulation</span>
      </div>

      {!locked && (
        <>
          <span className="field-label">Demo trips (fill in the trip, then press Start)</span>
          <div className="demo-list">
            {demos.map((demo) => (
              <button key={demo.id} type="button" className="demo-button" onClick={() => onUseDemo(demo)}>
                <strong>{demo.label}</strong>
                <span>{demo.note}</span>
              </button>
            ))}
          </div>
          <span className="field-label">An ambulance that meets Ambulance 1 at a signal</span>
          <div className="field-row">
            <ConditionSelect conditions={conditions} value={condition} onChange={setCondition} />
            <button
              type="button"
              className="button button-secondary"
              disabled={bookings === 0 || full}
              onClick={() => onBookCrossing(condition)}
            >
              Add it
            </button>
          </div>
          {bookings === 0 && (
            <span className="field-hint">Add Ambulance 1 above first.</span>
          )}
        </>
      )}

      {running && locked && (
        <>
          <button
            type="button"
            className="button incident-button"
            disabled={incidentBusy || !firstDriving}
            onClick={onSimulateIncident}
            title="Block a road ahead of Ambulance 1 and watch the police clear it"
          >
            {incidentBusy ? "Blocking a road…" : "Accident ahead of Ambulance 1"}
          </button>
          {selectedDriving && (
            <button
              type="button"
              className="button button-secondary"
              disabled={busy}
              onClick={onHospitalDeclines}
              title="Pretend the hospital is now full: the ambulance goes to another hospital"
            >
              {selectedLabel}: hospital is full
            </button>
          )}
          <span className="field-label">Send one more ambulance now</span>
          <ConditionSelect conditions={conditions} value={condition}
            disabled={busy || full} onChange={setCondition} />
          <div className="field-row">
            <button
              type="button"
              className="button button-secondary"
              disabled={busy || full || !firstDriving}
              onClick={() => onAddCrossing(condition)}
            >
              Meets Ambulance 1 at a signal
            </button>
            {canAddPlanned && (
              <button
                type="button"
                className="button button-secondary"
                disabled={busy || full}
                onClick={() => onAddPlanned(condition)}
              >
                On the planned trip
              </button>
            )}
          </div>
          {!firstDriving && <span className="field-hint">Works once Ambulance 1 is driving.</span>}
        </>
      )}

      <div className="playback nl-speed" role="group" aria-label="Speed"
        title="Play the simulation faster. The ambulance itself does not drive faster.">
        <span>Speed</span>
        {[1, 2, 5, 10].map((speed) => (
          <button
            key={speed}
            type="button"
            className={speed === playbackSpeed ? "active" : ""}
            onClick={() => onPlaybackSpeedChange(speed)}
          >
            {speed}×
          </button>
        ))}
      </div>
    </section>
  );
}

export default TestToolsPanel;
