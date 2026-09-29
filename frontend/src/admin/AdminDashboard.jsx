import { useEffect, useState } from "react";

import SvgIcon from "../components/SvgIcon";
import { HOSPITAL_ICON } from "../icons";
import { adminApi } from "./adminApi";
import { DelayChart, RequestsTrendChart, ResponseTrendChart } from "./Charts";
import GrievancePanel from "./GrievancePanel";
import JunctionReport from "./JunctionReport";
import KpiTiles from "./KpiTiles";
import { RequestsTable, RouteSummary } from "./RoutePerformance";
import "./admin.css";

// Period filter for every chart and figure: last N days, or all.
const PERIODS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: null, label: "All time" },
];

// Everything the dashboard shows for one period, in one go.
async function loadDashboard(days) {
  const [kpis, delays, trend, route, requests, junctions] = await Promise.all([
    adminApi.kpis(days),
    adminApi.delays(days),
    adminApi.trend(days),
    adminApi.routePerformance(days),
    adminApi.requests({ days, limit: 100 }),
    adminApi.junctions(days),
  ]);
  return { kpis, delays, trend, route, requests, junctions };
}

// Grievance and performance tracking: every ambulance request the live
// simulation recorded (backend/services/admin_service.py) and the
// grievance tickets. Opened at #/admin.
function AdminDashboard() {
  const [days, setDays] = useState(30);
  const [options, setOptions] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [refreshed, setRefreshed] = useState(0);

  useEffect(() => {
    adminApi.options().then(setOptions).catch((problem) => setError(problem.message));
  }, []);

  // Reload on a new period, Refresh, or a change made on this page.
  useEffect(() => {
    let current = true;
    loadDashboard(days)
      .then((result) => {
        if (current) {
          setData(result);
          setError("");
        }
      })
      .catch((problem) => {
        if (current) setError(`${problem.message}. Is the backend running?`);
      });
    return () => {
      current = false;
    };
  }, [days, refreshed]);

  const refresh = () => setRefreshed((value) => value + 1);

  const changeReason = async (requestId, delayReason) => {
    try {
      setBusyId(requestId);
      await adminApi.updateRequest(requestId, { delay_reason: delayReason });
      refresh();
    } catch (problem) {
      setError(problem.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="admin-page">
      <header className="header">
        <div className="brand">
          <SvgIcon svg={HOSPITAL_ICON} className="brand-mark" />
          <div>
            <h1>Admin dashboard</h1>
            <p>Ambulance performance and grievances</p>
          </div>
        </div>
        <div className="header-actions">
          <div className="playback" role="group" aria-label="Period">
            {PERIODS.map((period) => (
              <button
                key={period.label}
                className={days === period.days ? "active" : ""}
                onClick={() => setDays(period.days)}
              >
                {period.label}
              </button>
            ))}
          </div>
          <button
            className="button button-secondary"
            onClick={refresh}
          >
            Refresh
          </button>
          <a className="button button-primary" href="#/">
            Live map
          </a>
        </div>
      </header>

      <main className="admin-content">
        {error && <p className="admin-error">{error}</p>}

        {!data ? (
          !error && <p className="admin-empty">Loading…</p>
        ) : (
          <>
            <KpiTiles kpis={data.kpis} />

            <div className="admin-grid">
              <DelayChart delays={data.delays} />
              <RouteSummary summary={data.route} />
              <RequestsTrendChart trend={data.trend} />
              <ResponseTrendChart trend={data.trend} />
            </div>

            <JunctionReport junctions={data.junctions} />

            <RequestsTable
              requests={data.requests}
              delayReasons={options?.delay_reasons || []}
              onChangeReason={changeReason}
              busyId={busyId}
            />
          </>
        )}

        {options && (
          <GrievancePanel
            options={options}
            onChanged={refresh}
          />
        )}
      </main>
    </div>
  );
}

export default AdminDashboard;
