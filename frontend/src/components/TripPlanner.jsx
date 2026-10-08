import { useEffect, useRef, useState } from "react";

import Collapsible from "./Collapsible";
import ConditionSelect from "./ConditionSelect";
import HospitalPicker from "./HospitalPicker";
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
  conditions,
  condition,
  onConditionChange,
  booked = [],
  fromBase = true,
  onFromBaseChange,
}) {
  // The AI's trip estimate for the current live traffic level.
  const estimate = plan?.estimated_minutes?.[liveLevel] ?? null;
  const liveLevelName = { light: "light", normal: "normal", heavy: "heavy" }[
    liveLevel
  ];

  // What the user is typing; null shows the chosen start's name.
  const [draft, setDraft] = useState(null);
  const [results, setResults] = useState({ query: "", places: [] });
  const [searchError, setSearchError] = useState("");

  const typed = (draft ?? "").trim();

  // Hospitals that can treat this patient (nearest first once the
  // patient's location is known), and the dispatcher's calls to them.
  const [suitable, setSuitable] = useState(null);
  const [calls, setCalls] = useState(null);
  const [calling, setCalling] = useState(false);
  // 108's default is a government hospital (free treatment); the
  // family can ask for a private one.
  const [governmentOnly, setGovernmentOnly] = useState(false);
  const bookedKey = booked.join("|");
  // A hospital the user picked by hand for this patient: kept until the
  // patient, condition or preference changes (automatic calls don't
  // replace it; "Call hospitals again" does).
  const patientKey = start
    ? `${start.latitude},${start.longitude}|${condition}|${governmentOnly}`
    : null;
  const handPicked = useRef(null);

  useEffect(() => {
    if (disabled || !condition) return undefined;
    const controller = new AbortController();
    const params = new URLSearchParams({ condition });
    if (governmentOnly) params.set("government_only", "true");
    if (start) {
      params.set("latitude", start.latitude);
      params.set("longitude", start.longitude);
    }
    fetch(`${apiUrl}/plan/hospitals?${params}`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : null))
      .then((list) => list && setSuitable(list))
      .catch(() => {});
    return () => controller.abort();
  }, [apiUrl, condition, start, disabled, governmentOnly]);

  const callHospitals = async ({ automatic = false } = {}) => {
    if (!start || !condition) return;
    if (automatic && handPicked.current === patientKey) return;
    handPicked.current = null;
    try {
      setCalling(true);
      const response = await fetch(`${apiUrl}/plan/call-hospitals`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude: start.latitude,
          longitude: start.longitude,
          condition,
          booked: bookedKey ? bookedKey.split("|") : [],
          government_only: governmentOnly,
        }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Call failed");
      setCalls(result.calls);
      if (result.hospital) {
        onHospitalChange(
          hospitals.find((item) => item.name === result.hospital.name) || result.hospital
        );
      }
    } catch {
      setCalls([]);
    } finally {
      setCalling(false);
    }
  };

  // A dispatcher calls as soon as the patient's location and condition
  // are known.
  useEffect(() => {
    // The demo trip keeps its own hospital (Ruby Hall Clinic).
    if (disabled || !start || !condition || start.demo || start.keepHospital) return undefined;
    // Shortly after the last change, so quick changes make one call.
    const timer = setTimeout(() => callHospitals({ automatic: true }), 300);
    return () => clearTimeout(timer);
    // Only when the patient, condition, bookings or preference change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start, condition, bookedKey, disabled, governmentOnly]);

  const choices = suitable || hospitals;
  const chosenListed = choices.some((item) => item.name === hospital?.name);

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

      <label className="field-label" htmlFor="trip-start">From (patient&apos;s location)</label>
      <div className="field-row">
        <div className="search-box">
          <input
            id="trip-start"
            type="text"
            placeholder="Type a place in Pune"
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
          title="Click the map to choose where the patient is"
        >
          {pickMode ? "Click map…" : "Pick on map"}
        </button>
      </div>
      {searchError && <p className="field-error">{searchError}</p>}
      {!searchError && typed.length >= 3 && results.query === typed &&
        results.places.length === 0 && (
          <p className="field-hint">No places found. Try another name, or Pick on map.</p>
        )}
      {pickMode && (
        <p className="field-hint">
          Click a place inside the marked area of the map.
        </p>
      )}

      {onFromBaseChange && (
        <label className="from-base-toggle">
          <input
            type="checkbox"
            checked={fromBase}
            disabled={disabled}
            onChange={(event) => onFromBaseChange(event.target.checked)}
          />
          <span>
            Send the nearest free 108 ambulance to the patient
            <span className="muted"> (untick if the patient is already in the ambulance)</span>
          </span>
        </label>
      )}

      <label className="field-label" htmlFor="trip-condition">
        What happened to the patient?
      </label>
      <ConditionSelect
        id="trip-condition"
        conditions={conditions}
        value={condition}
        disabled={disabled}
        onChange={onConditionChange}
      />

      <div className="field-row hospital-heading">
        <label className="field-label" htmlFor="trip-hospital">
          Hospital{suitable ? ` · ${suitable.length} options` : ""}
        </label>
        <label className="inline-check">
          <input
            type="checkbox"
            checked={governmentOnly}
            disabled={disabled}
            onChange={(event) => setGovernmentOnly(event.target.checked)}
          />
          Govt only
        </label>
      </div>
      <HospitalPicker
        id="trip-hospital"
        choices={choices}
        hospital={hospital}
        condition={condition}
        canTreat={!hospital || chosenListed}
        disabled={disabled}
        onChange={(item) => {
          handPicked.current = patientKey;
          // The calls were about another hospital.
          setCalls(null);
          onHospitalChange(hospitals.find((entry) => entry.name === item.name) || item);
        }}
      />

      {start && (
        <div className="hospital-calls">
          <button
            className="link-button"
            disabled={disabled || calling}
            onClick={() => callHospitals()}
          >
            {calling ? "Calling hospitals…" : "📞 Call hospitals again"}
          </button>
          {calls?.length > 0 && (
            <ul>
              {calls.map((call) => (
                <li key={call.name} className={`call-${call.answer}`}>
                  {call.name} ({call.drive_minutes < 1 ? "<1" : call.drive_minutes} min):{" "}
                  {call.answer === "accepted"
                    ? `✓ yes, ${call.need_label.toLowerCase()} ready`
                    : `✗ no, ${call.need_label.toLowerCase()} busy`}
                </li>
              ))}
            </ul>
          )}
          {calls?.length === 0 && !calling && (
            <span className="field-error">No hospital nearby can take this patient right now.</span>
          )}
        </div>
      )}

      <button
        className="button button-primary find-button"
        disabled={disabled || planning || !start || !hospital}
        onClick={() => onFindRoute()}
      >
        {planning ? "Finding route…" : "Find route"}
      </button>

      {planError && <p className="field-error">{planError}</p>}

      {plan && !planError && (
        <div className="plan-summary">
          {plan.base && (
            <span className="plan-journey">
              🚑 Ambulance {plan.unit ? `${plan.unit.id} (${plan.unit.kind === "ALS" ? "advanced" : "basic"})` : ""}
              {plan.unit ? ` from ${plan.unit.station}` : ` from ${plan.base.name}`}: reaches the patient in{" "}
              {plan.minutes_to_patient < 1 ? "under 1" : plan.minutes_to_patient} min, then{" "}
              {plan.minutes_to_hospital} min to the hospital
            </span>
          )}
          {plan.dispatch?.length > 0 && (
            <Collapsible title="Why this ambulance?">
              <ul className="dispatch-list">
                {plan.dispatch.map((row) => (
                  <li key={row.id} className={row.chosen ? "chosen" : ""}>
                    <strong>{row.id}</strong> {row.kind === "ALS" ? "advanced" : "basic"} · {row.station}
                    {row.minutes != null ? ` · ${row.minutes < 1 ? "<1" : row.minutes} min` : ""}
                    {" · "}{row.status}
                  </li>
                ))}
              </ul>
            </Collapsible>
          )}
          <strong>
            {formatDistance(plan.length_meters)} · {plan.signals.length}{" "}
            {plan.signals.length === 1 ? "signal" : "signals"}
            {estimate
              ? ` · about ${Math.round(estimate)} min`
              : ` · about ${plan.minutes_without_traffic} min with no traffic`}
          </strong>
          <Collapsible title="More about this route">
          {estimate && (
            <span>
              Time guessed for {liveLevelName} traffic now. Light traffic:{" "}
              {Math.round(plan.estimated_minutes.light)} min, heavy traffic:{" "}
              {Math.round(plan.estimated_minutes.heavy)} min.
            </span>
          )}
          {plan.streets.length > 0 && (
            <span>via {plan.streets.join(", ")}</span>
          )}
          {plan.start_snap_meters > 60 && (
            <span>
              Start moved {plan.start_snap_meters} m to the nearest road.
            </span>
          )}
          {plan.police_along_route?.length > 0 && (
            <div className="plan-police">
              <span>
                Some roads on the way have no signal (dashed on the map). If
                one is jammed, these police stations get a call:
              </span>
              <ul>
                {plan.police_along_route.map((station) => (
                  <li key={station.name}>
                    <strong>{station.name}</strong>
                    {station.phone ? ` · 📞 ${station.phone}` : ""}
                    {" · "}
                    {station.covers
                      .map(
                        (cover) =>
                          `${cover.road} (${Math.max(1, Math.round(cover.drive_seconds / 60))} min away)`
                      )
                      .join(", ")}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {plan.shorter_alternative && (
            <span>
              There is a shorter road (
              {formatDistance(plan.shorter_alternative.length_meters)}), but
              it is slower.
            </span>
          )}
          </Collapsible>
        </div>
      )}
    </section>
  );
}

export default TripPlanner;
