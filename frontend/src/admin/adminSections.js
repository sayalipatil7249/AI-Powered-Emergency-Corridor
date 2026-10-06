import { useEffect, useState } from "react";

// Every admin section, in sidebar order: the page it is on (#/admin:
// dashboard, complaints and trips; #/admin/live: the live control room)
// and its id. Admin opens on the dashboard.
export const GROUPS = [
  {
    title: "Main",
    page: "#/admin",
    items: [
      { section: "dashboard", label: "Dashboard", about: "Every ambulance right now, how fast they reach hospital, where the time goes, and what slows them down." },
      { section: "complaints", label: "Complaints", badge: true, about: "Problems reported by crews, or filed automatically. Set the status and add a note when solved." },
      { section: "trips", label: "Trips", about: "Every trip, newest first (click one for its map and timeline), and the junctions where ambulances lost the most time." },
    ],
  },
  {
    title: "More details",
    page: "#/admin/live",
    items: [
      { section: "who-first", label: "Who went first", about: "When two ambulances needed the same signal at the same time: who got the green, who waited, and why." },
      { section: "priority-log", label: "Priority changes", about: "Every time a crew or the control room set a patient's condition: from what, to what, and by whom." },
      { section: "police", label: "Police", about: "Police called to clear roads without signals, what happened, and the phone numbers the app calls." },
    ],
  },
];

// "#/admin/trips?s=complaints" -> { page: "#/admin/trips", section: "complaints" }
export function parse(hash) {
  const [address, query = ""] = hash.split("?");
  // Old addresses: #/admin/trips (reports) and #/control-room (live).
  const page = address === "#/admin/trips" ? "#/admin"
    : address === "#/control-room" ? "#/admin/live" : address;
  return { page, section: new URLSearchParams(query).get("s") };
}

// Sections on one page, in sidebar order.
export function pageSections(page) {
  return GROUPS.filter((group) => group.page === page).flatMap((group) => group.items);
}

// The section shown on an admin page (from "?s=" in the address),
// default the page's first section.
export function useAdminSection(page) {
  const read = () => {
    const current = parse(window.location.hash);
    const items = pageSections(page);
    const known = items.some((item) => item.section === current.section);
    return known ? current.section : items[0].section;
  };
  const [section, setSection] = useState(read);
  useEffect(() => {
    const update = () => {
      setSection(read());
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
    // read() only depends on page
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);
  return section;
}

