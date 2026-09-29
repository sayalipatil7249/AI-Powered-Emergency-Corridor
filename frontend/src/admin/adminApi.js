// The admin dashboard's backend calls (backend/api/routes/admin.py).
// Same backend address as the live map (App.jsx).
const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API_URL}/admin${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return body;
}

// "?a=1&b=x" from an object, skipping empty values.
function query(params) {
  const entries = Object.entries(params).filter(
    ([, value]) => value !== "" && value != null && value !== false
  );
  return entries.length ? `?${new URLSearchParams(entries)}` : "";
}

export const adminApi = {
  options: () => request("/options"),
  kpis: (days) => request(`/kpis${query({ days })}`),
  delays: (days) => request(`/delays${query({ days })}`),
  trend: (days) => request(`/trend${query({ days: days || 30 })}`),
  routePerformance: (days) => request(`/route-performance${query({ days })}`),
  requests: (filters) => request(`/requests${query(filters)}`),
  trip: (requestId) => request(`/requests/${encodeURIComponent(requestId)}`),
  junctions: (days) => request(`/junctions${query({ days, limit: 15 })}`),
  updateRequest: (requestId, changes) =>
    request(`/requests/${encodeURIComponent(requestId)}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),
  grievances: (filters) => request(`/grievances${query(filters)}`),
  createGrievance: (ticket) =>
    request("/grievances", { method: "POST", body: JSON.stringify(ticket) }),
  updateGrievance: (id, changes) =>
    request(`/grievances/${id}`, {
      method: "PATCH",
      body: JSON.stringify(changes),
    }),
};

// "IN_PROGRESS" -> "In progress"
export function label(value) {
  if (!value) return "--";
  const text = value.replaceAll("_", " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatPercent(share) {
  return share == null ? "--" : `${Math.round(share * 100)}%`;
}

export function formatDateTime(value) {
  if (!value) return "--";
  return new Date(value).toLocaleString([], {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
