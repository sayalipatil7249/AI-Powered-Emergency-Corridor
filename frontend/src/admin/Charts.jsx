import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDuration } from "../routeStatus";
import { label } from "./adminApi";

// One series per chart, so one hue (validated on the dashboard surface),
// and recessive axes and grid in the dashboard's muted ink.
const SERIES = "#3987e5";
const AXIS = { fill: "#8b95a7", fontSize: 12 };
const GRID = "rgba(148, 163, 184, 0.14)";

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
            <LabelList dataKey="count" position="right" fill="#e6e9ef" fontSize={12} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function shortDate(date) {
  return new Date(`${date}T00:00:00`).toLocaleDateString([], {
    day: "numeric",
    month: "short",
  });
}

// Requests per day (bars). Response time is a separate chart: two
// measures of different scale never share one plot.
export function RequestsTrendChart({ trend = [] }) {
  const rows = trend.map((item) => ({ ...item, day: shortDate(item.date) }));

  return (
    <ChartCard
      title="Requests per day"
      empty={rows.length === 0 && "No requests in this period."}
      table={
        <table className="admin-table compact">
          <thead>
            <tr><th>Day</th><th>Requests</th><th>Completed</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.date}>
                <td>{row.day}</td>
                <td>{row.requests}</td>
                <td>{row.completed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={rows} margin={{ top: 8, right: 8, left: -16 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="day" tick={AXIS} axisLine={false} tickLine={false} />
          <YAxis allowDecimals={false} tick={AXIS} axisLine={false} tickLine={false} />
          <Tooltip
            cursor={{ fill: "rgba(148, 163, 184, 0.08)" }}
            content={
              <ChartTooltip
                format={(item) => (
                  <>
                    <strong>{item.day}</strong>
                    <span>
                      {item.requests} requests · {item.completed} completed
                    </span>
                  </>
                )}
              />
            }
          />
          <Bar dataKey="requests" fill={SERIES} radius={[4, 4, 0, 0]} maxBarSize={32} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

export function ResponseTrendChart({ trend = [] }) {
  const rows = trend
    .filter((item) => item.avg_response_seconds != null)
    .map((item) => ({
      ...item,
      day: shortDate(item.date),
      minutes: Math.round((item.avg_response_seconds / 60) * 10) / 10,
    }));

  return (
    <ChartCard
      title="Average response time"
      sub="Completed trips, minutes"
      empty={rows.length === 0 && "No completed trips in this period."}
      table={
        <table className="admin-table compact">
          <thead>
            <tr><th>Day</th><th>Avg response</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.date}>
                <td>{row.day}</td>
                <td>{formatDuration(row.avg_response_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={rows} margin={{ top: 8, right: 16, left: -16 }}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="day" tick={AXIS} axisLine={false} tickLine={false} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} />
          <Tooltip
            cursor={{ stroke: "#8b95a7", strokeDasharray: "3 3" }}
            content={
              <ChartTooltip
                format={(item) => (
                  <>
                    <strong>{item.day}</strong>
                    <span>{formatDuration(item.avg_response_seconds)} on average</span>
                  </>
                )}
              />
            }
          />
          <Line
            type="monotone"
            dataKey="minutes"
            stroke={SERIES}
            strokeWidth={2}
            dot={{ r: 4, fill: SERIES, stroke: "#111827", strokeWidth: 2 }}
            activeDot={{ r: 6 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
