import { useEffect, useState } from "react";

import { adminApi } from "./adminApi";
import { GROUPS, pageSections, parse } from "./adminSections";

// Title and one-line explanation of the section shown.
export function SectionHeading({ section, children }) {
  const item = GROUPS.flatMap((group) => group.items).find((entry) => entry.section === section);
  if (!item) return null;
  return (
    <div className="admin-section-heading">
      <div>
        <h2>{item.label}</h2>
        <p>{item.about}</p>
      </div>
      {children}
    </div>
  );
}

// Left sidebar on the admin pages: each item opens one section.
function AdminSidebar() {
  const [hash, setHash] = useState(window.location.hash);
  const [open, setOpen] = useState(null);
  const { page, section } = parse(hash);

  useEffect(() => {
    const update = () => setHash(window.location.hash);
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);

  // Open complaints, for the badge.
  useEffect(() => {
    let current = true;
    const load = () =>
      adminApi
        .grievanceSummary()
        .then((summary) => {
          if (!current) return;
          const counts = summary.by_status || {};
          setOpen((counts.OPEN || 0) + (counts.IN_PROGRESS || 0));
        })
        .catch(() => {});
    load();
    const timer = setInterval(load, 15000);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <nav className="admin-sidebar" aria-label="Admin sections">
      {GROUPS.map((group) => (
        <div key={group.title} className="admin-sidebar-group">
          <span className="admin-sidebar-title">{group.title}</span>
          {group.items.map((item) => {
            const href = `${group.page}?s=${item.section}`;
            const active =
              page === group.page &&
              (section === item.section ||
                (!section && pageSections(page)[0] === item));
            return (
              <a
                key={item.section}
                href={href}
                className={active ? "active" : ""}
                aria-current={active ? "page" : undefined}
              >
                <span>{item.label}</span>
                {item.badge && open > 0 && <span className="admin-sidebar-badge">{open}</span>}
              </a>
            );
          })}
        </div>
      ))}
      <div className="admin-sidebar-group">
        <a href="#/" className="admin-sidebar-back">← Live map</a>
      </div>
    </nav>
  );
}

export default AdminSidebar;
