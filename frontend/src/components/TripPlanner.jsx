import { useEffect, useState } from "react";

import { formatDistance } from "../routeStatus";

const SEARCH_DELAY_MS = 450;

// Choose where the ambulance starts and which hospital it goes to, and
// find the fastest route (backend /plan endpoints).
// App remounts this (key) when the start changes, which resets the box.
function TripPlanner({
  apiUrl,
  hospitals,
  start,
  onStartChange,
  hospital,
  onHospitalChange,
  pickMode,
  onPickModeChange,
  plan,
  planning,
  planError,
  onFindRoute,
  onUseDemo,
  disabled,
  liveLevel = "normal",
}) {
  // The AI's trip estimate for the current live traffic level.
  const estimate = plan?.estimated_minutes?.[liveLevel] ?? null;
  const liveLevelName = { light: "light", normal: "moderate", heavy: "heavy" }[
    liveLevel
  ];

  // What the user is typing; null shows the chosen start's name.
  const [draft, setDraft] = useState(null);
  const [results, setResults] = useState({ query: "", places: [] });
  const [searchError, setSearchError] = useState("");

  const typed = (draft ?? "").trim();

  // Search places as the user types (debounced).
  useEffect(() => {
    const text = (draft ?? "").trim();

    if (text.length < 3) {
      return undefined;
    }

    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(
          `${apiUrl}/plan/search?q=${encodeURIComponent(text)}`,
          { signal: controller.signal }
        );
        if (!response.ok) throw new Error("search failed");
        setResults({ query: text, places: await response.json() });
        setSearchError("");
      } catch (error) {
        if (error.name !== "AbortError") {
          setSearchError("Place search is unavailable right now.");
        }
      }
    }, SEARCH_DELAY_MS);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [apiUrl, draft]);

  // Only show results for what is currently typed.
  const suggestions =
    typed.length >= 3 && results.query === typed ? results.places : [];

  const choose = (place) => {
    setDraft(null);
    onStartChange(place);
  };

  return (
    <section className="panel-section planner">
      <div className="section-heading">
        <h2>Plan a trip</h2>
        {!disabled && (
          <button className="link-button" onClick={onUseDemo}>
            Demo route
          </button>
        )}
      </div>

      <label className="field-label" htmlFor="trip-start">From</label>
      <div className="field-row">
        <div className="search-box">
          <input
            id="trip-start"
            type="text"
            placeholder="Search a place in central Pune"
            value={draft ?? (start?.name || "")}
            disabled={disabled}
            autoComplete="off"
            onChange={(event) => setDraft(event.target.value)}
          />
          {suggestions.length > 0 && (
            <ul className="suggestions">
              {suggestions.map((place) => (
                <li key={`${place.name}-${place.latitude}`}>
                  <button onClick={() => choose(place)}>{place.name}</button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <button
          className={`button button-secondary pick-button ${
            pickMode ? "active" : ""
          }`}
          disabled={disabled}
          onClick={() => onPickModeChange(!pickMode)}
          title="Click on the map to set the start point"
        >
          {pickMode ? "Click map…" : "Pick on map"}
        </button>
      </div>
      {searchError && <p className="field-error">{searchError}</p>}
      {pickMode && (
        <p className="field-hint">
          Click a point inside the highlighted area of the map.
        </p>
      )}

      <label className="field-label" htmlFor="trip-hospital">To</label>
      <select
        id="trip-hospital"
        value={hospital?.name || ""}
        disabled={disabled}
        onChange={(event) =>
          onHospitalChange(
            hospitals.find((item) => item.name === event.target.value)
          )
        }
      >
        {hospitals.map((item) => (
          <option key={item.name} value={item.name}>
            {item.name}
          </option>
        ))}
      </select>

      <button
        className="button button-primary find-button"
        disabled={disabled || planning || !start || !hospital}
        onClick={onFindRoute}
      >
        {planning ? "Finding route…" : "Find fastest route"}
      </button>

      {planError && <p className="field-error">{planError}</p>}

      {plan && !planError && (
        <div className="plan-summary">
          <strong>
            {formatDistance(plan.length_meters)} · {plan.signals.length}{" "}
            {plan.signals.length === 1 ? "signal" : "signals"}
            {estimate
              ? ` · about ${Math.round(estimate)} min`
              : ` · ~${plan.minutes_without_traffic} min on empty roads`}
          </strong>
          {estimate && (
            <span>
              AI estimate for an ambulance in {liveLevelName} traffic right
              now ({Math.round(plan.estimated_minutes.light)}–
              {Math.round(plan.estimated_minutes.heavy)} min from light to
              heavy traffic; {plan.minutes_without_traffic} min on empty
              roads).
            </span>
          )}
          {plan.streets.length > 0 && (
            <span>via {plan.streets.join(", ")}</span>
          )}
          {plan.start_snap_meters > 60 && (
            <span>
              Start moved {plan.start_snap_meters} m to the nearest drivable
              road.
            </span>
          )}
          {plan.shorter_alternative && (
            <span>
              A shorter route exists (
              {formatDistance(plan.shorter_alternative.length_meters)}) but
              is slower for an ambulance.
            </span>
          )}
        </div>
      )}
    </section>
  );
}

export default TripPlanner;
