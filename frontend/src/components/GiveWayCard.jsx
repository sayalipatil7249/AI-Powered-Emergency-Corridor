// Drivers who made room for the siren on this trip
// (simulation/sumo/adapters.py SumoGiveWay): lane changes on wider roads,
// pulling over to the roadside on main roads when stuck in a queue.
function GiveWayCard({ giveWay }) {
  if (!giveWay) {
    return null;
  }

  const pushes = giveWay.junction_pushes || 0;
  const total = giveWay.lane_changes + giveWay.pull_overs + pushes;

  return (
    <section className="live-card" aria-label="Drivers making room">
      <div className="live-card-heading">Siren · drivers making room</div>
      <p>
        <strong>{total}</strong> {total === 1 ? "driver" : "drivers"} made
        room so far
      </p>
      <p className="muted">
        {giveWay.lane_changes} changed lane · {giveWay.pull_overs} pulled over
        {giveWay.pulled_over_now > 0 &&
          ` (${giveWay.pulled_over_now} at the roadside now)`}
        {pushes > 0 && ` · ${pushes} pushed out of a blocked junction`}
        {giveWay.did_not_react > 0 &&
          ` · ${giveWay.did_not_react} did not react`}
      </p>
    </section>
  );
}

export default GiveWayCard;
