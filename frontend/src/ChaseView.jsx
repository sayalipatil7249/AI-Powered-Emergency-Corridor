import { useEffect, useRef, useState } from "react";
import { Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre draws tiles in a web worker; let Vite bundle it and tell
// MapLibre where it is (its own lookup breaks under Vite's bundling).
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

import { SIGNAL_STATUS, signalLabel } from "./routeStatus";
import { SIGNAL_ICON } from "./icons";
import { PULLED_OVER_COLOR, kerbPosition } from "./pulledOver";
import { ROUTE_TRAFFIC_COLORS, carColor } from "./trafficColors";
import {
  POLICE_ACTIVE_COLOR,
  POLICE_CLEARED_COLOR,
  policeUnitLabels,
  policeZones,
} from "./policeZones";

setWorkerUrl(maplibreWorkerUrl);

// Free vector map with building footprints and heights (no key).
// Light or dark map to match the page theme (MapView remounts this view
// when the theme changes).
const isDark = () => document.documentElement.dataset.theme === "dark";
const styleUrl = () =>
  `https://tiles.openfreemap.org/styles/${isDark() ? "dark" : "positron"}`;

const PUNE_CENTER = [73.859, 18.523]; // longitude, latitude

const STATUS_COLORS = {
  GREEN: "#22c55e",
  TURNING: "#84cc16",
  READY: "#f59e0b",
  WAITING: "#94a3b8",
  PASSED: "#cbd5e1",
};

// Camera behind the ambulance, looking ahead along the road.
const CAMERA_ZOOM = 19.3;
const CAMERA_PITCH = 66;

// Vehicle sizes in metres.
const CAR = { length: 4.4, width: 1.9, height: 1.5 };
const AMBULANCE = { length: 6.0, width: 2.3, height: 2.6 };

// Siren lights: red and blue, switching every SIREN_FLASH_MS.
const SIREN_COLORS = ["#ef4444", "#3b82f6"];
const SIREN_FLASH_MS = 450;
// A tall glowing pillar marks the hospital from far away.
const HOSPITAL_BEACON = { length: 10, width: 10, height: 45 };
// A shorter amber pillar marks the patient (108 trips with a pickup),
// so it is clear why the ambulance stops there.
const PATIENT_BEACON = { length: 4, width: 4, height: 16 };
const PATIENT_COLOR = "#f59e0b";

// Police: cars, station towers, accident beacons (metres).
const POLICE_CAR = { length: 4.6, width: 1.9, height: 1.6 };
const STATION_TOWER = { length: 7, width: 7, height: 28 };
const CRASH_BEACON = { length: 5, width: 5, height: 22 };
const CRASHED_CAR_COLOR = "#f97316";

// Police station tower colour by what the station is doing.
const STATION_COLORS = {
  available: "#1d4ed8",
  alerted: "#f59e0b",
  unit_en_route: "#3b82f6",
  on_scene: "#14b8a6",
};

const EMPTY = { type: "FeatureCollection", features: [] };

// A vehicle footprint: SUMO reports the FRONT of the vehicle and its
// heading (degrees clockwise from north); the body extends backwards.
function footprint(latitude, longitude, heading, size) {
  const angle = (heading * Math.PI) / 180;
  const metersLat = 1 / 111320;
  const metersLon = 1 / (111320 * Math.cos((latitude * Math.PI) / 180));

  const forward = [Math.sin(angle), Math.cos(angle)]; // east, north
  const right = [Math.cos(angle), -Math.sin(angle)];
  const half = size.width / 2;

  const corners = [
    [0, half],
    [0, -half],
    [-size.length, -half],
    [-size.length, half],
  ].map(([along, across]) => {
    const east = forward[0] * along + right[0] * across;
    const north = forward[1] * along + right[1] * across;
    return [longitude + east * metersLon, latitude + north * metersLat];
  });

  return [...corners, corners[0]];
}

// Police cars are drawn separately (policeCarsGeoJson).
function vehiclesGeoJson(vehicles) {
  return {
    type: "FeatureCollection",
    features: vehicles
      .filter(
        (vehicle) =>
          vehicle.latitude != null && !vehicle.vehicle_id?.startsWith("police")
      )
      .map((vehicle) => {
        // Pulled over for the siren: drawn at the kerb, not on the lane.
        const [latitude, longitude] = vehicle.pulled_over
          ? kerbPosition(vehicle.latitude, vehicle.longitude, vehicle.heading)
          : [vehicle.latitude, vehicle.longitude];
        const isAmbulance = vehicle.vehicle_id?.startsWith("ambulance");
        return {
          type: "Feature",
          geometry: {
            type: "Polygon",
            coordinates: [
              footprint(latitude, longitude, vehicle.heading ?? 0, isAmbulance ? AMBULANCE : CAR),
            ],
          },
          properties: {
            // Crashed cars (simulated accident) stand out in orange; other
            // ambulances are near black; drivers pulled over for the siren pink.
            color: vehicle.vehicle_id?.startsWith("incident")
              ? CRASHED_CAR_COLOR
              : isAmbulance
                ? (isDark() ? "#f8fafc" : "#0f172a")
                : vehicle.pulled_over
                  ? PULLED_OVER_COLOR
                  : carColor(vehicle.speed ?? 0),
          },
        };
      }),
  };
}

function routeTrafficGeoJson(segments) {
  return {
    type: "FeatureCollection",
    features: segments.map((segment) => ({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: segment.coordinates.map(([latitude, longitude]) => [
          longitude,
          latitude,
        ]),
      },
      properties: { color: ROUTE_TRAFFIC_COLORS[segment.level] },
    })),
  };
}

function hospitalGeoJson(point, name) {
  if (!point) return EMPTY;
  const [latitude, longitude] = point;
  // Centre the beacon on the point (footprint() starts at the front).
  const shifted = latitude + HOSPITAL_BEACON.length / 2 / 111320;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [footprint(shifted, longitude, 0, HOSPITAL_BEACON)],
        },
        properties: { label: name },
      },
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [longitude, latitude] },
        properties: { label: name },
      },
    ],
  };
}

function patientGeoJson(point) {
  if (!point) return EMPTY;
  const [latitude, longitude] = point;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [square(latitude, longitude, PATIENT_BEACON)],
        },
        properties: { label: "Patient" },
      },
      {
        type: "Feature",
        geometry: { type: "Point", coordinates: [longitude, latitude] },
        properties: { label: "Patient" },
      },
    ],
  };
}

// Roof light bar: a short strip across the roof, behind the cab.
const LIGHT_BAR = { length: 0.7, width: 1.9, back: 1.2 };

// A point `back` metres behind (latitude, longitude) for this heading.
function behind(latitude, longitude, heading, back) {
  const angle = (heading * Math.PI) / 180;
  return [
    latitude - (Math.cos(angle) * back) / 111320,
    longitude -
      (Math.sin(angle) * back) /
        (111320 * Math.cos((latitude * Math.PI) / 180)),
  ];
}

// A centred square footprint (towers and beacons).
function square(latitude, longitude, size) {
  return footprint(latitude + size.length / 2 / 111320, longitude, 0, size);
}

// Police cars: body, flashing light bar, and a label with the station.
function policeCarsGeoJson(vehicles, labels) {
  const features = [];
  for (const vehicle of vehicles) {
    if (!vehicle.vehicle_id?.startsWith("police") || vehicle.latitude == null) {
      continue;
    }
    const heading = vehicle.heading ?? 0;
    const [barLatitude, barLongitude] = behind(
      vehicle.latitude, vehicle.longitude, heading, 1.6
    );
    features.push(
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [
            footprint(vehicle.latitude, vehicle.longitude, heading, POLICE_CAR),
          ],
        },
        properties: { part: "body" },
      },
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [footprint(barLatitude, barLongitude, heading, LIGHT_BAR)],
        },
        properties: { part: "light-bar" },
      },
      {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [vehicle.longitude, vehicle.latitude],
        },
        properties: {
          part: "label",
          label: labels[vehicle.vehicle_id] || "Police",
        },
      }
    );
  }
  return { type: "FeatureCollection", features };
}

// Roads the police manage (blue, pulsing) or have cleared (teal), and
// labels: officers at work, before / after once cleared.
function policeZonesGeoJson(zones) {
  const features = [];
  for (const zone of zones) {
    features.push({
      type: "Feature",
      geometry: {
        type: "LineString",
        coordinates: zone.zone.map(([latitude, longitude]) => [longitude, latitude]),
      },
      properties: { active: zone.active },
    });
    const [latitude, longitude] =
      zone.active && zone.unit_latitude != null
        ? [zone.unit_latitude, zone.unit_longitude]
        : zone.zone[Math.floor(zone.zone.length / 2)];
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [longitude, latitude] },
      properties: {
        active: zone.active,
        label: zone.active
          ? "Police clearing traffic"
          : "Cleared by police",
      },
    });
  }
  return { type: "FeatureCollection", features };
}

// Simulated accidents: a red beacon while the road is blocked.
function incidentsGeoJson(incidents) {
  const features = [];
  for (const incident of incidents) {
    const blocked = incident.cleared_at == null;
    if (blocked) {
      features.push({
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [square(incident.latitude, incident.longitude, CRASH_BEACON)],
        },
        properties: {},
      });
    }
    features.push({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [incident.longitude, incident.latitude],
      },
      properties: {
        label: blocked
          ? incident.police_since != null
            ? "Accident · police clearing it"
            : "Accident · road blocked"
          : incident.cleared_by === "police"
            ? "Accident cleared by police"
            : "Accident cleared",
      },
    });
  }
  return { type: "FeatureCollection", features };
}

// Police stations: a tower coloured by what the station is doing.
function stationsGeoJson(stations, board) {
  const live = Object.fromEntries(
    (board?.stations || []).map((station) => [station.name, station])
  );
  const features = [];
  for (const station of stations) {
    const status = live[station.name]?.status || "available";
    features.push(
      {
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [square(station.latitude, station.longitude, STATION_TOWER)],
        },
        properties: { color: STATION_COLORS[status] },
      },
      {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [station.longitude, station.latitude],
        },
        properties: {
          label:
            status === "available"
              ? station.name
              : `${station.name} · ${live[station.name].status_text}`,
        },
      }
    );
  }
  return { type: "FeatureCollection", features };
}

function ambulanceGeoJson(ambulance) {
  if (ambulance?.latitude == null) return EMPTY;

  const { latitude, longitude } = ambulance;
  const heading = ambulance.heading ?? 0;

  // The light bar starts LIGHT_BAR.back metres behind the front.
  const angle = (heading * Math.PI) / 180;
  const barLatitude =
    latitude - (Math.cos(angle) * LIGHT_BAR.back) / 111320;
  const barLongitude =
    longitude -
    (Math.sin(angle) * LIGHT_BAR.back) /
      (111320 * Math.cos((latitude * Math.PI) / 180));

  const polygon = (coordinates, part) => ({
    type: "Feature",
    geometry: { type: "Polygon", coordinates: [coordinates] },
    properties: { part },
  });

  return {
    type: "FeatureCollection",
    features: [
      polygon(footprint(latitude, longitude, heading, AMBULANCE), "body"),
      polygon(
        footprint(barLatitude, barLongitude, heading, LIGHT_BAR),
        "light-bar"
      ),
    ],
  };
}

function ambulancePointGeoJson(ambulance) {
  if (ambulance?.latitude == null) return EMPTY;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [ambulance.longitude, ambulance.latitude],
        },
        properties: {
          label:
            ambulance.status === "COMPLETED"
              ? "Ambulance arrived"
              : "Ambulance",
        },
      },
    ],
  };
}

function routeGeoJson(route) {
  if (route.length < 2) return EMPTY;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry: {
          type: "LineString",
          coordinates: route.map(([latitude, longitude]) => [
            longitude,
            latitude,
          ]),
        },
        properties: {},
      },
    ],
  };
}

function signalsGeoJson(routeStatuses) {
  return {
    type: "FeatureCollection",
    features: routeStatuses
      .filter((signal) => signal.latitude != null)
      .map((signal) => ({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [signal.longitude, signal.latitude],
        },
        properties: {
          color: STATUS_COLORS[signal.status] || STATUS_COLORS.WAITING,
          label:
            signal.status === "PASSED"
              ? ""
              : `${signalLabel(signal)} · ${SIGNAL_STATUS[signal.status].label}`,
        },
      })),
  };
}

// The traffic-light symbol as a map image (drawn above each signal).
function addSignalImage(map) {
  const size = 48;
  const image = new Image(size, size);
  image.onload = () => {
    if (!map.hasImage("traffic-light")) {
      map.addImage("traffic-light", image, { pixelRatio: 2 });
    }
  };
  image.src =
    "data:image/svg+xml;charset=utf-8," +
    encodeURIComponent(
      SIGNAL_ICON.replace("<svg ", `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" `)
    );
}

function addLayers(map) {
  // Road surfaces, wide enough to read in the tilted view.
  map.addLayer({
    id: "road-surface",
    type: "line",
    source: "openmaptiles",
    "source-layer": "transportation",
    filter: [
      "in",
      ["get", "class"],
      ["literal", ["motorway", "trunk", "primary", "secondary", "tertiary", "minor", "service"]],
    ],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#343d4f",
      "line-width": [
        "interpolate", ["exponential", 2], ["zoom"],
        14, 1.5,
        19, 34,
        20, 68,
      ],
    },
  });

  // 3D buildings from the map data's footprints and heights.
  map.addLayer({
    id: "buildings-3d",
    type: "fill-extrusion",
    source: "openmaptiles",
    "source-layer": "building",
    minzoom: 14,
    paint: {
      "fill-extrusion-color": isDark() ? "#1e2636" : "#d6d9de",
      "fill-extrusion-height": ["coalesce", ["get", "render_height"], 9],
      "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
      "fill-extrusion-opacity": 0.9,
    },
  });

  map.addSource("route", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "route",
    type: "line",
    source: "route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#60a5fa", "line-width": 9, "line-opacity": 0.55 },
  });

  // Traffic on the route ahead: green clear, amber slow, red jammed.
  map.addSource("route-traffic", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "route-traffic",
    type: "line",
    source: "route-traffic",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": ["get", "color"], "line-width": 9, "line-opacity": 0.8 },
  });

  // Roads the police are managing (blue, pulsing) or have cleared (teal).
  map.addSource("police-zones", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "police-zones-active",
    type: "line",
    source: "police-zones",
    filter: ["all", ["==", ["geometry-type"], "LineString"], ["get", "active"]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": POLICE_ACTIVE_COLOR, "line-width": 22, "line-opacity": 0.55 },
  });
  map.addLayer({
    id: "police-zones-cleared",
    type: "line",
    source: "police-zones",
    filter: ["all", ["==", ["geometry-type"], "LineString"], ["!", ["get", "active"]]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": POLICE_CLEARED_COLOR, "line-width": 16, "line-opacity": 0.45 },
  });

  map.addSource("hospital", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "hospital-beacon",
    type: "fill-extrusion",
    source: "hospital",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: {
      "fill-extrusion-color": "#ef4444",
      "fill-extrusion-height": HOSPITAL_BEACON.height,
      "fill-extrusion-opacity": 0.6,
    },
  });

  map.addSource("patient", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "patient-beacon",
    type: "fill-extrusion",
    source: "patient",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: {
      "fill-extrusion-color": PATIENT_COLOR,
      "fill-extrusion-height": PATIENT_BEACON.height,
      "fill-extrusion-opacity": 0.75,
    },
  });

  // Glowing ring on the road so the ambulance is easy to spot.
  map.addSource("ambulance-point", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "ambulance-halo",
    type: "circle",
    source: "ambulance-point",
    paint: {
      "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 15, 6, 19, 26, 20, 52],
      "circle-color": SIREN_COLORS[0],
      "circle-opacity": 0.35,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 2,
      "circle-pitch-alignment": "map",
    },
  });

  map.addSource("vehicles", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "vehicles",
    type: "fill-extrusion",
    source: "vehicles",
    paint: {
      "fill-extrusion-color": ["get", "color"],
      "fill-extrusion-height": CAR.height,
      "fill-extrusion-opacity": 0.95,
    },
  });

  // Police cars: blue and white body, flashing red / blue light bar.
  map.addSource("police-cars", { type: "geojson", data: EMPTY });
  [
    ["police-car-lower", "#1d4ed8", 0, 0.9],
    ["police-car-upper", "#f8fafc", 0.9, POLICE_CAR.height],
  ].forEach(([id, color, base, height]) => {
    map.addLayer({
      id,
      type: "fill-extrusion",
      source: "police-cars",
      filter: ["==", ["get", "part"], "body"],
      paint: {
        "fill-extrusion-color": color,
        "fill-extrusion-base": base,
        "fill-extrusion-height": height,
      },
    });
  });
  map.addLayer({
    id: "police-car-light-bar",
    type: "fill-extrusion",
    source: "police-cars",
    filter: ["==", ["get", "part"], "light-bar"],
    paint: {
      "fill-extrusion-color": SIREN_COLORS[1],
      "fill-extrusion-base": POLICE_CAR.height,
      "fill-extrusion-height": POLICE_CAR.height + 0.35,
    },
  });

  // Police station towers and accident beacons.
  map.addSource("police-stations", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "police-station-towers",
    type: "fill-extrusion",
    source: "police-stations",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: {
      "fill-extrusion-color": ["get", "color"],
      "fill-extrusion-height": STATION_TOWER.height,
      "fill-extrusion-opacity": 0.7,
    },
  });
  map.addSource("incidents", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "incident-beacons",
    type: "fill-extrusion",
    source: "incidents",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: {
      "fill-extrusion-color": "#dc2626",
      "fill-extrusion-height": CRASH_BEACON.height,
      "fill-extrusion-opacity": 0.75,
    },
  });

  // The ambulance: white body with a red stripe (like Indian
  // ambulances) and a flashing red / blue light bar, so it never looks
  // like the other vehicles (red = stopped car).
  map.addSource("ambulance", { type: "geojson", data: EMPTY });
  const body = ["==", ["get", "part"], "body"];
  [
    ["ambulance-lower", "#f8fafc", 0, 1.0],
    ["ambulance-stripe", "#dc2626", 1.0, 1.45],
    ["ambulance-upper", "#f8fafc", 1.45, AMBULANCE.height],
  ].forEach(([id, color, base, height]) => {
    map.addLayer({
      id,
      type: "fill-extrusion",
      source: "ambulance",
      filter: body,
      paint: {
        "fill-extrusion-color": color,
        "fill-extrusion-base": base,
        "fill-extrusion-height": height,
      },
    });
  });
  map.addLayer({
    id: "ambulance-light-bar",
    type: "fill-extrusion",
    source: "ambulance",
    filter: ["==", ["get", "part"], "light-bar"],
    paint: {
      "fill-extrusion-color": SIREN_COLORS[0],
      "fill-extrusion-base": AMBULANCE.height,
      "fill-extrusion-height": AMBULANCE.height + 0.35,
    },
  });

  map.addSource("signals", { type: "geojson", data: EMPTY });
  map.addLayer({
    id: "signals",
    type: "circle",
    source: "signals",
    paint: {
      "circle-radius": 9,
      "circle-color": ["get", "color"],
      "circle-stroke-color": "#0a0f1a",
      "circle-stroke-width": 2,
      "circle-pitch-alignment": "map",
    },
  });
  map.addLayer({
    id: "signal-labels",
    type: "symbol",
    source: "signals",
    layout: {
      // Traffic-light symbol above the signal's dot, its name above that.
      "icon-image": ["case", ["==", ["get", "label"], ""], "", "traffic-light"],
      "icon-anchor": "bottom",
      "icon-offset": [0, -8],
      "icon-allow-overlap": true,
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 13,
      "text-anchor": "bottom",
      "text-offset": [0, -2.9],
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": isDark() ? "#e6e9ef" : "#111827",
      "text-halo-color": isDark() ? "#0a0f1a" : "#ffffff",
      "text-halo-width": 2,
    },
  });

  map.addLayer({
    id: "hospital-label",
    type: "symbol",
    source: "hospital",
    filter: ["==", ["geometry-type"], "Point"],
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 15,
      "text-offset": [0, -1.2],
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": "#ffffff",
      "text-halo-color": "#7f1d1d",
      "text-halo-width": 3,
    },
  });

  map.addLayer({
    id: "patient-label",
    type: "symbol",
    source: "patient",
    filter: ["==", ["geometry-type"], "Point"],
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 14,
      "text-offset": [0, -1.2],
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": "#ffffff",
      "text-halo-color": "#92400e",
      "text-halo-width": 3,
    },
  });

  // Labels: police stations, accidents, officers / cleared roads,
  // police cars.
  const label = (id, source, filter, color, halo, size, offset) => {
    map.addLayer({
      id,
      type: "symbol",
      source,
      filter,
      layout: {
        "text-field": ["get", "label"],
        "text-font": ["Noto Sans Bold"],
        "text-size": size,
        "text-offset": [0, offset],
        "text-max-width": 22,
        "text-allow-overlap": true,
      },
      paint: { "text-color": color, "text-halo-color": halo, "text-halo-width": 2.5 },
    });
  };
  const points = ["==", ["geometry-type"], "Point"];
  label("police-station-labels", "police-stations", points, "#dbeafe", "#1e3a8a", 12, -2.2);
  label("incident-labels", "incidents", points, "#ffffff", "#b91c1c", 14, -2.2);
  label("police-zone-labels", "police-zones", points, "#ffffff", "#1e40af", 13, -1.6);
  label("police-car-labels", "police-cars", ["==", ["get", "part"], "label"], "#ffffff", "#1d4ed8", 12, -1.8);

  // Floating label above the ambulance (drawn last, always on top).
  map.addLayer({
    id: "ambulance-label",
    type: "symbol",
    source: "ambulance-point",
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Bold"],
      "text-size": 14,
      "text-offset": [0, -2.4],
      "text-max-width": 30,
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": "#ffffff",
      "text-halo-color": "#b91c1c",
      "text-halo-width": 3,
    },
  });
}

// 3D view from behind the ambulance, following it along the road.
// Scroll to zoom and right-drag / ctrl-drag to tilt while following;
// dragging the map pauses following until "Follow ambulance" is pressed.
function ChaseView({
  ambulance,
  vehicles = [],
  route = [],
  routeStatuses = [],
  routeTraffic = [],
  hospitalPoint,
  hospitalName,
  pickupPoint,
  policeWatch,
  response,
  incidents = [],
  policeStations = [],
  policeBoard,
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [following, setFollowing] = useState(true);
  // Next camera move sets the chase zoom and tilt (start / resume).
  const resetCamera = useRef(true);

  // Redraw when the view changes size (e.g. the map is expanded).
  useEffect(() => {
    const observer = new ResizeObserver(() => mapRef.current?.resize());
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const map = new MapLibreMap({
      container: containerRef.current,
      style: styleUrl(),
      center: PUNE_CENTER,
      zoom: 15,
      pitch: CAMERA_PITCH,
      maxPitch: 75,
      attributionControl: { compact: true },
    });

    // Moving the map yourself (drag, rotate) pauses following; our own
    // camera moves have no originalEvent. Scroll-zooming keeps following.
    map.on("movestart", (event) => {
      const type = event.originalEvent?.type;
      if (type && type !== "wheel") setFollowing(false);
    });

    map.on("load", () => {
      addSignalImage(map);
      addLayers(map);
      setReady(true);
    });

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Flash the siren lights (steady when the viewer prefers less motion).
  useEffect(() => {
    if (!ready) return undefined;
    const map = mapRef.current;
    const reduceMotion = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)"
    ).matches;
    if (reduceMotion) return undefined;

    let lit = 0;
    const timer = setInterval(() => {
      lit = 1 - lit;
      map.setPaintProperty(
        "ambulance-light-bar", "fill-extrusion-color", SIREN_COLORS[lit]
      );
      map.setPaintProperty("ambulance-halo", "circle-color", SIREN_COLORS[lit]);
      map.setPaintProperty(
        "police-car-light-bar", "fill-extrusion-color", SIREN_COLORS[1 - lit]
      );
      map.setPaintProperty("police-zones-active", "line-opacity", lit ? 0.3 : 0.6);
    }, SIREN_FLASH_MS);
    return () => clearInterval(timer);
  }, [ready]);

  useEffect(() => {
    if (ready) mapRef.current.getSource("route").setData(routeGeoJson(route));
  }, [ready, route]);

  useEffect(() => {
    if (ready) {
      mapRef.current
        .getSource("route-traffic")
        .setData(routeTrafficGeoJson(routeTraffic));
    }
  }, [ready, routeTraffic]);

  useEffect(() => {
    if (ready) {
      mapRef.current
        .getSource("hospital")
        .setData(hospitalGeoJson(hospitalPoint, hospitalName));
    }
  }, [ready, hospitalPoint, hospitalName]);

  // The patient, until the ambulance leaves with them.
  const showPatient = pickupPoint && ambulance?.leg !== "to_hospital";
  useEffect(() => {
    if (ready) {
      mapRef.current
        .getSource("patient")
        .setData(patientGeoJson(showPatient ? pickupPoint : null));
    }
  }, [ready, pickupPoint, showPatient]);

  useEffect(() => {
    if (ready) {
      const map = mapRef.current;
      map.getSource("vehicles").setData(vehiclesGeoJson(vehicles));
      map
        .getSource("police-cars")
        .setData(policeCarsGeoJson(vehicles, policeUnitLabels(policeWatch, response)));
    }
  }, [ready, vehicles, policeWatch, response]);

  useEffect(() => {
    if (ready) {
      mapRef.current
        .getSource("police-zones")
        .setData(policeZonesGeoJson(policeZones(policeWatch, response)));
    }
  }, [ready, policeWatch, response]);

  useEffect(() => {
    if (ready) {
      mapRef.current.getSource("incidents").setData(incidentsGeoJson(incidents));
    }
  }, [ready, incidents]);

  useEffect(() => {
    if (ready) {
      mapRef.current
        .getSource("police-stations")
        .setData(stationsGeoJson(policeStations, policeBoard));
    }
  }, [ready, policeStations, policeBoard]);

  useEffect(() => {
    if (ready) {
      mapRef.current.getSource("signals").setData(signalsGeoJson(routeStatuses));
    }
  }, [ready, routeStatuses]);

  useEffect(() => {
    if (!ready) return;
    const map = mapRef.current;

    map.getSource("ambulance").setData(ambulanceGeoJson(ambulance));
    map.getSource("ambulance-point").setData(ambulancePointGeoJson(ambulance));

    if (!following) return;

    if (ambulance?.latitude == null) {
      // Before departure: look at the start of the route.
      if (route.length > 0) {
        map.jumpTo({ center: [route[0][1], route[0][0]], zoom: 16 });
      }
      return;
    }

    // Follow from behind: ambulance in the lower part of the view. Keep
    // whatever zoom and tilt the user has chosen.
    const camera = {
      center: [ambulance.longitude, ambulance.latitude],
      bearing: ambulance.heading ?? map.getBearing(),
      offset: [0, map.getContainer().clientHeight * 0.22],
      duration: 500,
      easing: (t) => t,
    };

    // Set zoom and tilt instantly: the next update (every 0.5 s) would
    // interrupt a slower animation and leave the camera halfway.
    if (resetCamera.current) {
      camera.zoom = CAMERA_ZOOM;
      camera.pitch = CAMERA_PITCH;
      camera.duration = 0;
      resetCamera.current = false;
    }

    map.easeTo(camera);
  }, [ready, ambulance, route, following]);

  return (
    <div className="chase-view">
      <div ref={containerRef} className="chase-map" />
      {!ambulance && (
        <div className="chase-waiting">
          The chase view follows the ambulance once it sets off.
        </div>
      )}
      {ambulance && !following && (
        <button
          className="button button-primary chase-follow"
          onClick={() => {
            resetCamera.current = true;
            setFollowing(true);
          }}
        >
          Follow ambulance
        </button>
      )}
    </div>
  );
}

export default ChaseView;
