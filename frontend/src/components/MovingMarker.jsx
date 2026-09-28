import { useEffect, useRef, useState } from "react";
import { Marker, Polyline } from "react-leaflet";

// The dashboard gets a new position every 0.5 s. Instead of jumping
// there, the marker glides over a little more than that, so it is
// always moving smoothly.
const GLIDE_MS = 550;

// A jump longer than this (a new trip) is not animated.
const SNAP_METERS = 300;

// Recent positions drawn as a trail behind the vehicle.
const TRAIL_POINTS = 30;

function metersBetween([lat1, lon1], [lat2, lon2]) {
  const x = (lon2 - lon1) * 111320 * Math.cos((lat1 * Math.PI) / 180);
  const y = (lat2 - lat1) * 110540;
  return Math.hypot(x, y);
}

// Direction of travel, degrees clockwise from north.
function bearing([lat1, lon1], [lat2, lon2]) {
  const x = (lon2 - lon1) * Math.cos((lat1 * Math.PI) / 180);
  const y = lat2 - lat1;
  return ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
}

// Icons are drawn facing east (right). Driving west they are mirrored
// instead of turned upside down.
function headingTransform(heading) {
  if (heading == null) return "";
  const h = ((heading % 360) + 360) % 360;
  return h > 180
    ? `rotate(${h - 270}deg) scaleX(-1)`
    : `rotate(${h - 90}deg)`;
}

// A marker that glides between position updates, turns to face where it
// is going (the element with class "map-heading" inside its icon), and
// leaves a short trail. Everything moves through Leaflet directly: no
// React re-render per animation frame.
function MovingMarker({
  position,
  heading,
  icon,
  zIndexOffset,
  trailColor,
  children,
}) {
  const markerRef = useRef(null);
  const trailRef = useRef(null);
  const shown = useRef(position);
  // Leaflet moves the marker after the first render (effect below).
  const [startPosition] = useState(position);
  const [startTrail] = useState(() => (position ? [position] : []));
  const trail = useRef(startTrail);
  const lastHeading = useRef(heading);

  const [latitude, longitude] = position || [];

  useEffect(() => {
    const marker = markerRef.current;
    if (!marker || latitude == null || longitude == null) return undefined;

    const to = [latitude, longitude];
    const from = shown.current || to;
    const distance = metersBetween(from, to);

    // Face the way it is going: the given heading, else from movement.
    if (heading != null) {
      lastHeading.current = heading;
    } else if (distance > 2) {
      lastHeading.current = bearing(from, to);
    }
    const turn = marker.getElement()?.querySelector(".map-heading");
    if (turn) turn.style.transform = headingTransform(lastHeading.current);

    // Trail: restart it after a jump.
    trail.current =
      distance > SNAP_METERS
        ? [to]
        : [...trail.current, to].slice(-TRAIL_POINTS);
    trailRef.current?.setLatLngs(trail.current);

    if (distance > SNAP_METERS) {
      shown.current = to;
      marker.setLatLng(to);
      return undefined;
    }

    const start = performance.now();
    let frame;
    const step = (time) => {
      const t = Math.min(1, (time - start) / GLIDE_MS);
      shown.current = [
        from[0] + (to[0] - from[0]) * t,
        from[1] + (to[1] - from[1]) * t,
      ];
      marker.setLatLng(shown.current);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [latitude, longitude, heading]);

  if (!position) return null;

  return (
    <>
      {trailColor && (
        <Polyline
          ref={trailRef}
          positions={startTrail}
          interactive={false}
          pathOptions={{ color: trailColor, weight: 4, opacity: 0.45 }}
        />
      )}
      {/* The position prop is only the starting point; the effect above
          moves the marker from then on. */}
      <Marker
        ref={markerRef}
        position={startPosition || position}
        icon={icon}
        zIndexOffset={zIndexOffset}
      >
        {children}
      </Marker>
    </>
  );
}

export default MovingMarker;
