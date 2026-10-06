// A section that can be folded away: the main screen shows what is
// needed to watch the simulation; details open on demand.
function Collapsible({ title, hint, defaultOpen = false, className = "", children }) {
  return (
    <details className={`collapsible ${className}`} open={defaultOpen || undefined}>
      <summary>
        <span>{title}</span>
        {hint && <span className="muted">{hint}</span>}
      </summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}

export default Collapsible;
