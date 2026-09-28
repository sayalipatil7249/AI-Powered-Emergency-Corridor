// Small inline SVG icons (strings) used by the map markers
// and, through components/SvgIcon.jsx, by the React components.

export const CHECK_ICON = `
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"
  stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M5 12.5l4.5 4.5L19 7.5" />
</svg>`;

// A traffic light: dark housing with red, amber and green lamps.
export const SIGNAL_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <rect x="7" y="1.5" width="10" height="21" rx="3" fill="#111827" stroke="#e5e7eb" stroke-width="1.5" />
  <circle cx="12" cy="6.5" r="2.3" fill="#ef4444" />
  <circle cx="12" cy="12" r="2.3" fill="#f59e0b" />
  <circle cx="12" cy="17.5" r="2.3" fill="#22c55e" />
</svg>`;

// Police: a blue shield with a white star.
export const POLICE_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <path d="M12 1.8l8.2 3.2v6.2c0 5.1-3.5 9.2-8.2 11-4.7-1.8-8.2-5.9-8.2-11V5z"
    fill="#1d4ed8" stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round" />
  <path d="M12 6.8l1.5 3.1 3.4.4-2.5 2.3.7 3.4-3.1-1.7-3.1 1.7.7-3.4-2.5-2.3 3.4-.4z"
    fill="#ffffff" />
</svg>`;

// Deadlock ahead: an amber warning triangle.
export const JAM_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <path d="M12 2.5l10 18H2z" fill="#f59e0b" stroke="#0a0f1a" stroke-width="1.4"
    stroke-linejoin="round" />
  <path d="M11 9h2v6h-2zm0 7.6h2v2h-2z" fill="#0a0f1a" />
</svg>`;

export const HOSPITAL_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <rect x="2" y="2" width="20" height="20" rx="5" fill="#dc2626" />
  <path d="M10 6h4v4h4v4h-4v4h-4v-4H6v-4h4z" fill="#ffffff" />
</svg>`;

export const START_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <circle cx="12" cy="12" r="10" fill="#0f172a" stroke="#ffffff" stroke-width="3" />
  <circle cx="12" cy="12" r="4" fill="#ffffff" />
</svg>`;

export const AMBULANCE_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <path d="M2 7.5A1.5 1.5 0 0 1 3.5 6H14v10H2z" fill="#ffffff" />
  <path d="M14 9h3.6l3.4 3.6V16h-7z" fill="#ffffff" />
  <path d="M7 8h2v2h2v2H9v2H7v-2H5v-2h2z" fill="#dc2626" />
  <circle cx="6" cy="17" r="2" fill="#0f172a" stroke="#ffffff" stroke-width="1.2" />
  <circle cx="17" cy="17" r="2" fill="#0f172a" stroke="#ffffff" stroke-width="1.2" />
</svg>`;

// Simulated accident: a red octagon with an exclamation mark.
export const CRASH_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <path d="M8 1.8h8l6.2 6.2v8L16 22.2H8L1.8 16V8z" fill="#dc2626"
    stroke="#ffffff" stroke-width="1.6" stroke-linejoin="round" />
  <path d="M10.8 6h2.4l-.4 8h-1.6zm0 9.6h2.4V18h-2.4z" fill="#ffffff" />
</svg>`;

// A road the police cleared: a teal circle with a tick.
export const CLEARED_ICON = `
<svg viewBox="0 0 24 24" aria-hidden="true">
  <circle cx="12" cy="12" r="10" fill="#0d9488" stroke="#ffffff" stroke-width="1.6" />
  <path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#ffffff" stroke-width="2.4"
    stroke-linecap="round" stroke-linejoin="round" />
</svg>`;
