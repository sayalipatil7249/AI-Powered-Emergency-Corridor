import { useEffect } from "react";
import {
  CircleMarker,
  MapContainer,
  Polyline,
  TileLayer,
  Tooltip,
  useMap,
} from "react-leaflet";
import "leaflet/dist/leaflet.css";

import { ambulanceColor, ambulanceStage } from "../fleet";

const PUNE_CENTER = [18.523, 73.859];

// Fit the map to every ambulance route once routes appear.
function FitRoutes({ routes }) {
  const map = useMap();
  const key = routes.map((route) => route.length).join(",");

  useEffect(() => {
    const points = routes.flat();
    if (points.length > 1) {
      map.fitBounds(points, { padding: [30, 30] });
    }
    // Only when the set of routes changes, not on every position update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, key]);

  return null;
}

// Every ambulance and its route, and the police units on their way.
function AdminMap({ ambulances, policeAlerts, selectedId, onSelect }) {
  const active = ambulances.filter((item) => item.status !== "arrived");

  return (
    <MapContainer center={PUNE_CENTER} zoom={14} className="cr-map" preferCanvas>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        maxZoom={19}
      />
      <FitRoutes routes={ambulances.map((item) => item.route || [])} />

      {active.map((item) =>
        item.route?.length > 1 ? (
          <Polyline
            key={`route-${item.vehicle_id}`}
            positions={item.route}
            eventHandlers={{ click: () => onSelect(item.vehicle_id) }}
            pathOptions={{
              color: ambulanceColor(item.number),
              weight: item.vehicle_id === selectedId ? 6 : 4,
              opacity: item.vehicle_id === selectedId ? 0.95 : 0.6,
            }}
          />
        ) : null
      )}

      {policeAlerts
        .filter((alert) => alert.unit_latitude != null)
        .map((alert) => (
          <CircleMarker
            key={`unit-${alert.alert_id}`}
            center={[alert.unit_latitude, alert.unit_longitude]}
            radius={6}
            pathOptions={{ color: "#ffffff", fillColor: "#3b82f6", fillOpacity: 1, weight: 2 }}
          >
            <Tooltip direction="top">Police · {alert.station}</Tooltip>
          </CircleMarker>
        ))}

      {active.map((item) =>
        item.latitude != null ? (
          <CircleMarker
            key={item.vehicle_id}
            center={[item.latitude, item.longitude]}
            radius={item.vehicle_id === selectedId ? 10 : 8}
            eventHandlers={{ click: () => onSelect(item.vehicle_id) }}
            pathOptions={{
              color: "#ffffff",
              fillColor: ambulanceColor(item.number),
              fillOpacity: 1,
              weight: 2,
            }}
          >
            <Tooltip permanent direction="top" offset={[0, -8]}>
              {item.number} · {item.level_name} · {ambulanceStage(item)?.short}
              {item.give_way ? " · giving way" : ""}
            </Tooltip>
          </CircleMarker>
        ) : null
      )}
    </MapContainer>
  );
}

export default AdminMap;
