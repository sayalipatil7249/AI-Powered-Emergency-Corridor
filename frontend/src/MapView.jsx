import { useEffect, useRef, useState } from "react";
import {
  MapContainer,
  TileLayer,
  Marker,
  Polyline,
  Polygon,
  CircleMarker,
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
  HOSPITAL_ICON,
  JAM_ICON,
  POLICE_ICON,
  SIGNAL_ICON,
  START_ICON,
} from "./icons";
import { SIGNAL_STATUS, signalLabel } from "./routeStatus";
import { ROUTE_TRAFFIC_COLORS, carColor } from "./trafficColors";
import AgentFeed from "./components/AgentFeed";
import ChaseView from "./ChaseView";
import LiveTrafficCard from "./components/LiveTrafficCard";
import ResponseCard from "./components/ResponseCard";

// Colours for live (TomTom) traffic on the map.
const LIVE_COLORS = {
  free: "#22c55e",
  slow: "#f59e0b",
  jammed: "#ef4444",
  closed: "#991b1b",
};

const PUNE_CENTER = [18.523, 73.859];

const LEGEND = ["GREEN", "TURNING", "READY", "WAITING", "PASSED"];

const ambulanceIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-ambulance">${AMBULANCE_ICON}</div>`,
  iconSize: [40, 40],
  iconAnchor: [20, 20],
});

const policeStationIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-place">${POLICE_ICON}</div>`,
  iconSize: [18, 18],
  iconAnchor: [9, 9],
});

// The police unit sent to a jam: bigger, with a pulsing ring.
const policeUnitIcon = L.divIcon({
  className: "map-icon",
  html: `<div class="map-police-unit">${POLICE_ICON}</div>`,
  iconSize: [30, 30],
  iconAnchor: [15, 15],
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
function MapCamera({ route, ambulance, follow, area }) {
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
    const margin = 0.06; // degrees (about 6 km) of room around the area

    map.setMaxBounds([
      [area.south - margin, area.west - margin],
      [area.north + margin, area.east + margin],
    ]);
    map.setMinZoom(12);

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

  return null;
}

function MapView({
  ambulance,
  vehicles = [],
  route = [],
  routeStatuses = [],
  routeTraffic = [],
  policeStations = [],
  response,
  startPoint,
  hospitalPoint,
  overlay,
  agentFeed = [],
  previewSignals = [],
  hospitalName,
  area,
  liveTraffic,
  pickMode = false,
  onPick,
}) {
  const [follow, setFollow] = useState(true);
  // "map": top-down map; "chase": 3D view following the ambulance.
  const [view, setView] = useState("map");

  const nextSignal = routeStatuses.find(
    (signal) => signal.status !== "PASSED"
  );

  const start = startPoint || route[0];
  const hospital = hospitalPoint || route[route.length - 1];
  const arrived = ambulance?.status === "COMPLETED";

  return (
    <div className={`map-wrapper ${pickMode ? "picking" : ""}`}>
      {view === "chase" && (
        <div className="chase-layer">
          <ChaseView
            ambulance={ambulance}
            vehicles={vehicles}
            route={route}
            routeStatuses={routeStatuses}
            routeTraffic={routeTraffic}
            hospitalPoint={hospital}
            hospitalName={hospitalName}
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
                fillColor: "#05070c",
                fillOpacity: 0.55,
              }}
            />
            <Polygon
              positions={areaRing(area)}
              interactive={false}
              pathOptions={{
                color: "#60a5fa",
                weight: 1.5,
                opacity: 0.7,
                dashArray: "6 6",
                fill: false,
              }}
            />
          </>
        )}

        <MapCamera
          route={route}
          ambulance={ambulance}
          follow={follow}
          area={area}
        />
        <MapClickPicker active={pickMode} onPick={onPick} />

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

        {/* Ambulance route */}
        {route.length > 1 && (
          <>
            <Polyline
              positions={route}
              pathOptions={{ color: "#60a5fa", weight: 12, opacity: 0.18 }}
            />
            <Polyline
              positions={route}
              pathOptions={{ color: "#60a5fa", weight: 5, opacity: 0.95 }}
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

        {start && (
          <Marker position={start} icon={startIcon}>
            <Tooltip direction="top" offset={[0, -12]}>
              Start
            </Tooltip>
          </Marker>
        )}

        {hospital && (
          <Marker position={hospital} icon={hospitalIcon} zIndexOffset={3000}>
            {/* Left, so it never collides with signal labels (drawn right) */}
            <Tooltip permanent direction="left" offset={[-16, 0]}>
              {hospitalName}
            </Tooltip>
          </Marker>
        )}

        {/* Police stations that can be sent to clear a jam */}
        {policeStations.map((station) => (
          <Marker
            key={`police-${station.name}`}
            position={[station.latitude, station.longitude]}
            icon={policeStationIcon}
          >
            <Tooltip direction="top" offset={[0, -8]}>
              {station.name}
            </Tooltip>
          </Marker>
        ))}

        {/* Deadlock ahead that the AI responded to */}
        {response?.jam &&
          ["police_en_route", "police_clearing"].includes(response.status) && (
            <Marker
              position={[response.jam.latitude, response.jam.longitude]}
              icon={jamIcon}
              zIndexOffset={1500}
            >
              <Tooltip permanent direction="bottom" offset={[0, 12]}>
                Deadlock: {response.jam.name}
              </Tooltip>
            </Marker>
          )}

        {/* The police unit on its way / at the jam */}
        {response?.police && response.police.stage !== "done" && (
          <Marker
            position={[response.police.latitude, response.police.longitude]}
            icon={policeUnitIcon}
            zIndexOffset={1800}
          >
            <Tooltip permanent direction="top" offset={[0, -16]}>
              {response.police.stage === "clearing"
                ? "Police clearing traffic"
                : `Police · ${Math.round(response.police.eta_seconds)} s away`}
            </Tooltip>
          </Marker>
        )}

        {/* Other vehicles */}
        {vehicles.map((vehicle) =>
          vehicle.latitude == null || vehicle.longitude == null ? null : (
            <CircleMarker
              key={vehicle.vehicle_id}
              center={[vehicle.latitude, vehicle.longitude]}
              radius={3.5}
              pathOptions={{
                color: "#0b1120",
                fillColor: vehicle.vehicle_id.startsWith("police")
                  ? "#3b82f6"
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
                permanent={isNext}
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
          <Marker
            position={[ambulance.latitude, ambulance.longitude]}
            icon={ambulanceIcon}
            zIndexOffset={2000}
          >
            {ambulance.speed != null && ambulance.status !== "COMPLETED" && (
              <Tooltip
                permanent
                direction="top"
                offset={[0, -22]}
                className="speed-tooltip"
              >
                {Math.round(ambulance.speed * 3.6)} km/h
              </Tooltip>
            )}
          </Marker>
        )}
      </MapContainer>

      <div className="map-overlay map-overlay-top">{overlay}</div>

      <div className="map-overlay map-controls">
        <div className="view-toggle" role="group" aria-label="Map view">
          <button
            className={view === "map" ? "active" : ""}
            onClick={() => setView("map")}
          >
            Map
          </button>
          <button
            className={view === "chase" ? "active" : ""}
            onClick={() => setView("chase")}
          >
            Chase view
          </button>
        </div>

        {view === "map" && (
          <label className="follow-toggle">
            <input
              type="checkbox"
              checked={follow}
              onChange={(event) => setFollow(event.target.checked)}
            />
            Follow ambulance
          </label>
        )}

        <LiveTrafficCard live={liveTraffic} tripRunning={Boolean(ambulance)} />
        {ambulance && ambulance.status !== "COMPLETED" && (
          <ResponseCard response={response} />
        )}
      </div>

      <div className="map-overlay map-overlay-agent">
        <AgentFeed
          messages={agentFeed}
          departTime={ambulance?.depart_time}
        />
      </div>

      <div className="map-overlay map-legend">
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
          <li className="legend-title">Route traffic</li>
          <li><span className="legend-line" style={{ background: ROUTE_TRAFFIC_COLORS.free }} />Clear</li>
          <li><span className="legend-line" style={{ background: ROUTE_TRAFFIC_COLORS.slow }} />Slow</li>
          <li><span className="legend-line" style={{ background: ROUTE_TRAFFIC_COLORS.jammed }} />Jammed</li>
          <li className="legend-title">Cars</li>
          <li><span className="legend-swatch" style={{ background: carColor(10) }} />Moving</li>
          <li><span className="legend-swatch" style={{ background: carColor(0) }} />Stopped</li>
        </ul>
      </div>
    </div>
  );
}

export default MapView;
