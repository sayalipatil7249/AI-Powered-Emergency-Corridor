import { useState } from "react";

import Collapsible from "./Collapsible";
import ConditionSelect from "./ConditionSelect";
import { ambulanceColor, LEVEL_CLASS } from "../fleet";

// Each entry represents a separate crew; extra traffic is a demo control.
function BookingList({
  bookings,
  preview,
  previewBusy,
  previewError,
  onRetryPreview,
  disabled,
  onConditionChange,
  conditions,
  maxAmbulances = 10,
  canBookTrip,
  tripLabel,
  onBookTrip,
  onBookCrossing,
  onRemove,
  onViewRoute,
}) {
  const [crossingCondition, setCrossingCondition] = useState("stable");
  const full = bookings.length >= maxAmbulances;
  const describe = (booking) =>
    conditions.find((item) => item.condition === booking.condition);

  return (
    <section className="panel-section fleet bookings">
      <div className="section-heading">
        <h2>More than one ambulance</h2>
        <span className="muted">
          {bookings.length > 0
            ? `${bookings.length} added · they all leave together`
            : "Optional"}
        </span>
      </div>

      {bookings.length > 0 ? (
        <ul className="fleet-list">
          {bookings.map((booking, index) => {
            const condition = describe(booking);
            return (
              <li key={booking.id} className="fleet-item">
                <div className="fleet-item-top">
                  <span
                    className="fleet-dot"
                    style={{ background: ambulanceColor(index + 1) }}
                  />
                  <strong>Ambulance {index + 1}</strong>
                  {condition && (
                    <span className={`level-badge ${LEVEL_CLASS[condition.level]}`}>
                      {condition.level_name}
                    </span>
                  )}
                  <button
                    className="link-button booking-remove"
                    disabled={disabled}
                    onClick={() => onRemove(booking.id)}
                    title="Remove this ambulance"
                  >
                    Remove
                  </button>
                </div>
                <span className="fleet-route">{booking.label}</span>
                {preview?.ambulances[index] && (
                  <button type="button" className="button button-secondary"
                    onClick={() => onViewRoute(index)}>Show on map</button>
                )}
                <label className="fleet-condition"><span>Patient condition</span>
                  <ConditionSelect conditions={conditions} value={booking.condition}
                    disabled={disabled} onChange={(value) => onConditionChange(booking.id, value)} />
                </label>
                {preview?.ambulances[index] && (
                  <p className="field-hint">{preview.ambulances[index].routing_decision.explanation}</p>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="field-hint booking-hint">
          For one ambulance, just press Start. Add trips here to send more than one.
        </p>
      )}

      {previewBusy && <p className="field-hint" role="status">Checking the routes…</p>}
      {previewError && <div role="alert"><p className="field-error">{previewError}</p>
        <button className="button button-secondary" onClick={onRetryPreview}>Try again</button></div>}
      {preview && <p className="field-hint">{preview.shared_junctions.length} junctions where they meet (marked on the map).</p>}
      <div className="fleet-add">
        <button
          className="button button-secondary"
          disabled={disabled || full || !canBookTrip}
          onClick={onBookTrip}
          title={`Add ${tripLabel} as one more ambulance`}
        >
          + Add as Ambulance {bookings.length + 1}
        </button>

        <Collapsible title="Test tools" hint="ambulances meeting">
          <span className="field-label">
            An ambulance that meets Ambulance 1 at a signal
          </span>
          <ConditionSelect
            conditions={conditions}
            value={crossingCondition}
            disabled={disabled || full}
            onChange={setCrossingCondition}
          />
          <button
            className="button button-secondary"
            disabled={disabled || full || bookings.length === 0}
            onClick={() => onBookCrossing(crossingCondition)}
          >
            + Add it
          </button>
          {bookings.length === 0 && (
            <span className="muted">Add Ambulance 1 first.</span>
          )}
        </Collapsible>
        {full && (
          <span className="muted">At most {maxAmbulances} ambulances at once.</span>
        )}
      </div>
    </section>
  );
}

export default BookingList;
