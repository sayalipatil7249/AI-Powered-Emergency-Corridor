import { useEffect, useState } from "react";

import { adminApi, formatDateTime, label } from "./adminApi";

// Who raised a complaint, in plain words.
const ROLE_NAMES = {
  DRIVER: "Ambulance crew",
  CALL_CENTRE: "108 control room",
  HOSPITAL: "Hospital staff",
  POLICE: "Police",
  SYSTEM: "Automatic",
  USER: "Public",
};

const STATUS_TONES = {
  OPEN: "critical",
  IN_PROGRESS: "warning",
  RESOLVED: "good",
  CLOSED: "neutral",
};

function Select({ value, options, onChange, all, ...props }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} {...props}>
      {all && <option value="">{all}</option>}
      {options.map((option) => (
        <option key={option} value={option}>
          {label(option)}
        </option>
      ))}
    </select>
  );
}

// One ticket's row, with an editable status and resolution note.
function TicketRow({ ticket, statuses, onSaved }) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState(ticket.resolution_note || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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

  return (
    <>
      <tr>
        <td>
          <button className="admin-link-button" onClick={() => setOpen(!open)} aria-expanded={open}>
            {ticket.ticket_no}
          </button>
          <span className="muted">{formatDateTime(ticket.created_at)}</span>
        </td>
        <td>
          <strong>{ticket.subject}</strong>
          {ticket.request_id && (
            <a
              className="ticket-trip"
              href={`#/admin/trip/${encodeURIComponent(ticket.request_id)}`}
              title="Open this trip: map and timeline"
            >
              <span className="ticket-trip-id">Trip {ticket.request_id}</span>
              {ticket.trip && (
                <span>
                  {ticket.trip.start_name || "--"} → {ticket.trip.hospital_name || "--"}
                  {ticket.trip.ambulance_id &&
                    ` · ${ticket.trip.ambulance_id.replace("ambulance_0", "Ambulance ").replace("ambulance_", "Ambulance ")}`}
                </span>
              )}
            </a>
          )}
        </td>
        <td>
          {ROLE_NAMES[ticket.raised_by_role] || label(ticket.raised_by_role)}
          {ticket.raised_by_name && <span className="muted">{ticket.raised_by_name}</span>}
        </td>
        <td>{label(ticket.category)}</td>
        <td>
          <span className={`admin-priority priority-${ticket.priority.toLowerCase()}`}>
            {label(ticket.priority)}
          </span>
        </td>
        <td>
          <span className={`admin-status tone-${STATUS_TONES[ticket.status]}`}>
            {label(ticket.status)}
          </span>
          <Select
            value={ticket.status}
            options={statuses}
            onChange={(status) => save({ status })}
            disabled={busy}
            aria-label={`Status of ${ticket.ticket_no}`}
          />
        </td>
      </tr>
      {open && (
        <tr className="admin-ticket-detail">
          <td colSpan={6}>
            <p>{ticket.description || <span className="muted">No description.</span>}</p>
            <label>
              Resolution note
              <textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} />
            </label>
            <div className="admin-form-actions">
              {ticket.resolved_at && (
                <span className="muted">Resolved {formatDateTime(ticket.resolved_at)}</span>
              )}
              {error && <span className="field-error">{error}</span>}
              <button
                className="button button-secondary"
                disabled={busy}
                onClick={() => save({ resolution_note: note })}
              >
                Save note
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function GrievancePanel({ options, onChanged }) {
  const [filters, setFilters] = useState({ status: "", category: "", priority: "", search: "" });
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  // New complaints arrive from the main screen and the app itself:
  // check for them every 15 s.
  useEffect(() => {
    const timer = setInterval(() => setReload((value) => value + 1), 15000);
    return () => clearInterval(timer);
  }, []);

  // Reload when a filter changes (search waits until typing pauses).
  useEffect(() => {
    let current = true;
    const timer = setTimeout(() => {
      adminApi
        .grievances(filters)
        .then((list) => current && (setTickets(list), setError("")))
        .catch((problem) => current && setError(problem.message))
        .finally(() => current && setLoading(false));
    }, filters.search ? 300 : 0);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [filters, reload]);

  const setFilter = (key) => (value) => setFilters({ ...filters, [key]: value });

  const saved = (updated) => {
    setTickets((list) => list.map((item) => (item.id === updated.id ? updated : item)));
    onChanged();
  };

  return (
    <section className="admin-card">
      <div className="admin-card-heading">
        <div>
          <h2>Complaints</h2>
          <p className="muted">
            Raised by ambulance crews (and the 108 control room, hospitals,
            police) with &ldquo;Report a problem&rdquo;, or filed automatically
            when something goes wrong · set the status and add a note when
            solved
          </p>
        </div>
      </div>

      <div className="admin-filters" role="group" aria-label="Filter tickets">
        <input
          type="search"
          placeholder="Search ticket, subject, name, request…"
          value={filters.search}
          onChange={(event) => setFilter("search")(event.target.value)}
        />
        <Select value={filters.status} options={options.grievance_statuses} onChange={setFilter("status")} all="All statuses" />
        <Select value={filters.category} options={options.grievance_categories} onChange={setFilter("category")} all="All categories" />
        <Select value={filters.priority} options={options.grievance_priorities} onChange={setFilter("priority")} all="All priorities" />
      </div>

      {error && <p className="field-error">{error}</p>}
      {loading ? (
        <p className="admin-empty">Loading tickets…</p>
      ) : tickets.length === 0 ? (
        <p className="admin-empty">No tickets match these filters.</p>
      ) : (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Ticket</th>
                <th>Subject</th>
                <th>Raised by</th>
                <th>Category</th>
                <th>Priority</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((ticket) => (
                <TicketRow
                  key={`${ticket.id}-${ticket.updated_at}`}
                  ticket={ticket}
                  statuses={options.grievance_statuses}
                  onSaved={saved}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default GrievancePanel;
