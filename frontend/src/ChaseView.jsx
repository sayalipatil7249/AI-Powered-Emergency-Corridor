import { useEffect, useRef, useState } from "react";
import { Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre draws tiles in a web worker; let Vite bundle it and tell
// MapLibre where it is (its own lookup breaks under Vite's bundling).
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

import { SIGNAL_STATUS, signalLabel } from "./routeStatus";
import { SIGNAL_ICON } from "./icons";
import { ROUTE_TRAFFIC_COLORS, carColor } from "./trafficColors";

setWorkerUrl(maplibreWorkerUrl);

// Free vector map with building footprints and heights (no key).
const STYLE_URL = "https://tiles.openfreemap.org/styles/dark";

const PUNE_CENTER = [73.859, 18.523]; // longitude, latitude

const STATUS_COLORS = {
  GREEN: "#22c55e",
  TURNING: "#84cc16",
  READY: "#f59e0b",
  WAITING: "#64748b",
  PASSED: "#334155",
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

function vehiclesGeoJson(vehicles) {
  return {
    type: "FeatureCollection",
    features: vehicles
      .filter((vehicle) => vehicle.latitude != null)
      .map((vehicle) => ({
        type: "Feature",
        geometry: {
          type: "Polygon",
          coordinates: [
            footprint(
              vehicle.latitude,
              vehicle.longitude,
              vehicle.heading ?? 0,
              CAR
            ),
          ],
        },
        properties: {
          color: vehicle.vehicle_id?.startsWith("police")
            ? "#1d4ed8"
            : carColor(vehicle.speed ?? 0),
        },
      })),
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

// Roof light bar: a short strip across the roof, behind the cab.
const LIGHT_BAR = { length: 0.7, width: 1.9, back: 1.2 };

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
  const speed = Math.round((ambulance.speed ?? 0) * 3.6);
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
              ? "Ambulance · arrived"
              : `Ambulance · ${speed} km/h`,
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
      "fill-extrusion-color": "#1e2636",
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
      "text-color": "#e6e9ef",
      "text-halo-color": "#0a0f1a",
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
}) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [following, setFollowing] = useState(true);
  // Next camera move sets the chase zoom and tilt (start / resume).
  const resetCamera = useRef(true);

  useEffect(() => {
    const map = new MapLibreMap({
      container: containerRef.current,
      style: STYLE_URL,
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

  useEffect(() => {
    if (ready) {
      mapRef.current.getSource("vehicles").setData(vehiclesGeoJson(vehicles));
    }
  }, [ready, vehicles]);

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
