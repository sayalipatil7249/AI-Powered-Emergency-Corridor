import { useState } from "react";

import { formatDistance } from "../routeStatus";

// Matches when every typed word starts a word of the name
// ("ruby", "sass hosp", "kem").
function matches(name, query) {
  const words = name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((part) => words.some((word) => word.startsWith(part)) || name.toLowerCase().includes(part));
}

function details(item, condition) {
  return [
    item.type_label,
    item.ownership === "government" ? "Govt" : null,
    item.distance_meters != null ? formatDistance(item.distance_meters) : null,
    item.free != null && condition ? (item.free ? "can take patient" : "full now") : null,
  ].filter(Boolean).join(" · ");
}

// Search box for the hospital: type part of a name, pick from the
// matching hospitals (nearest first). Full hospitals can't be picked.
function HospitalPicker({ id, choices, hospital, condition, canTreat, disabled, onChange }) {
  const [query, setQuery] = useState(null); // null: show the chosen name
  const [open, setOpen] = useState(false);

  const typed = (query ?? "").trim();
  const shown = typed ? choices.filter((item) => matches(item.name, typed)) : choices;

  const pick = (item) => {
    onChange(item);
    setQuery(null);
    setOpen(false);
  };

  return (
    <div className="search-box hospital-picker">
      <input
        id={id}
        type="text"
        autoComplete="off"
        disabled={disabled}
        placeholder="Search a hospital"
        value={query ?? (hospital?.name || "")}
        onFocus={(event) => {
          setOpen(true);
          event.target.select();
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setOpen(true);
        }}
        onBlur={() => {
          // After a click on an option has registered.
          setTimeout(() => {
            setOpen(false);
            setQuery(null);
          }, 150);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            const first = shown.find((item) => item.free !== 0);
            if (first) pick(first);
            event.preventDefault();
          } else if (event.key === "Escape") {
            setOpen(false);
            setQuery(null);
            event.currentTarget.blur();
          }
        }}
      />
      {hospital && !open && (
        <span className="hospital-picker-details">
          {canTreat ? details(hospital, condition) : "Can't treat this patient"}
        </span>
      )}
      {open && !disabled && (
        <ul className="suggestions hospital-options">
          {shown.length === 0 && <li className="muted no-match">No hospital found.</li>}
          {shown.map((item) => {
            const full = item.free === 0 && item.name !== hospital?.name;
            return (
              <li key={item.name}>
                <button
                  type="button"
                  disabled={full}
                  className={item.name === hospital?.name ? "chosen" : ""}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => pick(item)}
                >
                  <strong>{item.name}</strong>
                  <span>{details(item, condition)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

export default HospitalPicker;
