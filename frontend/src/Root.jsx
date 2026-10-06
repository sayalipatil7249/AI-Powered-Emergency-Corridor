import { useEffect, useState } from "react";

import App from "./App.jsx";
import AdminDashboard from "./admin/AdminDashboard.jsx";
import AdminPage from "./admin/AdminPage.jsx";
import TripDetail from "./admin/TripDetail.jsx";

// Pages: the live map (#/); the admin dashboard, complaints and trips
// (#/admin); the live control room (#/admin/live); one trip's details
// (#/admin/trip/<request id>).
function Root() {
  const [hash, setHash] = useState(window.location.hash);

  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  if (hash.startsWith("#/admin/trip/")) {
    const requestId = decodeURIComponent(hash.slice("#/admin/trip/".length));
    return <TripDetail key={requestId} requestId={requestId} />;
  }
  // The live control room (#/control-room: its old address).
  if (hash.startsWith("#/admin/live") || hash.startsWith("#/control-room")) {
    return <AdminPage />;
  }
  // Dashboard, complaints and trips (#/admin/trips: the old address).
  if (hash.startsWith("#/admin")) {
    return <AdminDashboard />;
  }
  return <App />;
}

export default Root;
