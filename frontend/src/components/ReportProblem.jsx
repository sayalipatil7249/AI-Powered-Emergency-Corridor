import { useState } from "react";

// The app is used by ambulance crews; the 108 control room, hospitals
// and police they work with can report too.
const ROLES = [
  ["DRIVER", "Ambulance crew (medic / driver)"],
  ["CALL_CENTRE", "108 control room"],
  ["HOSPITAL", "Hospital staff"],
  ["POLICE", "Police"],
];

const CATEGORIES = [
  ["DELAY", "We were held up / delayed"],
  ["ROUTE", "Wrong or bad route"],
  ["HOSPITAL", "Hospital refused or wasn't ready"],
  ["POLICE", "Police didn't come or didn't help"],
  ["VEHICLE", "Problem with the ambulance"],
  ["APP", "Problem with this app"],
  ["OTHER", "Something else"],
];

// "Report a problem": the ambulance crew (or the 108 control room, a
// hospital, the police) raises a complaint; it goes to the admin, who
// handles it on the Admin page (POST /complaints).
function ReportProblem({ apiUrl, ambulances = [], requestId = "", onClose }) {
  const [form, setForm] = useState({
    raised_by_role: "DRIVER",
    raised_by_name: "",
    category: "DELAY",
    request_id: requestId,
    subject: "",
    description: "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(null);
  const set = (key) => (event) => setForm({ ...form, [key]: event.target.value });

  const trips = ambulances.filter((item) => item.request_id);

  const submit = async (event) => {
    event.preventDefault();
    try {
      setBusy(true);
      setError("");
      const response = await fetch(`${apiUrl}/complaints`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...form, request_id: form.request_id || null }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "Could not send it. Try again.");
      setSent(result.ticket_no);
    } catch (problem) {
      setError(problem.message === "Failed to fetch" ? "Can't reach the server." : problem.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="report-backdrop" onClick={onClose}>
      <section
        className="report-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Report a problem"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="report-heading">
          <h2>Report a problem</h2>
          <button type="button" className="ask-link" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {sent ? (
          <div className="report-sent">
            <p>
              Thank you. Your complaint <strong>{sent}</strong> was sent to the
              admin, who will look into it.
            </p>
            <button type="button" className="button button-primary" onClick={onClose}>
              Done
            </button>
          </div>
        ) : (
          <form className="report-form" onSubmit={submit}>
            <label>
              Who are you?
              <select value={form.raised_by_role} onChange={set("raised_by_role")}>
                {ROLES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
              </select>
            </label>
            <label>
              What went wrong?
              <select value={form.category} onChange={set("category")}>
                {CATEGORIES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
              </select>
            </label>
            <label>
              Which trip? <span className="muted">(optional)</span>
              <select value={form.request_id} onChange={set("request_id")}>
                <option value="">Not about one trip</option>
                {trips.map((item) => (
                  <option key={item.request_id} value={item.request_id}>
                    {item.label}: {item.start_name} → {item.hospital_name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              In one line
              <input
                value={form.subject}
                onChange={set("subject")}
                required
                maxLength={200}
                placeholder="e.g. Ambulance waited 5 min at Deccan junction"
              />
            </label>
            <label>
              More details <span className="muted">(optional)</span>
              <textarea rows={3} value={form.description} onChange={set("description")} />
            </label>
            <label>
              Your name <span className="muted">(optional)</span>
              <input value={form.raised_by_name} onChange={set("raised_by_name")} maxLength={120} />
            </label>
            {error && <p className="field-error">{error}</p>}
            <div className="report-actions">
              <button type="button" className="button button-secondary" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="button button-primary" disabled={busy || !form.subject.trim()}>
                {busy ? "Sending…" : "Send to admin"}
              </button>
            </div>
          </form>
        )}
      </section>
    </div>
  );
}

export default ReportProblem;
