import { useEffect, useMemo, useRef, useState } from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Polyline,
  Polygon,
  CircleMarker,
  Popup,
  Tooltip,
  useMap,
  useMapEvents,
} from "react-leaflet";

import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./leafletConfig";

import {
  AMBULANCE_ICON,
  CHECK_ICON,
  CLEARED_ICON,
  CRASH_ICON,
  HOSPITAL_ICON,
  JAM_ICON,
  POLICE_ICON,
  SIGNAL_ICON,
  START_ICON,
} from "./icons";
import { SIGNAL_STATUS, formatDuration, signalLabel } from "./routeStatus";
import { PULLED_OVER_COLOR, kerbPosition } from "./pulledOver";
import { useTheme } from "./theme";
import { ROUTE_TRAFFIC_COLORS, carColor } from "./trafficColors";
import {
  ACTIVE_ALERTS,
  POLICE_ACTIVE_COLOR,
  POLICE_CLEARED_COLOR,
  policeZones,
  zoneSummary,
} from "./policeZones";
import ChaseView from "./ChaseView";
import MovingMarker from "./components/MovingMarker";
import { ambulanceColor, legLabel } from "./fleet";
import { API_URL } from "./api";

// Stretches of the route without signals (police cover them).
const NO_SIGNAL_COLOR = "#c084fc";


// Colours for live (TomTom) traffic on the map.
const LIVE_COLORS = {
  free: "#22c55e",
  slow: "#f59e0b",
  jammed: "#ef4444",
  closed: "#991b1b",
};

const PUNE_CENTER = [18.523, 73.859];

const LEGEND = ["GREEN", "TURNING", "READY", "WAITING", "PASSED"];

// The ambulance turns to face where it is driving (MovingMarker).
const ambulanceIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-ambulance"><div class="map-heading">${AMBULANCE_ICON}</div></div>`,
  iconSize: [40, 40],
  iconAnchor: [20, 20],
});

// Other ambulances (several at once): their own colour ring.
const otherAmbulanceIcons = {};
function otherAmbulanceIcon(number) {
  if (!otherAmbulanceIcons[number]) {
    const color = ambulanceColor(number);
    otherAmbulanceIcons[number] = L.divIcon({
      className: "map-icon",
      html: `<div class="map-ambulance other" style="background:${color};box-shadow:0 0 0 6px ${color}40,0 4px 12px rgba(0,0,0,0.45)"><div class="map-heading">${AMBULANCE_ICON}</div></div>`,
      iconSize: [34, 34],
      iconAnchor: [17, 17],
    });
  }
  return otherAmbulanceIcons[number];
}

// Police stations: bigger when they cover this route or are busy, and
// coloured by what they are doing (corridor/police_board.py).
const stationIcons = {};
function stationIcon(onRoute, status) {
  const key = `${onRoute}-${status}`;
  if (!stationIcons[key]) {
    const size = onRoute || status !== "available" ? 26 : 18;
    stationIcons[key] = L.divIcon({
      className: "map-icon",
      html: `<div class="map-place station-${status} ${
        onRoute ? "route-station" : ""
      }">${POLICE_ICON}</div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }
  return stationIcons[key];
}

// 108 ambulances not on a call: at their station, handing over a
// patient, or driving back (corridor/dispatch.py). ALS red, BLS blue.
const unitIcons = {};
function unitIcon(kind, status) {
  const key = `${kind}-${status}`;
  if (!unitIcons[key]) {
    unitIcons[key] = L.divIcon({
      className: "map-icon",
      html: `<div class="map-unit unit-${kind} unit-${status}">${AMBULANCE_ICON}<span>${kind[0]}</span></div>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
  }
  return unitIcons[key];
}

// The police unit sent to a jam: bigger, with a flashing siren.
const policeUnitIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-police-unit siren">${POLICE_ICON}</div>`,
  iconSize: [30, 30],
  iconAnchor: [15, 15],
});

const crashIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place map-crash">${CRASH_ICON}</div>`,
  iconSize: [28, 28],
  iconAnchor: [14, 14],
});

const clearedIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place">${CLEARED_ICON}</div>`,
  iconSize: [22, 22],
  iconAnchor: [11, 11],
});

const jamIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place">${JAM_ICON}</div>`,
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});

const startIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place">${START_ICON}</div>`,
  iconSize: [24, 24],
  iconAnchor: [12, 12],
});

const hospitalIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place">${HOSPITAL_ICON}</div>`,
  iconSize: [28, 28],
  iconAnchor: [14, 14],
});

// The patient's location (the whole journey's pickup).
const patientIcon = L.divIcon({
  className: "map-icon",
  html: '<div class="map-patient">🧍</div>',
  iconSize: [26, 26],
  iconAnchor: [13, 13],
});

// Every other hospital: small, named on hover (like a map app).
const smallHospitalIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place map-place-small">${HOSPITAL_ICON}</div>`,
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});

// Everything outside the simulated area is dimmed: a polygon covering
// the world with the area cut out as a hole.
const WORLD = [[-89, -179], [-89, 179], [89, 179], [89, -179]];

// The area's outline follows where the simulated roads are.
function areaRing(area) {
  if (area.outline?.length > 2) return area.outline;
  return [
    [area.south, area.west],
    [area.south, area.east],
    [area.north, area.east],
    [area.north, area.west],
  ];
}

// A traffic-light symbol and the signal's number, in its status colour.
function createSignalIcon(signal, isNext) {
  const content =
    signal.status === "PASSED"
      ? CHECK_ICON
      : `${SIGNAL_ICON}<span>${signal.number}</span>`;
  const height = isNext ? 30 : 24;
  const width = signal.status === "PASSED" ? height : Math.round(height * 1.7);

  return L.divIcon({
    className: "map-icon",
    html: `<div class="map-signal ${SIGNAL_STATUS[signal.status].className} ${
      isNext ? "next" : ""
    }">${content}</div>`,
    iconSize: [width, height],
    iconAnchor: [width / 2, height / 2],
  });
}

// Picks the trip's start point when the planner's "Pick on map" is on.
// Click anywhere (when not picking a start): what is there - a hospital,
// a police station or the street - with quick actions.
function MapClickInfo({ active, hospitals, onSetStart, onSetDestination }) {
  const [info, setInfo] = useState(null);

  useMapEvents({
    click(event) {
      if (!active) return;
      const { lat, lng } = event.latlng;
      setInfo({ latitude: lat, longitude: lng, place: null });
      fetch(`${API_URL}/plan/where?latitude=${lat}&longitude=${lng}`)
        .then((response) => (response.ok ? response.json() : null))
        .then((place) =>
          setInfo((current) =>
            current && current.latitude === lat && current.longitude === lng
              ? { ...current, place }
              : current
          )
        )
        .catch(() => {});
    },
  });

  if (!info) return null;
  const { place } = info;
  const hospital =
    place?.kind === "hospital" && hospitals.find((item) => item.name === place.name);

  return (
    <Popup
      position={[info.latitude, info.longitude]}
      eventHandlers={{ remove: () => setInfo(null) }}
    >
      <div className="place-popup">
        <strong>{place ? place.name || "Unnamed road" : "Looking up…"}</strong>
        {place?.kind !== "street" && place?.street && (
          <span className="muted">on {place.street}</span>
        )}
        <span className="muted">
          {info.latitude.toFixed(5)}, {info.longitude.toFixed(5)}
        </span>
        {place && !place.inside_area && (
          <span className="muted">Outside the map area</span>
        )}
        {place?.inside_area && (onSetStart || (hospital && onSetDestination)) && (
          <div className="place-popup-actions">
            {onSetStart && (
              <button
                className="button button-secondary"
                onClick={() => {
                  onSetStart({
                    name: place.name || "Point on the map",
                    latitude: info.latitude,
                    longitude: info.longitude,
                  });
                  setInfo(null);
                }}
              >
                Start from here
              </button>
            )}
            {hospital && onSetDestination && (
              <button
                className="button button-secondary"
                onClick={() => {
                  onSetDestination(hospital);
                  setInfo(null);
                }}
              >
                Set as destination
              </button>
            )}
          </div>
        )}
      </div>
    </Popup>
  );
}

function MapClickPicker({ active, onPick }) {
  useMapEvents({
    click(event) {
      if (active) {
        onPick({ latitude: event.latlng.lat, longitude: event.latlng.lng });
      }
    },
  });
  return null;
}

// Zooms to each new route once, then (optionally) keeps the ambulance
// in view as it drives.
// Leaflet only redraws on window resizes; the map also changes size
// when it is expanded or the side panel changes.
function ResizeWatcher() {
  const map = useMap();

  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map]);

  return null;
}

// "All" camera: refit to every active ambulance at most this often (ms),
// so the map does not jitter with each position update.
const SHOW_ALL_EVERY_MS = 2500;

function MapCamera({ route, ambulance, follow, showAll = false, positions = [], area }) {
  const map = useMap();
  const fittedRoute = useRef("");
  const fittedArea = useRef(false);

  // Before any route: show the whole simulated area, and keep the map
  // from drifting far away from it.
  useEffect(() => {
    if (!area || fittedArea.current) return;

    const bounds = [
      [area.south, area.west],
      [area.north, area.east],
    ];
    // Degrees (about 30 km) of room around the area: enough to drag the
    // map freely at any zoom - a tight limit snapped every drag back when
    // zoomed out - while the map cannot be lost entirely.
    const margin = 0.3;

    map.setMaxBounds([
      [area.south - margin, area.west - margin],
      [area.north + margin, area.east + margin],
    ]);
    map.setMinZoom(11);

    if (route.length < 2) {
      map.fitBounds(bounds, { padding: [30, 30] });
    }
    fittedArea.current = true;
  }, [map, area, route.length]);

  // Leaflet measures its box once at start-up; re-measure whenever the
  // layout changes so tiles fill the whole map.
  useEffect(() => {
    const observer = new ResizeObserver(() => map.invalidateSize());
    observer.observe(map.getContainer());
    return () => observer.disconnect();
  }, [map]);

  useEffect(() => {
    // A new route (planned or live) is identified by its end points.
    const signature = route.length > 1
      ? `${route[0]}|${route[route.length - 1]}|${route.length}`
      : "";

    if (signature && signature !== fittedRoute.current) {
      // Extra top padding keeps the route clear of the status overlay.
      map.fitBounds(route, {
        paddingTopLeft: [60, 110],
        paddingBottomRight: [60, 70],
      });
    }

    fittedRoute.current = signature;
  }, [map, route]);

  useEffect(() => {
    if (
      follow &&
      ambulance?.latitude != null &&
      ambulance?.longitude != null
    ) {
      map.panTo([ambulance.latitude, ambulance.longitude], {
        animate: true,
      });
    }
  }, [map, follow, ambulance?.latitude, ambulance?.longitude]);

  // Keep every active ambulance in view.
  const lastFit = useRef(0);
  const positionsKey = positions.map((point) => point.join(",")).join("|");
  useEffect(() => {
    if (!showAll || positions.length === 0) return;
    const now = Date.now();
    if (now - lastFit.current < SHOW_ALL_EVERY_MS) return;
    lastFit.current = now;
    if (positions.length === 1) {
      map.panTo(positions[0], { animate: true });
    } else {
      map.fitBounds(positions, {
        paddingTopLeft: [60, 110],
        paddingBottomRight: [60, 60],
        maxZoom: 17,
        animate: true,
      });
    }
    // positionsKey stands for positions (a new array every update).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, showAll, positionsKey]);

  return null;
}

function MapView({
  ambulance,
  vehicles = [],
  route = [],
  routeStatuses = [],
  routeTraffic = [],
  policeStations = [],
  units = [],
  routePolice = [],
  stretches = [],
  policeWatch,
  policeBoard,
  incidents = [],
  response,
  startPoint,
  hospitalPoint,
  overlay,
  previewSignals = [],
  previewRoutes = [],
  previewFocus = false,
  sharedJunctions = [],
  hospitalName,
  area,
  liveTraffic,
  pickMode = false,
  onPick,
  otherAmbulances = [],
  selectedNumber = 1,
  selectedAmbulanceId,
  onSelectAmbulance,
  view,
  onViewChange,
  camera = "follow",
  onCameraChange,
  expanded = false,
  onExpandChange,
  pickupPoint,
  hospitals = [],
  onSetStart,
  onSetDestination,
}) {
  const showingMap = view === "map" || previewRoutes.length > 0;
  // The legend is folded behind a button so it never covers the map.
  const [showLegend, setShowLegend] = useState(false);
  // Map labels (hospital, police, jams, the next signal...): off by
  // default so they don't hide the roads; hover any marker for its label.
  const [showLabels, setShowLabels] = useState(() => {
    try {
      return localStorage.getItem("mapLabels") === "on";
    } catch {
      return false;
    }
  });
  const labelsKey = showLabels ? "labels-on" : "labels-off";
  const toggleLabels = () => {
    const next = !showLabels;
    setShowLabels(next);
    try {
      localStorage.setItem("mapLabels", next ? "on" : "off");
    } catch {
      // private window: the choice lasts until the page is closed
    }
  };
  const theme = useTheme();

  const nextSignal = routeStatuses.find(
    (signal) => signal.status !== "PASSED"
  );

  // Stations covering this route: "covers" says which roads, how fast.
  const coverByStation = Object.fromEntries(
    routePolice.map((station) => [station.name, station.covers])
  );
  const activeAlerts = (policeWatch?.alerts || []).filter((alert) =>
    ACTIVE_ALERTS.includes(alert.status)
  );
  const liveStations = Object.fromEntries(
    (policeBoard?.stations || []).map((station) => [station.name, station])
  );
  const zones = policeZones(policeWatch, response);

  // Police zones are drawn as SVG (not canvas) so they can be animated.
  const svgRenderer = useMemo(() => L.svg({ padding: 0.5 }), []);

  const start = startPoint || route[0];
  const hospital = hospitalPoint || route[route.length - 1];
  const arrived = ambulance?.status === "COMPLETED";

  return (
    <div className={`map-wrapper ${pickMode ? "picking" : ""}`}>
      {!showingMap && (
        <div className="chase-layer">
          <ChaseView
            key={`${selectedAmbulanceId}-${theme}`}
            ambulance={ambulance}
            vehicles={[
              ...vehicles,
              ...otherAmbulances.filter((item) => item.status === "driving"),
            ]}
            route={route}
            routeStatuses={routeStatuses}
            routeTraffic={routeTraffic}
            hospitalPoint={hospital}
            hospitalName={hospitalName}
            pickupPoint={pickupPoint}
            policeWatch={policeWatch}
            response={response}
            incidents={incidents}
            policeStations={policeStations}
            policeBoard={policeBoard}
          />
        </div>
      )}

      <MapContainer
        center={PUNE_CENTER}
        zoom={15}
        className="map"
        zoomControl={false}
        preferCanvas
      >
        {/* OpenStreetMap, turned dark in CSS to match the dashboard.
            (Esri's dark tiles were tried but failed to load in browsers.) */}
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
          maxZoom={19}
        />

        {area && (
          <>
            <Polygon
              positions={[WORLD, areaRing(area)]}
              interactive={false}
              pathOptions={{
                stroke: false,
                fillColor: theme === "dark" ? "#05070c" : "#ffffff",
                fillOpacity: theme === "dark" ? 0.55 : 0.6,
              }}
            />
            <Polygon
              positions={areaRing(area)}
              interactive={false}
              pathOptions={{
                color: "#64748b",
                weight: 1.5,
                opacity: 0.7,
                dashArray: "6 6",
                fill: false,
              }}
            />
          </>
        )}

        <ResizeWatcher />
        <MapCamera
          route={previewRoutes.length && previewFocus
            ? previewRoutes.find((item) => item.number === selectedNumber)?.geometry
              || previewRoutes.flatMap((item) => item.geometry)
            : previewRoutes.length ? previewRoutes.flatMap((item) => item.geometry) : route}
          ambulance={ambulance}
          follow={camera === "follow"}
          showAll={camera === "all"}
          positions={[
            ...(ambulance?.latitude != null && ambulance.status !== "COMPLETED"
              ? [[ambulance.latitude, ambulance.longitude]] : []),
            ...otherAmbulances
              .filter((item) => item.status === "driving" && item.latitude != null)
              .map((item) => [item.latitude, item.longitude]),
          ]}
          area={area}
        />
        <MapClickPicker active={pickMode} onPick={onPick} />
        <MapClickInfo
          active={!pickMode}
          hospitals={hospitals}
          onSetStart={onSetStart}
          onSetDestination={onSetDestination}
        />

        {/* Live traffic on real roads (TomTom), under the route */}
        {liveTraffic?.segments.map((segment, index) => (
          <Polyline
            key={`live-${index}`}
            positions={segment.coordinates}
            interactive={false}
            pathOptions={{
              color: LIVE_COLORS[segment.level] || LIVE_COLORS.free,
              weight: 4,
              opacity: 0.75,
              dashArray: segment.level === "closed" ? "4 6" : null,
            }}
          />
        ))}

        {previewRoutes.map((item) => (
          <Polyline key={`request-${item.number}`} positions={item.geometry}
            eventHandlers={{ click: () => onSelectAmbulance?.(
              `ambulance_${String(item.number).padStart(2, "0")}`, "map"
            ) }}
            pathOptions={{ color: ambulanceColor(item.number),
              weight: item.number === selectedNumber ? 7 : 4, opacity: 0.85,
              dashArray: item.number === 1 ? undefined : "10 8" }}>
            <Tooltip sticky>{item.label}: {item.start_name} → {item.hospital_name} · click to view route</Tooltip>
          </Polyline>
        ))}
        {previewRoutes.flatMap((item) => [
          { point: item.geometry[0], label: `${item.label} start` },
          { point: item.geometry.at(-1), label: `${item.label}: ${item.hospital_name}` },
        ].filter((endpoint) => endpoint.point).map((endpoint, index) => (
          <CircleMarker key={`endpoint-${item.number}-${index}`} center={endpoint.point} radius={6}
            pathOptions={{ color: ambulanceColor(item.number), fillOpacity: 1 }}>
            <Tooltip>{endpoint.label}</Tooltip>
          </CircleMarker>
        )))}
        {sharedJunctions.map((junction) => (
          <CircleMarker key={`shared-${junction.signal_id}`}
            center={[junction.latitude, junction.longitude]} radius={11}
            pathOptions={{ color: "#d97706", fillColor: "#ffffff", fillOpacity: 0.9, weight: 3 }}>
            <Tooltip direction="top">
              <strong>Shared junction: {junction.name || junction.signal_id}</strong>
              {junction.visits.map((visit, index) => (
                <div key={`${visit.ambulance}-${index}`}>
                  {visit.ambulance}: arrival ~{Math.round(visit.arrival_seconds)} s
                  {visit.wait_seconds > 0 ? ` · give way ~${Math.round(visit.wait_seconds)} s` : " · no wait predicted"}
                </div>
              ))}
            </Tooltip>
          </CircleMarker>
        ))}

        {/* Ambulance route */}
        {route.length > 1 && (
          <>
            <Polyline
              positions={route}
              interactive={false}
              pathOptions={{ color: ambulanceColor(selectedNumber), weight: 12, opacity: 0.18 }}
            />
            <Polyline
              positions={route}
              interactive={false}
              pathOptions={{ color: ambulanceColor(selectedNumber), weight: 5, opacity: 0.95 }}
            />
          </>
        )}

        {/* Traffic on the route ahead: green clear, amber slow, red jammed */}
        {routeTraffic.map((segment, index) => (
          <Polyline
            key={`route-traffic-${index}`}
            positions={segment.coordinates}
            interactive={false}
            pathOptions={{
              color: ROUTE_TRAFFIC_COLORS[segment.level],
              weight: 5,
              opacity: 0.95,
            }}
          />
        ))}

        {/* Stretches without signals: dashed; the police watch them */}
        {stretches.map((stretch) =>
          stretch.geometry?.length > 1 ? (
            <Polyline
              key={`stretch-${stretch.number}`}
              positions={stretch.geometry}
              pathOptions={{
                color: NO_SIGNAL_COLOR,
                weight: stretch.state === "jammed" ? 4 : 2.5,
                opacity: stretch.state === "passed" ? 0.35 : 0.95,
                dashArray: "3 7",
              }}
            >
              <Tooltip sticky>
                No signal: {stretch.name} ({stretch.length_meters} m)
                {stretch.state === "jammed" && " · jammed"}
              </Tooltip>
            </Polyline>
          ) : null
        )}

        {start && (
          <Marker position={start} icon={startIcon}>
            <Tooltip direction="top" offset={[0, -12]}>
              {pickupPoint ? "Ambulance base" : "Start"}
            </Tooltip>
          </Marker>
        )}

        {pickupPoint && (
          <Marker position={pickupPoint} icon={patientIcon} zIndexOffset={2500}>
            <Tooltip key={labelsKey} permanent={showLabels} direction="top" offset={[0, -14]}>
              Patient
            </Tooltip>
          </Marker>
        )}

        {hospital && (
          <Marker position={hospital} icon={hospitalIcon} zIndexOffset={3000}>
            {/* Left, so it never collides with signal labels (drawn right) */}
            <Tooltip key={labelsKey} permanent={showLabels} direction="left" offset={[-16, 0]}>
              {hospitalName}
            </Tooltip>
          </Marker>
        )}

        {/* Every hospital: name on hover, actions on click */}
        {hospitals.map((item) =>
          hospital &&
          Math.abs(item.latitude - hospital[0]) < 1e-6 &&
          Math.abs(item.longitude - hospital[1]) < 1e-6 ? null : (
            <Marker
              key={`hospital-${item.name}-${item.latitude}`}
              position={[item.latitude, item.longitude]}
              icon={smallHospitalIcon}
              zIndexOffset={-200}
            >
              <Tooltip direction="top" offset={[0, -8]}>
                {item.name}
              </Tooltip>
              <Popup>
                <div className="place-popup">
                  <strong>{item.name}</strong>
                  <span className="muted">Hospital</span>
                  {onSetDestination && (
                    <div className="place-popup-actions">
                      <button
                        className="button button-secondary"
                        onClick={() => onSetDestination(item)}
                      >
                        Set as destination
                      </button>
                    </div>
                  )}
                </div>
              </Popup>
            </Marker>
          )
        )}

        {/* Police stations that can be sent to clear a jam */}
        {units
          .filter((unit) => unit.status !== "on_call")
          .map((unit) => (
            <Marker
              key={`unit-${unit.id}`}
              position={[unit.latitude, unit.longitude]}
              icon={unitIcon(unit.kind, unit.status)}
              zIndexOffset={unit.status === "available" ? 0 : 600}
            >
              <Tooltip direction="top" offset={[0, -8]}>
                108 {unit.id} · {unit.kind} · {unit.status_label}
                {unit.status === "available" ? ` at ${unit.station}` : ""}
              </Tooltip>
            </Marker>
          ))}

        {policeStations.map((station) => {
          const covers = coverByStation[station.name];
          const live = liveStations[station.name];
          const status = live?.status || "available";
          return (
            <Marker
              key={`police-${station.name}`}
              position={[station.latitude, station.longitude]}
              icon={stationIcon(Boolean(covers), status)}
              zIndexOffset={covers || status !== "available" ? 500 : 0}
            >
              <Tooltip direction="top" offset={[0, -8]}>
                {station.name}
                {status !== "available" && ` · ${live.status_text}`}
              </Tooltip>
              {/* Click for details */}
              <Popup>
                <div className="station-popup">
                  <strong>{station.name}</strong>
                  {station.name_local && (
                    <span lang="mr">{station.name_local}</span>
                  )}
                  <span className="muted">
                    {station.kind || "Police"}
                    {station.road_name && ` · on ${station.road_name}`}
                  </span>
                  <span className={station.phone ? "" : "muted"}>
                    {station.phone
                      ? `📞 ${station.phone} (contact in database)`
                      : "No contact number saved"}
                  </span>
                  {live && (
                    <span className={`station-state status-${status}`}>
                      {live.status_text}
                      {live.task_road && ` · ${live.task_road}`}
                    </span>
                  )}
                  {live?.reach_seconds != null && (
                    <span>
                      Can reach the ambulance in ~
                      {formatDuration(live.reach_seconds)} (live traffic)
                    </span>
                  )}
                  {covers && (
                    <span>
                      Covers on this route:{" "}
                      {covers
                        .map(
                          (cover) =>
                            `${cover.road} (${Math.max(1, Math.round(cover.drive_seconds / 60))} min)`
                        )
                        .join(", ")}
                    </span>
                  )}
                  {live?.jams_cleared > 0 && (
                    <span>Jams cleared this trip: {live.jams_cleared}</span>
                  )}
                </div>
              </Popup>
            </Marker>
          );
        })}

        {/* Roads the police are managing now (animated) or have cleared */}
        {zones.map((zone) => (
          <Polyline
            key={zone.key}
            positions={zone.zone}
            pathOptions={{
              renderer: svgRenderer,
              color: zone.active ? POLICE_ACTIVE_COLOR : POLICE_CLEARED_COLOR,
              weight: zone.active ? 16 : 12,
              opacity: zone.active ? 0.45 : 0.35,
              lineCap: "round",
              className: zone.active ? "police-zone-active" : "police-zone-cleared",
            }}
          >
            <Tooltip sticky>
              {zone.active
                ? `Police clearing traffic (${zone.station})`
                : `Cleared by police (${zone.station})`}
            </Tooltip>
          </Polyline>
        ))}
        {zones
          .filter((zone) => !zone.active)
          .map((zone) => (
            <Marker
              key={`${zone.key}-done`}
              position={zone.zone[Math.floor(zone.zone.length / 2)]}
              icon={clearedIcon}
              zIndexOffset={1200}
            >
              <Tooltip direction="top" offset={[0, -10]}>
                Cleared by police · {zone.road}
                <br />
                {zoneSummary(zone)}
              </Tooltip>
            </Marker>
          ))}

        {/* Simulated accidents */}
        {incidents.map((incident) => (
          <Marker
            key={incident.incident_id}
            position={[incident.latitude, incident.longitude]}
            icon={incident.cleared_at == null ? crashIcon : clearedIcon}
            zIndexOffset={1600}
            opacity={incident.cleared_at == null ? 1 : 0.8}
          >
            <Tooltip
              key={labelsKey}
              permanent={showLabels && incident.cleared_at == null}
              direction="left"
              offset={[-14, 0]}
            >
              {incident.cleared_at == null
                ? incident.police_since != null
                  ? "Accident · police clearing it"
                  : "Accident (simulated) · road blocked"
                : incident.cleared_by === "police"
                  ? "Accident cleared by police"
                  : "Accident cleared"}
            </Tooltip>
          </Marker>
        ))}

        {/* Jams on stretches without signals that police were called to */}
        {activeAlerts.map((alert) => (
          <Marker
            key={`alert-${alert.alert_id}`}
            position={[alert.latitude, alert.longitude]}
            icon={jamIcon}
            zIndexOffset={1500}
          >
            <Tooltip key={labelsKey} permanent={showLabels} direction="bottom" offset={[0, 12]}>
              No signal, jammed: {alert.road}
            </Tooltip>
          </Marker>
        ))}
        {activeAlerts
          .filter((alert) => alert.unit_latitude != null)
          .map((alert) => (
            <MovingMarker
              key={`unit-${alert.alert_id}`}
              position={[alert.unit_latitude, alert.unit_longitude]}
              icon={policeUnitIcon}
              zIndexOffset={1800}
              trailColor={POLICE_ACTIVE_COLOR}
            >
              <Tooltip key={labelsKey} permanent={showLabels} direction="top" offset={[0, -16]}>
                {alert.status === "ON_SCENE"
                  ? "Police clearing traffic"
                  : `Police from ${alert.station}`}
              </Tooltip>
            </MovingMarker>
          ))}

        {/* Deadlock ahead that the AI responded to */}
        {response?.jam &&
          ["police_en_route", "police_clearing"].includes(response.status) && (
            <Marker
              position={[response.jam.latitude, response.jam.longitude]}
              icon={jamIcon}
              zIndexOffset={1500}
            >
              <Tooltip key={labelsKey} permanent={showLabels} direction="bottom" offset={[0, 12]}>
                Deadlock: {response.jam.name}
              </Tooltip>
            </Marker>
          )}

        {/* The police unit on its way / at the jam */}
        {response?.police && response.police.stage !== "done" && (
          <MovingMarker
            key={`response-${response.police.unit_id}`}
            position={[response.police.latitude, response.police.longitude]}
            icon={policeUnitIcon}
            zIndexOffset={1800}
            trailColor={POLICE_ACTIVE_COLOR}
          >
            <Tooltip key={labelsKey} permanent={showLabels} direction="top" offset={[0, -16]}>
              {response.police.stage === "clearing"
                ? "Police clearing traffic"
                : `Police · ${Math.round(response.police.eta_seconds)} s away`}
            </Tooltip>
          </MovingMarker>
        )}

        {/* Other ambulances' routes */}
        {otherAmbulances.map((item) =>
          item.route?.length > 1 ? (
            <Polyline
              key={`route-${item.vehicle_id}`}
              positions={item.route}
              eventHandlers={{ click: () => onSelectAmbulance?.(item.vehicle_id, "map") }}
              pathOptions={{
                color: ambulanceColor(item.number),
                weight: 4,
                opacity: 0.75,
                dashArray: "10 8",
              }}
            >
              <Tooltip sticky>{item.label}: {item.start_name} → {item.hospital_name} · click to view route</Tooltip>
            </Polyline>
          ) : null
        )}

        {/* Other vehicles */}
        {vehicles.map((vehicle) =>
          vehicle.latitude == null || vehicle.longitude == null ? null : (
            <CircleMarker
              key={vehicle.vehicle_id}
              center={
                // Pulled over for the siren: drawn at the kerb.
                vehicle.pulled_over
                  ? kerbPosition(vehicle.latitude, vehicle.longitude, vehicle.heading)
                  : [vehicle.latitude, vehicle.longitude]
              }
              radius={3.5}
              pathOptions={{
                color: "#ffffff",
                fillColor: vehicle.vehicle_id.startsWith("police")
                  ? "#3b82f6"
                  : vehicle.pulled_over
                    ? PULLED_OVER_COLOR
                    : carColor(vehicle.speed),
                fillOpacity: 0.85,
                weight: 1,
              }}
            />
          )
        )}

        {/* Signals on a planned (not yet started) route */}
        {previewSignals.map((signal) => (
          <Marker
            key={`preview-signal-${signal.number}`}
            position={[signal.latitude, signal.longitude]}
            icon={createSignalIcon(
              { ...signal, status: "WAITING" },
              false
            )}
          >
            <Tooltip direction="right" offset={[16, 0]}>
              {signalLabel(signal)}
            </Tooltip>
          </Marker>
        ))}

        {/* Traffic signals on the route only */}
        {routeStatuses.map((signal) => {
          if (signal.latitude == null || signal.longitude == null) {
            return null;
          }

          const isNext = signal === nextSignal;

          return (
            <Marker
              key={`route-signal-${signal.number}`}
              position={[signal.latitude, signal.longitude]}
              icon={createSignalIcon(signal, isNext)}
              zIndexOffset={isNext ? 1000 : 0}
            >
              <Tooltip
                key={labelsKey}
                permanent={showLabels && isNext}
                direction="right"
                offset={[18, 0]}
              >
                {signalLabel(signal)} · {SIGNAL_STATUS[signal.status].label}
              </Tooltip>
            </Marker>
          );
        })}

        {/* After arrival the ambulance is parked at the hospital marker */}
        {!arrived && ambulance?.latitude != null && ambulance?.longitude != null && (
          <MovingMarker
            key={selectedAmbulanceId}
            position={[ambulance.latitude, ambulance.longitude]}
            heading={ambulance.heading}
            icon={selectedNumber === 1 ? ambulanceIcon : otherAmbulanceIcon(selectedNumber)}
            zIndexOffset={2000}
            trailColor={ambulanceColor(selectedNumber)}
            eventHandlers={{ click: () => onSelectAmbulance?.(selectedAmbulanceId, "chase") }}
          >
            {ambulance.speed != null && ambulance.status !== "COMPLETED" && (
              <Tooltip
                key={labelsKey}
                permanent={showLabels}
                direction="top"
                offset={[0, -22]}
                className="speed-tooltip"
              >
                {otherAmbulances.length > 0 && `${selectedNumber} · `}
                {Math.round(ambulance.speed * 3.6)} km/h
                {legLabel(ambulance.leg, Boolean(pickupPoint), true) &&
                  ` · ${legLabel(ambulance.leg, Boolean(pickupPoint), true)}`}
              </Tooltip>
            )}
          </MovingMarker>
        )}

        {/* The other ambulances */}
        {otherAmbulances.map((item) =>
          item.status === "driving" && item.latitude != null ? (
            <MovingMarker
              key={item.vehicle_id}
              position={[item.latitude, item.longitude]}
              heading={item.heading}
              icon={otherAmbulanceIcon(item.number)}
              zIndexOffset={1900}
              trailColor={ambulanceColor(item.number)}
              eventHandlers={{ click: () => onSelectAmbulance?.(item.vehicle_id, "chase") }}
            >
              <Tooltip
                key={labelsKey}
                permanent={showLabels}
                direction="top"
                offset={[0, -18]}
                className="speed-tooltip"
              >
                {item.number} · {item.level_name}
                {legLabel(item.leg, Boolean(item.pickup_point), true) &&
                  ` · ${legLabel(item.leg, Boolean(item.pickup_point), true)}`}
                {item.give_way ? " · giving way" : ""}
              </Tooltip>
            </MovingMarker>
          ) : null
        )}
      </MapContainer>

      <div className="map-overlay map-overlay-top">{overlay}</div>

      {/* Top right: view switch and follow toggle. The status cards are
          in the side panel (LiveStatusPanel), so they never hide the map. */}
      <div className="map-overlay map-side">
        <div className="map-side-controls">
          <div className="view-toggle" role="group" aria-label="Map view">
            <button
              className={showingMap ? "active" : ""}
              onClick={() => onViewChange("map")}
            >
              Map
            </button>
            <button
              className={!showingMap ? "active" : ""}
              onClick={() => onViewChange("chase")}
              disabled={previewRoutes.length > 0}
            >
              Chase view
            </button>
          </div>

          {showingMap && onCameraChange && (
            <div className="view-toggle camera-toggle" role="group" aria-label="Camera">
              {[
                ["follow", "Follow", "Keep the ambulance in the middle"],
                ["all", "All", "Show all ambulances"],
                ["free", "Free", "Let me move the map myself"],
              ].map(([mode, label, title]) => (
                <button
                  key={mode}
                  className={camera === mode ? "active" : ""}
                  onClick={() => onCameraChange(mode)}
                  title={title}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          {onExpandChange && (
            <button
              className="button button-secondary expand-toggle"
              onClick={() => onExpandChange(!expanded)}
              title={expanded ? "Show the side panel again" : "Make the map bigger"}
            >
              {expanded ? "⤡ Show panel" : "⤢ Expand map"}
            </button>
          )}

        </div>

      </div>

      <div className="map-overlay map-legend-wrap">
        <button
          className={`legend-toggle labels-toggle ${showLabels ? "active" : ""}`}
          onClick={toggleLabels}
          aria-pressed={showLabels}
          title={showLabels ? "Hide the labels on the map (hover a marker to see it)" : "Show every label on the map"}
        >
          {showLabels ? "Labels on" : "Labels off"}
        </button>
        <button
          className="legend-toggle"
          onClick={() => setShowLegend(!showLegend)}
          aria-expanded={showLegend}
        >
          {showLegend ? "Hide legend" : "Legend"}
        </button>
        {showLegend && (
      <div className="map-legend">
        <ul>
          <li className="legend-title">Signals</li>
          {LEGEND.map((key) => (
            <li key={key}>
              <span className={`legend-swatch ${SIGNAL_STATUS[key].className}`} />
              {SIGNAL_STATUS[key].label}
            </li>
          ))}
        </ul>
        <ul>
          <li className="legend-title">Traffic on the route</li>
          <li><span className="legend-line" style={{ background: ROUTE_TRAFFIC_COLORS.free }} />Clear</li>
          <li><span className="legend-line" style={{ background: ROUTE_TRAFFIC_COLORS.slow }} />Slow</li>
          <li><span className="legend-line" style={{ background: ROUTE_TRAFFIC_COLORS.jammed }} />Jammed</li>
          <li><span className="legend-line legend-dashed" style={{ color: NO_SIGNAL_COLOR }} />No signal (police help here)</li>
          <li><span className="legend-line" style={{ background: POLICE_ACTIVE_COLOR }} />Police clearing</li>
          <li><span className="legend-line" style={{ background: POLICE_CLEARED_COLOR }} />Cleared by police</li>
          <li className="legend-title">Cars</li>
          <li><span className="legend-swatch" style={{ background: carColor(10) }} />Moving</li>
          <li><span className="legend-swatch" style={{ background: carColor(0) }} />Stopped</li>
          <li><span className="legend-swatch" style={{ background: PULLED_OVER_COLOR }} />Pulled over (siren)</li>
        </ul>
      </div>
        )}
      </div>
    </div>
  );
}

export default MapView;
