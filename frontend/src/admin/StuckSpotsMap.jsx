import { useEffect } from "react";
import { CircleMarker, MapContainer, TileLayer, Tooltip, useMap } from "react-leaflet";
import "leaflet/dist/leaflet.css";

import { formatDuration } from "../routeStatus";

const PUNE_CENTER = [18.523, 73.859];

// Zoom to fit every spot once they load.
function FitSpots({ spots }) {
  const map = useMap();
  useEffect(() => {
    if (spots.length > 1) {
      map.fitBounds(spots.map((spot) => [spot.latitude, spot.longitude]), { padding: [30, 30] });
    }
  }, [map, spots]);
  return null;
}

// Where ambulances get stuck: nearby stops grouped into spots by the
// database (PostGIS, GET /admin/stuck-spots). Bigger and redder circles
// lost more time.
function StuckSpotsMap({ spots = [] }) {
  const worst = Math.max(1, ...spots.map((spot) => spot.stopped_seconds));

  return (
    <section className="admin-card">
      <div className="admin-card-heading">
        <div>
          <h2>Where ambulances get stuck</h2>
          <p className="muted">
            Stops within 80 m of each other grouped into one spot. Bigger and
            redder circles lost more time. Hover a circle for details.
          </p>
        </div>
      </div>
      {spots.length === 0 ? (
        <p className="admin-empty">No stops recorded in this period.</p>
      ) : (
        <MapContainer center={PUNE_CENTER} zoom={14} className="stuck-map" preferCanvas>
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            maxZoom={19}
          />
          <FitSpots spots={spots} />
          {spots.map((spot) => {
            const share = spot.stopped_seconds / worst;
            return (
              <CircleMarker
                key={`${spot.latitude},${spot.longitude}`}
                center={[spot.latitude, spot.longitude]}
                radius={8 + share * 22}
                pathOptions={{
                  color: "#ffffff",
                  weight: 2,
                  fillColor: share > 0.6 ? "#dc2626" : share > 0.25 ? "#ea7a1a" : "#d4a72c",
                  fillOpacity: 0.8,
                }}
              >
                <Tooltip direction="top">
                  <strong>{spot.name}</strong>
                  <br />
                  {formatDuration(spot.stopped_seconds)} stood still · {spot.stops} stops ·{" "}
                  {spot.trips} {spot.trips === 1 ? "trip" : "trips"}
                </Tooltip>
              </CircleMarker>
            );
          })}
        </MapContainer>
      )}
    </section>
  );
}

export default StuckSpotsMap;
