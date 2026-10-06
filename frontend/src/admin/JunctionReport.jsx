import { useState } from "react";
import { useTheme } from "../theme";
import {
  Bar,
  BarChart,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatDuration } from "../routeStatus";

// One measure (standing time), one hue - the dashboard's series colour.
const SERIES = "#b0475a";
const AXIS = { fill: "#5b6472", fontSize: 12 };
const CHART_ROWS = 8;

function shortName(name) {
  return name.length > 34 ? `${name.slice(0, 33)}…` : name;
}

// Junctions where ambulances stood still most, worst first: every stop
// of 10 s or more is put down to the junction the ambulance was waiting
// to get through.
function JunctionReport({ junctions = [] }) {
  const labelColor = useTheme() === "dark" ? "#e6e9ef" : "#111827";
  const [asTable, setAsTable] = useState(false);
  const rows = junctions.slice(0, CHART_ROWS).map((item) => ({
    ...item,
    label: shortName(item.name),
    minutes: Math.round((item.total_seconds / 60) * 10) / 10,
  }));

  return (
    <section className="admin-card">
      <div className="admin-card-heading">
        <div>
          <h2>Problem junctions</h2>
          <p className="muted">
            Where ambulances stood still longest (stops of 10 s or more), total
            minutes
          </p>
        </div>
        {junctions.length > 0 && (
          <button
            className="admin-link-button"
            onClick={() => setAsTable(!asTable)}
            aria-pressed={asTable}
          >
            {asTable ? "Chart" : "Table"}
          </button>
        )}
      </div>

      {junctions.length === 0 ? (
        <p className="admin-empty">
          No stops recorded yet. Trips record where the ambulance stood still
          from now on.
        </p>
      ) : asTable ? (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Junction</th>
                <th>Signal</th>
                <th>Stops</th>
                <th>Trips</th>
                <th>Total standing</th>
                <th>Average</th>
                <th>Longest</th>
              </tr>
            </thead>
            <tbody>
              {junctions.map((item, index) => (
                <tr key={item.junction_id}>
                  <td>{index + 1}</td>
                  <td>{item.name}</td>
                  <td>{item.signal ? "Signal" : "No signal"}</td>
                  <td>{item.stops}</td>
                  <td>{item.trips}</td>
                  <td>{formatDuration(item.total_seconds)}</td>
                  <td>{formatDuration(item.avg_seconds)}</td>
                  <td>{formatDuration(item.max_seconds)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={Math.max(140, rows.length * 44)}>
          <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 48 }}>
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="label"
              width={230}
              tick={AXIS}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(148, 163, 184, 0.08)" }}
              content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const item = payload[0].payload;
                return (
                  <div className="admin-tooltip">
                    <strong>{item.name}</strong>
                    <span>
                      {item.signal ? "Signal" : "No signal"} · {item.stops}{" "}
                      {item.stops === 1 ? "stop" : "stops"} on {item.trips}{" "}
                      {item.trips === 1 ? "trip" : "trips"}
                    </span>
                    <span>
                      {formatDuration(item.total_seconds)} in total · longest{" "}
                      {formatDuration(item.max_seconds)}
                    </span>
                  </div>
                );
              }}
            />
            <Bar
              dataKey="minutes"
              fill={SERIES}
              radius={[0, 4, 4, 0]}
              barSize={18}
              isAnimationActive={false}
            >
              <LabelList
                dataKey="minutes"
                position="right"
                fill={labelColor}
                fontSize={12}
                formatter={(value) => `${value} min`}
              />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </section>
  );
}

export default JunctionReport;
