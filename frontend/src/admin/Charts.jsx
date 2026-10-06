import { useState } from "react";
import { useTheme } from "../theme";
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDuration } from "../routeStatus";
import { label } from "./adminApi";

// One series per chart, so one hue (validated on the dashboard surface),
// and recessive axes and grid in the dashboard's muted ink.
const SERIES = "#b0475a";
const AXIS = { fill: "#5b6472", fontSize: 12 };

// Title, a chart / table switch, and an empty state.
function ChartCard({ title, sub, empty, table, children }) {
  const [asTable, setAsTable] = useState(false);

  return (
    <section className="admin-card">
      <div className="admin-card-heading">
        <div>
          <h2>{title}</h2>
          {sub && <p className="muted">{sub}</p>}
        </div>
        {!empty && (
          <button
            className="admin-link-button"
            onClick={() => setAsTable(!asTable)}
            aria-pressed={asTable}
          >
            {asTable ? "Chart" : "Table"}
          </button>
        )}
      </div>
      {empty ? (
        <p className="admin-empty">{empty}</p>
      ) : asTable ? (
        table
      ) : (
        children
      )}
    </section>
  );
}

function ChartTooltip({ active, payload, format }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return <div className="admin-tooltip">{format(item)}</div>;
}

// Late trips per delay reason: sorted bars, labelled at the bar end.
export function DelayChart({ delays = [] }) {
  const labelColor = useTheme() === "dark" ? "#e6e9ef" : "#111827";
  const rows = delays
    .filter((item) => item.count > 0)
    .map((item) => ({ ...item, name: label(item.reason) }))
    .sort((a, b) => b.count - a.count);

  return (
    <ChartCard
      title="Delay reasons"
      sub="Late trips (more than 1 min over plan) by cause"
      empty={rows.length === 0 && "No late trips in this period."}
      table={
        <table className="admin-table compact">
          <thead>
            <tr><th>Reason</th><th>Trips</th><th>Avg delay</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.reason}>
                <td>{row.name}</td>
                <td>{row.count}</td>
                <td>{formatDuration(row.avg_delay_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height={Math.max(140, rows.length * 44)}>
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 36 }}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis
            type="category"
            dataKey="name"
            width={110}
            tick={AXIS}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: "rgba(148, 163, 184, 0.08)" }}
            content={
              <ChartTooltip
                format={(item) => (
                  <>
                    <strong>{item.name}</strong>
                    <span>
                      {item.count} {item.count === 1 ? "trip" : "trips"} · avg{" "}
                      {formatDuration(item.avg_delay_seconds)} late
                    </span>
                  </>
                )}
              />
            }
          />
          <Bar dataKey="count" fill={SERIES} radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
            <LabelList dataKey="count" position="right" fill={labelColor} fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ---------------------------------------------------------------------
// Charts from the trip records (completed trips of the period)
// ---------------------------------------------------------------------

// How late a trip was: on time (within 1 min of the plan), a bit late
// (1-5 min) or very late (5+ min). Colours always come with a label.
const LATENESS = [
  { key: "on_time", label: "On time", color: "#16a34a" },
  { key: "late", label: "1–5 min late", color: "#ea7a1a" },
  { key: "very_late", label: "5+ min late", color: "#dc2626" },
];

function lateness(trip) {
  const delay = trip.delay_seconds || 0;
  if (delay <= 60) return LATENESS[0];
  if (delay <= 300) return LATENESS[1];
  return LATENESS[2];
}

const minutes = (seconds) => Math.round((seconds / 60) * 10) / 10;

function completed(requests) {
  return requests.filter((trip) => trip.status === "COMPLETED" && trip.response_seconds != null);
}

// "Kasba Peth, Budhwar Peth, Pune" -> "Kasba Peth"; long names shortened.
function shortName(name, length = 18) {
  if (!name) return "--";
  const first = name.split(",")[0].trim();
  return first.length > length ? `${first.slice(0, length - 1)}…` : first;
}

function Legend() {
  return (
    <ul className="admin-chart-legend">
      {LATENESS.map((item) => (
        <li key={item.key}>
          <span className="admin-swatch" style={{ background: item.color }} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

// The last trips: how long each took, coloured by how late it was.
export function RecentTripsChart({ requests = [] }) {
  const labelColor = useTheme() === "dark" ? "#e6e9ef" : "#111827";
  const rows = completed(requests)
    .slice(0, 10)
    .map((trip) => ({
      ...trip,
      name: `${shortName(trip.start_name, 16)} → ${shortName(trip.hospital_name, 18)}`,
      minutes: minutes(trip.response_seconds),
      tone: lateness(trip),
      text: `${minutes(trip.response_seconds)} min${
        trip.planned_seconds != null ? ` (expected ${minutes(trip.planned_seconds)})` : ""
      }`,
    }));

  return (
    <ChartCard
      title="Recent trips: on time or late?"
      sub="Time to hospital for the last 10 trips, newest at the top"
      empty={rows.length === 0 && "No completed trips in this period."}
      table={
        <table className="admin-table compact">
          <thead>
            <tr><th>Trip</th><th>Took</th><th>Expected</th><th>Result</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.request_id}>
                <td>{row.start_name} → {row.hospital_name}</td>
                <td>{formatDuration(row.response_seconds)}</td>
                <td>{formatDuration(row.planned_seconds)}</td>
                <td>{row.tone.label}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <Legend />
      <ResponsiveContainer width="100%" height={Math.max(160, rows.length * 34)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 150, left: 8, bottom: 4 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" tick={AXIS} axisLine={false} tickLine={false} width={300} />
          <Tooltip
            cursor={{ fill: "rgba(127, 127, 127, 0.08)" }}
            content={
              <ChartTooltip
                format={(item) => (
                  <>
                    <strong>{item.start_name} → {item.hospital_name}</strong>
                    <span>{item.text} · {item.tone.label}</span>
                  </>
                )}
              />
            }
          />
          <Bar dataKey="minutes" radius={[0, 6, 6, 0]} barSize={18} isAnimationActive={false}>
            {rows.map((row) => <Cell key={row.request_id} fill={row.tone.color} />)}
            <LabelList dataKey="text" position="right" fill={labelColor} fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

const TRAFFIC = [
  { key: "light", label: "Light traffic" },
  { key: "normal", label: "Normal traffic" },
  { key: "heavy", label: "Heavy traffic" },
];

// Does traffic slow ambulances down? Average time and on-time share
// per traffic level.
export function TrafficChart({ requests = [] }) {
  const labelColor = useTheme() === "dark" ? "#e6e9ef" : "#111827";
  const trips = completed(requests);
  const rows = TRAFFIC.map((level) => {
    const group = trips.filter((trip) => trip.traffic_level === level.key);
    if (group.length === 0) return null;
    const average = group.reduce((sum, trip) => sum + trip.response_seconds, 0) / group.length;
    const onTime = group.filter((trip) => lateness(trip).key === "on_time").length;
    return {
      ...level,
      trips: group.length,
      average,
      minutes: minutes(average),
      onTime: Math.round((onTime / group.length) * 100),
      text: `${minutes(average)} min · ${Math.round((onTime / group.length) * 100)}% on time`,
    };
  }).filter(Boolean);

  return (
    <ChartCard
      title="Traffic and time to hospital"
      sub="Average time to hospital in each traffic level, and how many arrived on time"
      empty={rows.length === 0 && "No completed trips in this period."}
      table={
        <table className="admin-table compact">
          <thead>
            <tr><th>Traffic</th><th>Trips</th><th>Average</th><th>On time</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td>{row.label}</td>
                <td>{row.trips}</td>
                <td>{formatDuration(row.average)}</td>
                <td>{row.onTime}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height={Math.max(130, rows.length * 46)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 150, left: 8, bottom: 4 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="label" tick={AXIS} axisLine={false} tickLine={false} width={110} />
          <Tooltip
            cursor={{ fill: "rgba(127, 127, 127, 0.08)" }}
            content={
              <ChartTooltip
                format={(item) => (
                  <>
                    <strong>{item.label}</strong>
                    <span>{item.trips} trips · {item.text}</span>
                  </>
                )}
              />
            }
          />
          <Bar dataKey="minutes" fill={SERIES} radius={[0, 6, 6, 0]} barSize={22} isAnimationActive={false}>
            <LabelList dataKey="text" position="right" fill={labelColor} fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// Which hospitals receive the most patients.
export function HospitalsChart({ requests = [] }) {
  const labelColor = useTheme() === "dark" ? "#e6e9ef" : "#111827";
  const counts = {};
  for (const trip of completed(requests)) {
    const name = trip.hospital_name || "Unknown";
    counts[name] = (counts[name] || 0) + 1;
  }
  const rows = Object.entries(counts)
    .map(([name, count]) => ({ name, short: shortName(name, 24), count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 7);

  return (
    <ChartCard
      title="Busiest hospitals"
      sub="Patients brought to each hospital"
      empty={rows.length === 0 && "No completed trips in this period."}
      table={
        <table className="admin-table compact">
          <thead>
            <tr><th>Hospital</th><th>Patients</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name}><td>{row.name}</td><td>{row.count}</td></tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height={Math.max(130, rows.length * 34)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 40, left: 8, bottom: 4 }}>
          <XAxis type="number" hide allowDecimals={false} />
          <YAxis type="category" dataKey="short" tick={AXIS} axisLine={false} tickLine={false} width={210} />
          <Tooltip
            cursor={{ fill: "rgba(127, 127, 127, 0.08)" }}
            content={
              <ChartTooltip
                format={(item) => (
                  <>
                    <strong>{item.name}</strong>
                    <span>{item.count} {item.count === 1 ? "patient" : "patients"}</span>
                  </>
                )}
              />
            }
          />
          <Bar dataKey="count" fill={SERIES} radius={[0, 6, 6, 0]} barSize={18} isAnimationActive={false}>
            <LabelList dataKey="count" position="right" fill={labelColor} fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
