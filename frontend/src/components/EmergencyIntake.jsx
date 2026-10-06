import { useState } from "react";

// "Describe the emergency": the operator types the call in their own
// words; the AI fills in the trip (place, condition, hospital) and says
// why. The operator checks it and presses Start; the AI never controls
// signals (POST /assistant/intake).
function EmergencyIntake({ apiUrl, disabled, onFill }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const submit = async (event) => {
    event.preventDefault();
    if (!text.trim() || busy) return;
    try {
      setBusy(true);
      setError("");
      const response = await fetch(`${apiUrl}/assistant/intake`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "The AI could not read this request.");
      setResult(data);
      onFill(data);
    } catch (problem) {
      setResult(null);
      setError(problem.message === "Failed to fetch" ? "Can't reach the server." : problem.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel-section intake">
      <div className="section-heading">
        <h2>Describe the emergency</h2>
        <span className="muted">AI fills in the trip</span>
      </div>
      <form onSubmit={submit} className="intake-form">
        <textarea
          rows={2}
          value={text}
          disabled={disabled || busy}
          maxLength={600}
          placeholder="e.g. Man collapsed holding his chest near Kasba Peth, family wants a government hospital"
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) submit(event);
          }}
        />
        <button
          type="submit"
          className="button button-primary"
          disabled={disabled || busy || !text.trim()}
        >
          {busy ? "Reading…" : "Fill in with AI"}
        </button>
      </form>
      {error && <p className="field-error">{error}</p>}
      {result && !error && (
        <div className="intake-result">
          <strong>{result.summary}</strong>
          <span>
            {result.level_name} · {result.condition_label} → {result.hospital.name}
            {result.from_base ? " · 108 ambulance sent to the patient" : " · patient already on board"}
          </span>
          <span className="muted">{result.reason}</span>
          {result.note && <span className="muted">{result.note}</span>}
          <span className="muted">Check the trip below, then press Start.</span>
        </div>
      )}
    </section>
  );
}

export default EmergencyIntake;
