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
                ? `Police managing traffic (${zone.station}) · ${zone.vehicles_waved} vehicles waved through`
                : `Cleared by police (${zone.station}) · ${zoneSummary(zone)}`}
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
              permanent={incident.cleared_at == null}
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
            <Tooltip permanent direction="bottom" offset={[0, 12]}>
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
              <Tooltip permanent direction="top" offset={[0, -16]}>
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
              <Tooltip permanent direction="bottom" offset={[0, 12]}>
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
            <Tooltip permanent direction="top" offset={[0, -16]}>
              {response.police.stage === "clearing"
                ? "Police clearing traffic"
                : `Police · ${Math.round(response.police.eta_seconds)} s away`}
            </Tooltip>
          </MovingMarker>
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
                color: "#0b1120",
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
          <MovingMarker
            position={[ambulance.latitude, ambulance.longitude]}
            heading={ambulance.heading}
            icon={ambulanceIcon}
            zIndexOffset={2000}
            trailColor="#f87171"
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
          </MovingMarker>
        )}
      </MapContainer>

      <div className="map-overlay map-overlay-top">{overlay}</div>

      {/* Top right: view switch and follow toggle. The status cards are
          in the side panel (LiveStatusPanel), so they never hide the map. */}
      <div className="map-overlay map-side">
        <div className="map-side-controls">
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

        </div>
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
          <li><span className="legend-line legend-dashed" style={{ color: NO_SIGNAL_COLOR }} />No signal (police)</li>
          <li><span className="legend-line" style={{ background: POLICE_ACTIVE_COLOR }} />Police managing</li>
          <li><span className="legend-line" style={{ background: POLICE_CLEARED_COLOR }} />Cleared by police</li>
          <li className="legend-title">Cars</li>
          <li><span className="legend-swatch" style={{ background: carColor(10) }} />Moving</li>
          <li><span className="legend-swatch" style={{ background: carColor(0) }} />Stopped</li>
          <li><span className="legend-swatch" style={{ background: PULLED_OVER_COLOR }} />Pulled over (siren)</li>
        </ul>
      </div>
    </div>
  );
}

export default MapView;
