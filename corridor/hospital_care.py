"""
Which hospitals can treat which patients, and whether they can take one
right now.

OpenStreetMap only gives hospital names, so each hospital's type is
worked out from its name: the big hospitals with a full emergency
department are listed by name; specialists are recognised by words in
their names ("Eye", "Maternity", "Children"...); other hospitals are
small general hospitals; clinics, diagnostic centres and narrow
specialists (eye, ENT, urology...) take no emergencies. This is an
approximation for the simulation, not real hospital data.

What a patient needs is more specific than "a bed": a heart attack
needs a free cath lab, a stroke a CT scan and a neurologist, a road
accident the trauma team (NEEDS). Each hospital type has a number of
each (CAPACITY); some are already in use when the simulation starts
(random, BUSY_SHARE) and each ambulance sent there takes one. The 108
call centre pre-alerts the hospital; one that can't take the patient
says so and the next suitable one is asked, as a dispatcher would.
All of this is simulated, not real hospital data.

Ownership: 108 ambulances take patients to government hospitals by
default (free treatment); the family can ask for a private one.
GOVERNMENT_HOSPITALS lists the government / municipal hospitals by name.
"""

import random

import re

# Hospitals with a full emergency department (trauma, cardiac, stroke,
# maternity, children): words that identify them in the OSM names.
MAJOR_HOSPITALS = (
    "sassoon", "sasoon", "ruby hall", "jehangir", "kem hospital",
    "poona hospital", "sahyadri hospital", "inlaks", "sadhu vaswani",
    "kamla nehru", "navalmal firodiya", "nityashraddha", "west valley",
    "deendayal memorial", "joshi hospital", "sant dnyaneswar",
    "rajiv gandhi hospital",
)

# Specialists, by words in the name (checked in this order).
SPECIALIST_WORDS = (
    ("cardiac", ("cardiolog", "heart", "cardiac")),
    ("children", ("child", "shaishav", "paediatric", "pediatric")),
    ("maternity", ("maternity", "prasuti", "matru", "sutika", "oyster and pearl")),
    ("ortho", ("sancheti", "ortho")),
    ("eye", ("eye", "vision", "netra", "laser cen")),
    ("no_emergency", (
        "clinic", "diagnostic", "metropolis", "laparoscop", "laser cure",
        "fertility", "urology", "ent hospital", "laproscopy", "dr. rakhi",
    )),
)

TYPE_LABELS = {
    "major": "Big hospital",
    "general": "Small hospital",
    "cardiac": "Heart hospital",
    "children": "Children's hospital",
    "maternity": "Maternity hospital",
    "ortho": "Bone hospital",
    "eye": "Eye hospital",
    "no_emergency": "Clinic (no emergencies)",
}

# Government and municipal (PMC) hospitals, by words in their names.
GOVERNMENT_HOSPITALS = (
    "sassoon", "sasoon", "kamla nehru", "rajiv gandhi hospital", "naydu", "naidu",
)

# What a patient needs at the hospital (default "emergency").
RESOURCES = {
    "emergency": "Emergency doctor",
    "icu": "ICU bed",
    "cath_lab": "Heart team (cath lab)",
    "neuro": "Brain scan and doctor",
    "trauma": "Accident team",
    "obstetric": "Pregnancy team",
    "labour_room": "Labour room",
    "paediatric": "Children's doctor",
    "ortho": "Bone doctor",
}

NEEDS = {
    "cardiac_arrest": "icu",
    "heart_attack": "cath_lab",
    "stroke": "neuro",
    "breathing": "icu",
    "unconscious": "icu",
    "major_trauma": "trauma",
    "severe_bleeding": "trauma",
    "severe_burns": "icu",
    "pregnancy_emergency": "obstetric",
    "poisoning": "icu",
    "seizure": "neuro",
    "fracture": "ortho",
    "labour": "labour_room",
    "child_illness": "paediatric",
}

# How many patients each hospital type can take at once, per resource.
CAPACITY = {
    "major": {"emergency": 8, "icu": 3, "cath_lab": 1, "neuro": 1, "trauma": 2,
              "obstetric": 2, "labour_room": 2, "paediatric": 2, "ortho": 2},
    "cardiac": {"emergency": 3, "icu": 2, "cath_lab": 1},
    "children": {"emergency": 3, "paediatric": 3},
    "maternity": {"emergency": 2, "obstetric": 2, "labour_room": 3},
    "ortho": {"emergency": 2, "ortho": 3},
    "general": {"emergency": 3, "ortho": 1, "labour_room": 1, "paediatric": 1},
    "eye": {},
    "no_emergency": {},
}

# Share of each resource already in use when a simulation starts.
BUSY_SHARE = 0.3

# Which hospital types can take a patient with each condition
# (corridor/priority.py), best first.
_ALL_ROUND = ("major", "general")
CAN_TREAT = {
    "cardiac_arrest": ("major", "cardiac"),
    "heart_attack": ("major", "cardiac"),
    "stroke": ("major",),
    "breathing": ("major",),
    "unconscious": ("major",),
    "major_trauma": ("major",),
    "severe_bleeding": ("major",),
    "severe_burns": ("major",),
    "pregnancy_emergency": ("major", "maternity"),
    "poisoning": ("major",),
    "seizure": ("major",),
    "chest_pain": ("major", "cardiac", "general"),
    "serious": _ALL_ROUND,
    "fracture": ("major", "ortho", "general"),
    "labour": ("maternity", "major", "general"),
    "allergic_reaction": _ALL_ROUND,
    "diabetic": _ALL_ROUND,
    "abdominal_pain": _ALL_ROUND,
    "high_fever": _ALL_ROUND,
    "child_illness": ("children", "major", "general"),
    "minor_injury": ("general", "major", "ortho"),
    "stable": ("general", "major", "cardiac", "children", "maternity", "ortho"),
    "transfer": ("general", "major", "cardiac", "children", "maternity", "ortho"),
    "discharge": ("general", "major", "cardiac", "children", "maternity", "ortho"),
}


def hospital_type(name):
    """The type of a hospital from its name (see the module notes)."""
    text = re.sub(r"\s+", " ", name.lower())
    if any(words in text for words in MAJOR_HOSPITALS):
        return "major"
    for kind, words in SPECIALIST_WORDS:
        if any(word in text for word in words):
            return kind
    return "general"


def can_treat(name, condition):
    return hospital_type(name) in CAN_TREAT.get(condition, _ALL_ROUND)


def ownership(name):
    """"government" or "private"."""
    text = name.lower()
    return "government" if any(word in text for word in GOVERNMENT_HOSPITALS) else "private"


def need(condition):
    """The resource a patient with this condition needs."""
    return NEEDS.get(condition, "emergency")


def describe(name):
    kind = hospital_type(name)
    owner = ownership(name)
    return {
        "type": kind,
        "type_label": TYPE_LABELS[kind],
        "ownership": owner,
        "ownership_label": "Government" if owner == "government" else "Private",
    }


class HospitalBoard:
    """
    What each hospital can take in one simulation run. Holders (an
    ambulance id or a booking) each hold one unit of one resource at one
    hospital; a hospital closed for a resource (e.g. its cath lab just
    became busy) takes nobody for it.
    """

    def __init__(self):
        self.seed = random.randrange(1_000_000)
        self.holds = {}      # holder -> (hospital name, resource)
        self.closed = set()  # (hospital name, resource)

    def reset(self):
        """A new run: nothing held, nothing closed (same hospitals' load)."""
        self.holds.clear()
        self.closed.clear()

    def reshuffle(self):
        """New random load for the next run."""
        self.seed = random.randrange(1_000_000)
        self.reset()

    def _busy_at_start(self, name, resource, capacity):
        rng = random.Random(f"{self.seed}|{name}|{resource}")
        return sum(rng.random() < BUSY_SHARE for _ in range(capacity))

    def free(self, name, resource, extra_taken=0, holder=None):
        """Free units of a resource (not counting holder's own)."""
        if (name, resource) in self.closed:
            return 0
        capacity = CAPACITY[hospital_type(name)].get(resource, 0)
        held = sum(
            1 for who, place in self.holds.items()
            if place == (name, resource) and who != holder
        )
        return max(0, capacity - self._busy_at_start(name, resource, capacity)
                   - held - extra_taken)

    def accepts(self, name, condition, holder=None):
        return can_treat(name, condition) and self.free(name, need(condition), holder=holder) > 0

    def hold(self, holder, name, condition):
        """holder takes the resource its patient needs (releasing any other)."""
        self.holds[holder] = (name, need(condition))

    def release(self, holder):
        self.holds.pop(holder, None)

    def close(self, name, resource):
        self.closed.add((name, resource))
