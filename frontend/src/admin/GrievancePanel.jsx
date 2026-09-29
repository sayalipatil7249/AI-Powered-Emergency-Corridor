import { useEffect, useState } from "react";

import { adminApi, formatDateTime, label } from "./adminApi";

const EMPTY_TICKET = {
  raised_by_role: "USER",
  raised_by_name: "",
  category: "DELAY",
  priority: "MEDIUM",
  request_id: "",
  subject: "",
  description: "",
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

function NewTicketForm({ options, onCreated, onCancel }) {
  const [ticket, setTicket] = useState(EMPTY_TICKET);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (key) => (value) => setTicket({ ...ticket, [key]: value });

  const submit = async (event) => {
    event.preventDefault();
    try {
      setSaving(true);
      setError("");
      const created = await adminApi.createGrievance(ticket);
      setTicket(EMPTY_TICKET);
      onCreated(created);
    } catch (problem) {
      setError(problem.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="admin-form" onSubmit={submit}>
      <label>
        Raised by
        <Select value={ticket.raised_by_role} options={options.grievance_roles} onChange={set("raised_by_role")} />
      </label>
      <label>
        Name
        <input
          value={ticket.raised_by_name}
          onChange={(event) => set("raised_by_name")(event.target.value)}
          placeholder="Optional"
        />
      </label>
      <label>
        Category
        <Select value={ticket.category} options={options.grievance_categories} onChange={set("category")} />
      </label>
      <label>
        Priority
        <Select value={ticket.priority} options={options.grievance_priorities} onChange={set("priority")} />
      </label>
      <label>
        Request ID
        <input
          value={ticket.request_id}
          onChange={(event) => set("request_id")(event.target.value)}
          placeholder="e.g. REQ-20260929-101123"
        />
      </label>
      <label className="wide">
        Subject
        <input
          value={ticket.subject}
          onChange={(event) => set("subject")(event.target.value)}
          required
          maxLength={200}
        />
      </label>
      <label className="wide">
        Description
        <textarea
          rows={3}
          value={ticket.description}
          onChange={(event) => set("description")(event.target.value)}
        />
      </label>
      {error && <p className="field-error wide">{error}</p>}
      <div className="admin-form-actions wide">
        <button type="button" className="button button-secondary" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="button button-primary" disabled={saving}>
          {saving ? "Saving…" : "Raise ticket"}
        </button>
      </div>
    </form>
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
          {ticket.request_id && <span className="muted">{ticket.request_id}</span>}
        </td>
        <td>
          {label(ticket.raised_by_role)}
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
  const [creating, setCreating] = useState(false);
  const [reload, setReload] = useState(0);

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
          <h2>Grievance tickets</h2>
          <p className="muted">Complaints from users, drivers, hospitals and police</p>
        </div>
        {!creating && (
          <button className="button button-primary" onClick={() => setCreating(true)}>
            New ticket
          </button>
        )}
      </div>

      {creating && (
        <NewTicketForm
          options={options}
          onCancel={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            setReload((value) => value + 1);
            onChanged();
          }}
        />
      )}

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
