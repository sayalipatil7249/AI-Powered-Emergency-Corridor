import { useEffect, useState } from "react";

// Light or dark theme: chosen with the header switch and remembered in
// this browser. index.css holds both colour sets (data-theme="dark").
const KEY = "theme";
const EVENT = "themechange";

export function savedTheme() {
  try {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
}

export function setTheme(theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // Private window: the choice lasts until the page is closed.
  }
  applyTheme(theme);
  window.dispatchEvent(new Event(EVENT));
}

// The current theme; every component using it updates on a switch.
export function useTheme() {
  const [theme, setCurrent] = useState(
    () => document.documentElement.dataset.theme || savedTheme()
  );
  useEffect(() => {
    const update = () => setCurrent(document.documentElement.dataset.theme || "light");
    window.addEventListener(EVENT, update);
    return () => window.removeEventListener(EVENT, update);
  }, []);
  return theme;
}
