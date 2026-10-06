import { useEffect, useState } from "react";

import { adminApi } from "./adminApi";
import { DelayChart, HospitalsChart, RecentTripsChart, TrafficChart } from "./Charts";
import GrievancePanel from "./GrievancePanel";
import JunctionReport from "./JunctionReport";
import KpiTiles from "./KpiTiles";
import { RequestsTable, RouteSummary } from "./RoutePerformance";
import AdminHeader from "./AdminHeader";
import StuckSpotsMap from "./StuckSpotsMap";
import AdminPage from "./AdminPage";
import AdminSidebar, { SectionHeading } from "./AdminSidebar";
import { useAdminSection } from "./adminSections";
import "./admin.css";

// Period filter for every chart and figure: last N days, or all.
const PERIODS = [
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: null, label: "All time" },
];

// Everything the dashboard shows for one period, in one go.
async function loadDashboard(days) {
  const [kpis, delays, trend, route, requests, junctions, stuckSpots] = await Promise.all([
    adminApi.kpis(days),
    adminApi.delays(days),
    adminApi.trend(days),
    adminApi.routePerformance(days),
    adminApi.requests({ days, limit: 100 }),
    adminApi.junctions(days),
    adminApi.stuckSpots(days).catch(() => []),
  ]);
  return { kpis, delays, trend, route, requests, junctions, stuckSpots };
}

// Grievance and performance tracking: every ambulance request the live
// simulation recorded (backend/services/admin_service.py) and the
// grievance tickets. Opened at #/admin.
function AdminDashboard() {
  const section = useAdminSection("#/admin");
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
      <AdminHeader>
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
      </AdminHeader>

      <div className="admin-shell">
      <AdminSidebar />
      <main className="admin-content">
        <SectionHeading section={section} />
        {error && <p className="admin-error">{error}</p>}

        {section === "dashboard" && (
          <>
            <h2 className="admin-subheading">Ambulances now</h2>
            <AdminPage embedded />
          </>
        )}

        {section === "complaints" ? (
          options && <GrievancePanel options={options} onChanged={refresh} />
        ) : !data ? (
          !error && <p className="admin-empty">Loading…</p>
        ) : (
          <>
            {/* The dashboard: every number and chart, on one page. */}
            {section === "dashboard" && (
              <>
                <KpiTiles kpis={data.kpis} />
                <div className="admin-grid">
                  <DelayChart delays={data.delays} />
                  <RouteSummary summary={data.route} />
                  <TrafficChart requests={data.requests} />
                  <HospitalsChart requests={data.requests} />
                  <div className="admin-grid-wide">
                    <RecentTripsChart requests={data.requests} />
                  </div>
                </div>
              </>
            )}

            {section === "trips" && (
              <>
                <RequestsTable
                  requests={data.requests}
                  delayReasons={options?.delay_reasons || []}
                  onChangeReason={changeReason}
                  busyId={busyId}
                />
                <StuckSpotsMap spots={data.stuckSpots} />
                <JunctionReport junctions={data.junctions} />
              </>
            )}
          </>
        )}
      </main>
      </div>
    </div>
  );
}

export default AdminDashboard;
