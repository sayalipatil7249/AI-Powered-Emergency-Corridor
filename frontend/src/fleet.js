// Several ambulances: colours to tell them apart, and priority levels.

// Ambulance 1 keeps the dashboard's red; the others get their own colour
// (marker ring, route line, list dot).
const AMBULANCE_COLORS = [
  "#ef4444", // red
  "#a855f7", // purple
  "#ec4899", // pink
  "#65a30d", // green
  "#22d3ee", // cyan
  "#818cf8", // indigo
  "#d946ef", // magenta
  "#fdba74", // peach
  "#7dd3fc", // sky
  "#a16207", // brown
];

export function ambulanceColor(number) {
  return AMBULANCE_COLORS[(number - 1) % AMBULANCE_COLORS.length];
}

// corridor/priority.py levels: 1 Critical, 2 Urgent, 3 Stable.
export const LEVEL_CLASS = { 1: "critical", 2: "urgent", 3: "stable" };

// The patient conditions the crew can pick (GET /fleet/conditions);
// used until that list has loaded. Same as corridor/priority.py.
export const DEFAULT_CONDITIONS = [
  { condition: "cardiac_arrest", condition_label: "Heart stopped (cardiac arrest)", level: 1, level_name: "Critical" },
  { condition: "heart_attack", condition_label: "Heart attack", level: 1, level_name: "Critical" },
  { condition: "stroke", condition_label: "Stroke", level: 1, level_name: "Critical" },
  { condition: "breathing", condition_label: "Can't breathe properly", level: 1, level_name: "Critical" },
  { condition: "unconscious", condition_label: "Unconscious", level: 1, level_name: "Critical" },
  { condition: "major_trauma", condition_label: "Serious road accident", level: 1, level_name: "Critical" },
  { condition: "severe_bleeding", condition_label: "Heavy bleeding", level: 1, level_name: "Critical" },
  { condition: "severe_burns", condition_label: "Bad burns", level: 1, level_name: "Critical" },
  { condition: "pregnancy_emergency", condition_label: "Pregnancy problem", level: 1, level_name: "Critical" },
  { condition: "poisoning", condition_label: "Poisoning or overdose", level: 1, level_name: "Critical" },
  { condition: "seizure", condition_label: "Fit (seizure)", level: 1, level_name: "Critical" },
  { condition: "chest_pain", condition_label: "Chest pain", level: 2, level_name: "Urgent" },
  { condition: "serious", condition_label: "Serious injury or illness", level: 2, level_name: "Urgent" },
  { condition: "fracture", condition_label: "Broken bone", level: 2, level_name: "Urgent" },
  { condition: "labour", condition_label: "In labour", level: 2, level_name: "Urgent" },
  { condition: "allergic_reaction", condition_label: "Allergic reaction", level: 2, level_name: "Urgent" },
  { condition: "diabetic", condition_label: "Sugar (diabetes) emergency", level: 2, level_name: "Urgent" },
  { condition: "abdominal_pain", condition_label: "Bad stomach pain", level: 2, level_name: "Urgent" },
  { condition: "high_fever", condition_label: "High fever", level: 2, level_name: "Urgent" },
  { condition: "child_illness", condition_label: "Sick child", level: 2, level_name: "Urgent" },
  { condition: "minor_injury", condition_label: "Small injury", level: 3, level_name: "Stable" },
  { condition: "stable", condition_label: "Stable patient", level: 3, level_name: "Stable" },
  { condition: "transfer", condition_label: "Move to another hospital", level: 3, level_name: "Stable" },
  { condition: "discharge", condition_label: "Going home from hospital", level: 3, level_name: "Stable" },
];

export const DEFAULT_CONDITION = "serious";

// Where an ambulance is in its journey (base -> patient -> hospital).
// Trips without a pickup only ever drive to the hospital: no label.
export function legLabel(leg, hasPickup, short = false) {
  if (!hasPickup) return "";
  if (short) {
    return { to_patient: "→ patient", at_patient: "🧍 loading", to_hospital: "→ hospital" }[leg] || "";
  }
  return {
    to_patient: "Going to patient",
    at_patient: "Picking up patient",
    to_hospital: "Taking patient to hospital",
  }[leg] || "";
}

// What an ambulance is doing right now, in a few words, for the
// ambulance lists and map labels: { text, short, tone }.
// tone: "patient" (going to / at the patient), "hospital" (patient on
// board), "handover", "free" (handed over), "waiting".
export function ambulanceStage(ambulance) {
  if (!ambulance) return null;
  const hasPickup = Boolean(ambulance.pickup_point);
  if (ambulance.status === "waiting") {
    return { text: "Not left yet", short: "waiting", tone: "waiting" };
  }
  if (ambulance.status === "arrived") {
    if (ambulance.stage === "handover") {
      return { text: "At hospital · handing patient to doctors", short: "handover", tone: "handover" };
    }
    if (ambulance.stage === "returning") {
      return { text: "Patient handed over · driving back to base", short: "returning", tone: "free" };
    }
    if (ambulance.stage === "available") {
      return { text: "Patient handed over · back at base", short: "free", tone: "free" };
    }
    return { text: "Patient handed over", short: "done", tone: "free" };
  }
  if (hasPickup && ambulance.leg === "to_patient") {
    return { text: "Going to the patient", short: "→ patient", tone: "patient" };
  }
  if (hasPickup && ambulance.leg === "at_patient") {
    return { text: "Picking up the patient (about 3 min)", short: "picking up", tone: "patient" };
  }
  return { text: "Patient on board · going to hospital", short: "→ hospital", tone: "hospital" };
}
