// Drivers who pulled over for the siren (backend: vehicle.pulled_over).
// SUMO takes them off the lane but still reports a position on it, so
// the map and the chase view draw them beside the lane, at the kerb.

export const PULLED_OVER_COLOR = "#f472b6";

// Traffic keeps left in India: the kerb is to the left of the heading.
const KERB_OFFSET_METERS = 2.8;

// [latitude, longitude] moved to the kerb. heading: degrees clockwise
// from north (as SUMO reports it).
export function kerbPosition(latitude, longitude, heading) {
  const angle = ((heading ?? 0) * Math.PI) / 180;
  const east = -Math.cos(angle) * KERB_OFFSET_METERS;
  const north = Math.sin(angle) * KERB_OFFSET_METERS;
  return [
    latitude + north / 111320,
    longitude + east / (111320 * Math.cos((latitude * Math.PI) / 180)),
  ];
}
