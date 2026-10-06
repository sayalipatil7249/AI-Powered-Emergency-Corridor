// The patient's condition, grouped by priority level. Used by the
// dispatcher (trip planner) and by each ambulance's crew (fleet panel).
function ConditionSelect({ id, conditions, value, onChange, disabled }) {
  const levels = [...new Set(conditions.map((item) => item.level))];

  return (
    <select
      id={id}
      className="condition-select"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
    >
      {levels.map((level) => {
        const items = conditions.filter((item) => item.level === level);
        return (
          <optgroup key={level} label={items[0].level_name}>
            {items.map((item) => (
              <option key={item.condition} value={item.condition}>
                {item.condition_label}
              </option>
            ))}
          </optgroup>
        );
      })}
    </select>
  );
}

export default ConditionSelect;
