"""
108 dispatch: which ambulance goes to a patient.

Like Maharashtra's 108 service, ambulances wait at stations across the
city (here: at hospitals) and come in two kinds:

    ALS  Advanced Life Support: paramedic, ventilator, cardiac monitor
    BLS  Basic Life Support: first aid and oxygen

The call centre sends the free ambulance that can reach the patient
fastest (by driving time, not straight-line distance). A critical
patient gets an ALS ambulance, unless one is a lot slower than the
nearest BLS (then the BLS goes: minutes matter more); others get the
fastest BLS, so the few ALS ambulances stay free for critical calls,
unless an ALS one is a lot faster.

The fleet is made up for the simulation (stations and kinds), not the
real 108 deployment in Pune.
"""

import math

# (unit id, kind, station hospital), spread over the simulated area.
UNITS = (
    ("108-A1", "ALS", "B.J.Medical College and Sassoon Hospital"),
    ("108-A2", "ALS", "Joshi Hospital"),
    ("108-A3", "ALS", "Jehangir Hospital"),
    ("108-B1", "BLS", "Kamla Nehru Hospital"),
    ("108-B2", "BLS", "Deccan Hardikar Hospital"),
    ("108-B3", "BLS", "Modern Hospital"),
    ("108-B4", "BLS", "Dr. Rajendra Mehata Hospital"),
    ("108-B5", "BLS", "Yashda Hospital"),
    ("108-B6", "BLS", "Surya Sahyadri Hospital"),
)

KIND_LABELS = {"ALS": "Advanced ambulance", "BLS": "Basic ambulance"}

# A non-critical patient gets an ALS ambulance only when it is this much
# faster than the fastest BLS one (s).
ALS_SAVING_SECONDS = 120

# A critical patient waits at most this much longer for an ALS ambulance
# than for the nearest BLS one (s).
ALS_WAIT_LIMIT_SECONDS = 180

# Ambulances compared by driving time (the nearest free ones).
CANDIDATES = 5


def units(hospitals):
    """The fleet with station positions: [{"id", "kind", "station",
    "latitude", "longitude"}]. Stations missing from hospitals (another
    simulated area) are left out; with too few, every big hospital gets
    an ambulance (ALS) instead."""

    by_name = {item["name"]: item for item in hospitals}
    fleet = [
        {"id": unit_id, "kind": kind, "station": station,
         "latitude": by_name[station]["latitude"],
         "longitude": by_name[station]["longitude"]}
        for unit_id, kind, station in UNITS if station in by_name
    ]
    if len(fleet) >= 3:
        return fleet

    from corridor.hospital_care import hospital_type
    return [
        {"id": f"108-{number}", "kind": "ALS" if number % 2 else "BLS",
         "station": item["name"],
         "latitude": item["latitude"], "longitude": item["longitude"]}
        for number, item in enumerate(
            (item for item in hospitals if hospital_type(item["name"]) == "major"), 1
        )
    ]


def choose(fleet, busy, critical, latitude, longitude, seconds_to):
    """
    The ambulance to send to a patient at (latitude, longitude).
    busy: unit ids already on a call. critical: the patient's priority is
    Critical. seconds_to(unit) -> driving seconds to the patient, or None.

    Returns (unit or None, rows): unit is a fleet entry plus "seconds";
    rows describe every ambulance considered, for the dashboard:
    {"id", "kind", "station", "minutes", "status", "chosen"}.
    """

    def straight(unit):
        return math.hypot(
            unit["latitude"] - latitude,
            (unit["longitude"] - longitude) * math.cos(math.radians(latitude)),
        )

    rows = []
    free = []
    for unit in sorted(fleet, key=straight):
        if unit["id"] in busy:
            rows.append({**_row(unit, None), "status": "Busy with another patient"})
            continue
        if len(free) >= CANDIDATES:
            continue
        seconds = seconds_to(unit)
        if seconds is None:
            rows.append({**_row(unit, None), "status": "Can't reach"})
            continue
        free.append({**unit, "seconds": seconds})

    free.sort(key=lambda unit: unit["seconds"])
    als = [unit for unit in free if unit["kind"] == "ALS"]
    bls = [unit for unit in free if unit["kind"] == "BLS"]

    chosen, reason = None, None
    if critical:
        if als and (not bls or als[0]["seconds"] <= bls[0]["seconds"] + ALS_WAIT_LIMIT_SECONDS):
            chosen, reason = als[0], "Sent: fastest advanced ambulance, for a critical patient"
        elif bls:
            chosen, reason = bls[0], (
                f"Sent: the advanced one is {round((als[0]['seconds'] - bls[0]['seconds']) / 60)} min "
                "further away" if als else "Sent: no advanced ambulance free"
            )
    elif bls and (not als or als[0]["seconds"] >= bls[0]["seconds"] - ALS_SAVING_SECONDS):
        chosen, reason = bls[0], "Sent: fastest basic ambulance (advanced ones kept for critical patients)"
    elif als:
        chosen, reason = als[0], "Sent: much closer than any basic ambulance"

    for unit in free:
        if unit is chosen:
            status = reason
        elif critical and unit["kind"] == "BLS" and chosen and chosen["kind"] == "ALS":
            status = "Basic ambulance, patient is critical"
        else:
            status = "Further away"
        if unit.get("status") == "returning":
            status += " (on its way back, sent from where it is)"
        rows.append({**_row(unit, unit["seconds"]), "status": status,
                     "chosen": unit is chosen})

    rows.sort(key=lambda row: (not row.get("chosen"), row["minutes"] is None,
                               row["minutes"] or 0))
    return chosen, rows


def _row(unit, seconds):
    return {
        "id": unit["id"],
        "kind": unit["kind"],
        "station": unit["station"],
        "minutes": round(seconds / 60, 1) if seconds is not None else None,
        "chosen": False,
    }


# -------------------------------------------------------------
# Where each 108 ambulance is and what it is doing, during a run
# -------------------------------------------------------------

STATUS_LABELS = {
    "available": "Free",
    "on_call": "With a patient",
    "handover": "Handing patient to doctors",
    "returning": "Free, driving back",
}


class UnitBoard:
    """
    Each ambulance's status in one simulation run:

        available  at its station, free for a call
        on_call    driving to the patient or the hospital
        handover   at the hospital, handing the patient over
        returning  driving back to its station; free for a new call
                   (sent from where it is now)
    """

    def __init__(self):
        self.units = {}

    def reset(self, fleet):
        self.units = {
            unit["id"]: {
                **unit,
                "station_latitude": unit["latitude"],
                "station_longitude": unit["longitude"],
                "status": "available",
                "ambulance": None,    # the run (vehicle id) it serves
                "vehicle_id": None,   # its vehicle while returning
                "missing_since": None,
            }
            for unit in fleet
        }

    def busy(self):
        """Units that can't take a new call."""
        return {
            unit_id for unit_id, unit in self.units.items()
            if unit["status"] in ("on_call", "handover")
        }

    def fleet(self):
        """For choose(): every unit where it is now."""
        return [
            {key: unit[key] for key in ("id", "kind", "station", "latitude",
                                        "longitude", "status")}
            for unit in self.units.values()
        ]

    def summary(self):
        return [
            {
                **{key: unit[key] for key in ("id", "kind", "station", "latitude",
                                              "longitude", "status", "ambulance")},
                "kind_label": KIND_LABELS[unit["kind"]],
                "status_label": STATUS_LABELS[unit["status"]],
            }
            for unit in self.units.values()
        ]

    def at_station(self, unit):
        unit.update(
            status="available", ambulance=None, vehicle_id=None, missing_since=None,
            latitude=unit["station_latitude"], longitude=unit["station_longitude"],
        )
