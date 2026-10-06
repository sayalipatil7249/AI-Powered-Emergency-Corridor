import { useEffect, useState } from "react";

// Backend address. Override with VITE_API_URL in frontend/.env if needed.
// 127.0.0.1 rather than "localhost": the backend listens on IPv4 only, and
// "localhost" can resolve to IPv6 and reach another app on port 8000.
export const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";
const WS_URL = API_URL.replace(/^http/, "ws");
export const RECONNECT_DELAY_MS = 3000;

// The live simulation state (/simulation/ws, every 0.5 s). Connects, and
// keeps retrying every few seconds if the backend is not up yet or
// restarts. Returns { state, connected }.
export function useLiveState() {
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let websocket = null;
    let reconnectTimer = null;
    let closedByPage = false;

    const connect = () => {
      websocket = new WebSocket(`${WS_URL}/simulation/ws`);

      websocket.onopen = () => {
        setConnected(true);
      };

      websocket.onmessage = (event) => {
        setState(JSON.parse(event.data));
      };

      websocket.onerror = (error) => {
        console.error("WebSocket error:", error);
      };

      websocket.onclose = () => {
        setConnected(false);

        if (!closedByPage) {
          reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
        }
      };
    };

    connect();

    return () => {
      closedByPage = true;
      clearTimeout(reconnectTimer);
      websocket?.close();
    };
  }, []);

  return { state, connected };
}
