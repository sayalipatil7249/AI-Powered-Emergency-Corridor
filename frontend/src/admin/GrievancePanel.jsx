import { useEffect, useState } from "react";

import { adminApi, formatDateTime } from "./adminApi";

// Who raised a complaint, in plain words.
const ROLE_NAMES = {
  DRIVER: "From the ambulance crew",
  CALL_CENTRE: "From the 108 control room",
  HOSPITAL: "From hospital staff",
  POLICE: "From the police",
  SYSTEM: "Filed automatically",
  USER: "From a member of the public",
};

// The database keeps four statuses; the page shows two.
const SOLVED = ["RESOLVED", "CLOSED"];
const isSolved = (ticket) => SOLVED.includes(ticket.status);

// Most urgent first, then newest.
const PRIORITY_ORDER = { HIGH: 0, MEDIUM: 1, LOW: 2 };

// "5 min ago", "3 hours ago", "yesterday", else the date.
function timeAgo(value) {
  const minutes = Math.round((Date.now() - new Date(value).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  if (hours < 48) return "yesterday";
  return formatDateTime(value);
}

// One complaint: what happened, who reported it, which trip, and one
// action (Mark solved, with an optional note; or Reopen).
function ComplaintCard({ ticket, onSaved }) {
  const [note, setNote] = useState(ticket.resolution_note || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const solved = isSolved(ticket);

  const save = async (changes) => {
    try {
      setBusy(true);
      setError("");
      onSaved(await adminApi.updateGrievance(ticket.id, changes));
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusy(false);
    }
  };

  const who = ROLE_NAMES[ticket.raised_by_role] || "Reported";
  const unit = ticket.trip?.ambulance_id
    ?.replace("ambulance_0", "Ambulance ")
    .replace("ambulance_", "Ambulance ");

  return (
    <li className={`complaint priority-${ticket.priority.toLowerCase()} ${solved ? "solved" : ""}`}>
      <div className="complaint-top">
        <span className="complaint-priority">{ticket.priority === "MEDIUM" ? "Medium" : ticket.priority === "HIGH" ? "High" : "Low"}</span>
        <strong className="complaint-subject">{ticket.subject}</strong>
        <span className="complaint-time" title={formatDateTime(ticket.created_at)}>
          {timeAgo(ticket.created_at)}
        </span>
      </div>

      <div className="complaint-meta">
        <span>
          {who}
          {ticket.raised_by_name && ticket.raised_by_role !== "SYSTEM" ? ` (${ticket.raised_by_name})` : ""}
        </span>
        {ticket.request_id && (
          <a
            className="complaint-trip"
            href={`#/admin/trip/${encodeURIComponent(ticket.request_id)}`}
            title="Open this trip: map and timeline"
          >
            {ticket.trip
              ? `${ticket.trip.start_name || "--"} → ${ticket.trip.hospital_name || "--"}${unit ? ` · ${unit}` : ""}`
              : "View the trip"}
          </a>
        )}
      </div>

      {ticket.description && <p className="complaint-text">{ticket.description}</p>}

      {solved ? (
        <div className="complaint-actions">
          <span className="complaint-solved">
            Solved{ticket.resolved_at ? ` ${timeAgo(ticket.resolved_at)}` : ""}
            {ticket.resolution_note ? `: ${ticket.resolution_note}` : ""}
          </span>
          <button className="button button-secondary" disabled={busy} onClick={() => save({ status: "OPEN" })}>
            Reopen
          </button>
        </div>
      ) : (
        <div className="complaint-actions">
          <input
            type="text"
            value={note}
            maxLength={500}
            placeholder="Add a note (optional): what was done"
            aria-label={`Note for ${ticket.subject}`}
            onChange={(event) => setNote(event.target.value)}
          />
          <button
            className="button button-primary"
            disabled={busy}
            onClick={() => save({ status: "RESOLVED", resolution_note: note })}
          >
            {busy ? "Saving…" : "Mark solved"}
          </button>
        </div>
      )}
      {error && <p className="field-error">{error}</p>}
    </li>
  );
}

// Complaints: Open / Solved tabs, a search box, and one card per
// complaint, most urgent and newest first.
function GrievancePanel({ onChanged }) {
  const [tab, setTab] = useState("open");
  const [search, setSearch] = useState("");
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  // New complaints arrive from the app and the crews: check every 15 s.
  useEffect(() => {
    const timer = setInterval(() => setReload((value) => value + 1), 15000);
    return () => clearInterval(timer);
  }, []);

  // Reload when the search changes (waits until typing pauses).
  useEffect(() => {
    let current = true;
    const timer = setTimeout(() => {
      adminApi
        .grievances({ search })
        .then((list) => current && (setTickets(list), setError("")))
        .catch((problem) => current && setError(problem.message))
        .finally(() => current && setLoading(false));
    }, search ? 300 : 0);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [search, reload]);

  const saved = (updated) => {
    setTickets((list) => list.map((item) => (item.id === updated.id ? updated : item)));
    onChanged();
  };

  const open = tickets.filter((ticket) => !isSolved(ticket));
  const solved = tickets.filter(isSolved);
  const shown = (tab === "open" ? open : solved).slice().sort((a, b) =>
    tab === "open"
      ? (PRIORITY_ORDER[a.priority] ?? 3) - (PRIORITY_ORDER[b.priority] ?? 3)
        || new Date(b.created_at) - new Date(a.created_at)
      : new Date(b.resolved_at || b.updated_at) - new Date(a.resolved_at || a.updated_at)
  );

  return (
    <section className="admin-card">
      <div className="complaints-heading">
        <div className="complaints-controls">
          <div className="complaints-tabs" role="tablist">
            <button role="tab" aria-selected={tab === "open"} className={tab === "open" ? "active" : ""} onClick={() => setTab("open")}>
              Open ({open.length})
            </button>
            <button role="tab" aria-selected={tab === "solved"} className={tab === "solved" ? "active" : ""} onClick={() => setTab("solved")}>
              Solved ({solved.length})
            </button>
          </div>
          <input
            type="search"
            placeholder="Search"
            aria-label="Search complaints"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
      </div>

      {error && <p className="field-error">{error}</p>}
      {loading ? (
        <p className="admin-empty">Loading complaints…</p>
      ) : shown.length === 0 ? (
        <p className="admin-empty">
          {search ? "No complaints match this search." : tab === "open" ? "No open complaints. All solved." : "Nothing solved yet."}
        </p>
      ) : (
        <ul className="complaint-list">
          {shown.map((ticket) => (
            <ComplaintCard key={`${ticket.id}-${ticket.updated_at}`} ticket={ticket} onSaved={saved} />
          ))}
        </ul>
      )}
    </section>
  );
}

export default GrievancePanel;
