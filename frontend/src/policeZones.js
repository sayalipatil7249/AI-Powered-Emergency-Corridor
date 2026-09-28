import { formatDuration } from "./routeStatus";

// Roads the police are managing now, and roads they have cleared
// (shared by the map and the 3D chase view).
export const POLICE_ACTIVE_COLOR = "#3b82f6";
export const POLICE_CLEARED_COLOR = "#14b8a6";

// Police alerts still being handled (corridor/police_watch.py).
export const ACTIVE_ALERTS = ["ALERTED", "EN_ROUTE", "ON_SCENE"];

const percent = (share) => `${Math.round(share * 100)}%`;

// Every road police are managing or have cleared, from the police
// alerts (roads without signals) and the AI deadlock response:
// [{key, active, zone, station, road, vehicles_waved, unit_latitude,
//   unit_longitude, stopped_on_arrival, stopped_after, on_scene_seconds}].
export function policeZones(policeWatch, response) {
  const zones = [];
  const add = (key, active, item) => {
    if (item.zone?.length > 1) zones.push({ key, active, ...item });
  };

  for (const alert of policeWatch?.alerts || []) {
    if (alert.status === "ON_SCENE") add(`zone-${alert.alert_id}`, true, alert);
    else if (alert.stopped_after != null) add(`zone-${alert.alert_id}`, false, alert);
  }

  const police = response?.police;
  if (police?.stage === "clearing") {
    add("zone-response", true, {
      ...police,
      unit_latitude: police.latitude,
      unit_longitude: police.longitude,
    });
  } else if (police?.stage === "done" && police.stopped_after != null) {
    add("zone-response", false, police);
  }
  return zones;
}

// "stopped cars 74% → 0% · 25 vehicles waved through · 2 min 44 s on scene"
export function zoneSummary(zone) {
  const parts = [`${zone.vehicles_waved} vehicles waved through`];
  if (zone.stopped_on_arrival != null && zone.stopped_after != null) {
    parts.unshift(
      `stopped cars ${percent(zone.stopped_on_arrival)} → ${percent(zone.stopped_after)}`
    );
  }
  if (zone.on_scene_seconds != null) {
    parts.push(`${formatDuration(zone.on_scene_seconds)} on scene`);
  }
  return parts.join(" · ");
}

// Police car id -> "Police · <station>" (ids from the police watch and
// the deadlock response).
export function policeUnitLabels(policeWatch, response) {
  const labels = {};
  for (const alert of policeWatch?.alerts || []) {
    if (alert.unit_id) labels[alert.unit_id] = `Police · ${alert.station}`;
  }
  if (response?.police?.unit_id) {
    labels[response.police.unit_id] = `Police · ${response.police.station}`;
  }
  return labels;
}
