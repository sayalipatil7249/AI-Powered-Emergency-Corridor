import { useEffect, useState } from "react";

import App from "./App.jsx";
import AdminDashboard from "./admin/AdminDashboard.jsx";
import AdminPage from "./admin/AdminPage.jsx";
import TripDetail from "./admin/TripDetail.jsx";
import RolePicker from "./RolePicker.jsx";

// Pages: "who is using this screen?" (#/), the 108 control room
// (#/control), one ambulance's crew screen (#/crew/<unit>; #/new/...
// are their old addresses); the admin dashboard, complaints and trips
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
  // One App for both, so the control room's planned trip survives
  // switching role in the same tab.
  const page = hash.replace(/^#\/new(?=\/)/, "#");
  if (page.startsWith("#/control")) {
    return <App tab="control" />;
  }
  if (page.startsWith("#/crew/")) {
    const unit = decodeURIComponent(page.slice("#/crew/".length));
    return <App tab="crew" unit={unit} />;
  }
  return <RolePicker />;
}

export default Root;
