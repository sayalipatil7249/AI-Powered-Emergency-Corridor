"""
Patient priority for ambulances sharing the city's signals.

The medic crew sets it by choosing the patient's condition (they know the
severity best); it applies at once and every change is logged. A
condition, not a bare "critical" button: it is specific, checkable
afterwards, and the other crew can be told why they give way.

The junction referee (corridor/referee.py) weighs the ambulances with
weight(): the level, plus the time an ambulance has already stood still
on this trip, so a lower-priority ambulance is not made to give way at
junction after junction.
"""

# Condition -> (label shown to people, priority level: 1 = most urgent).
# The common emergency call types of Indian ambulance services, in three
# levels. The same list serves an ambulance going to a patient (the
# condition reported by the caller) and one carrying the patient.
CONDITIONS = {
    # Critical: life-threatening, every second counts.
    "cardiac_arrest": ("Heart stopped (cardiac arrest)", 1),
    "heart_attack": ("Heart attack", 1),
    "stroke": ("Stroke", 1),
    "breathing": ("Can't breathe properly", 1),
    "unconscious": ("Unconscious", 1),
    "major_trauma": ("Serious road accident", 1),
    "severe_bleeding": ("Heavy bleeding", 1),
    "severe_burns": ("Bad burns", 1),
    "pregnancy_emergency": ("Pregnancy problem", 1),
    "poisoning": ("Poisoning or overdose", 1),
    "seizure": ("Fit (seizure)", 1),
    # Urgent: serious, needs a hospital soon.
    "chest_pain": ("Chest pain", 2),
    "serious": ("Serious injury or illness", 2),
    "fracture": ("Broken bone", 2),
    "labour": ("In labour", 2),
    "allergic_reaction": ("Allergic reaction", 2),
    "diabetic": ("Sugar (diabetes) emergency", 2),
    "abdominal_pain": ("Bad stomach pain", 2),
    "high_fever": ("High fever", 2),
    "child_illness": ("Sick child", 2),
    # Stable: no immediate danger.
    "minor_injury": ("Small injury", 3),
    "stable": ("Stable patient", 3),
    "transfer": ("Move to another hospital", 3),
    "discharge": ("Going home from hospital", 3),
}

LEVEL_NAMES = {1: "Critical", 2: "Urgent", 3: "Stable"}

# How much one second of delay counts, per level.
LEVEL_WEIGHTS = {1: 3.0, 2: 2.0, 3: 1.0}

# Waiting matters within a priority level, but must never make a stable
# patient outrank an urgent or critical patient.
AGING_SECONDS = 300
MAX_AGING_BONUS = 0.9

# The dispatcher's default when no condition is given.
DEFAULT_CONDITION = "serious"


def check_condition(condition):
    """The condition id, or ValueError listing the valid ones."""
    if condition not in CONDITIONS:
        raise ValueError(
            f"Unknown condition '{condition}'. Choose one of: "
            + ", ".join(CONDITIONS)
        )
    return condition


def level(condition):
    return CONDITIONS[condition][1]


def describe(condition):
    """{"condition", "condition_label", "level", "level_name"}."""
    label, number = CONDITIONS[condition]
    return {
        "condition": condition,
        "condition_label": label,
        "level": number,
        "level_name": LEVEL_NAMES[number],
    }


def weight(condition, seconds_stood_still=0.0):
    """How much a second of this ambulance's delay counts."""
    return LEVEL_WEIGHTS[level(condition)] + min(
        MAX_AGING_BONUS, max(0.0, seconds_stood_still) / AGING_SECONDS
    )


def condition_list():
    """Every condition for the crew's picker, most urgent first."""
    return sorted(
        (describe(condition) for condition in CONDITIONS),
        key=lambda item: item["level"],
    )
