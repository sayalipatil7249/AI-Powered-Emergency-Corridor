// Colours shared by the map and the chase view.

// Simulated traffic on the ambulance's route ahead.
export const ROUTE_TRAFFIC_COLORS = {
  free: "#22c55e",
  slow: "#f59e0b",
  jammed: "#ef4444",
};

// Other cars: red when stopped, amber when crawling.
export function carColor(speed) {
  if (speed < 0.5) return "#ef4444";
  if (speed < 3) return "#f59e0b";
  return "#94a3b8";
}
