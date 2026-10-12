"""
The "Ask" chat: answers questions about the live simulation.

Gemini gets a compact snapshot of what is happening right now (every
ambulance, its stage, ETA and delay, police alerts, recent messages,
priority changes) and answers from that only, so it cannot make up
facts about the run.
"""

import asyncio
import json
import logging
import os
from threading import Lock

import httpx
from google import genai
from google.genai import errors, types

from backend.services.simulation_service import simulation_service

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ASSISTANT_MODEL", "gemini-3.5-flash")

# Short questions about a snapshot: low effort keeps answers quick.
EFFORT = os.environ.get("ASSISTANT_EFFORT", "low")

# Earlier questions and answers sent along, so follow-ups make sense.
MAX_HISTORY = 8

SYSTEM_PROMPT = """You answer questions about a live ambulance green-corridor simulation in Pune.

Each question comes with a JSON snapshot of the simulation right now. Answer from that snapshot only. If the snapshot does not contain the answer, say you can't see that.

What the parts mean:
- ambulances: one per ambulance. level_name is the patient's priority (Critical, Urgent, Stable). leg is the stage of the journey: to_patient (driving from the base), at_patient (picking up), to_hospital (patient on board); patient_on_board says the same as yes / no. eta_seconds and distance_left_meters are what is left to the hospital, not to the patient: while leg is to_patient they include the drive to the patient and the time loading them. The snapshot has no separate time or distance to the patient. delay_reason says why it is stopped. give_way means it is waiting at a junction for a higher-priority ambulance. next_signal is the next traffic signal on its route.
- unit: the 108 ambulance sent (ALS = advanced life support, BLS = basic) and its station.
- pre_alert: the hospital's answer to the 108 call centre's pre-alert (what the patient needs, e.g. cath lab, and whether it is ready). diverted_from: the hospital it was going to before being diverted.
- stage (after arrival): "handover" (handing the patient to the hospital, handover_left_seconds to go), then "returning" (back in service, driving to its station) or "available".
- units_108: every 108 ambulance and what it is doing; "ambulance" is the trip it serves.
- police_alerts: police sent to clear roads without signals or jams.
- recent_messages: the latest messages shown on the dashboard.
- priority_changes: patient condition changes made by the crew or the admin.
- If status is not "running", no simulation is running.

Style: blunt and short. One to three sentences, plain words, no headings, no lists unless asked. Give times in minutes and seconds. Refer to ambulances by their label (e.g. "Ambulance 2")."""


class AssistantUnavailable(Exception):
    """No credentials, or the Gemini API could not answer."""


class _ClientRotation:
    """One process-wide sequence; each client keeps its own credentials."""

    def __init__(self):
        keys = tuple(
            key.strip()
            for key in os.environ.get("GEMINI_API_KEYS", "").split(",")
            if key.strip()
        )
        single_key = os.environ.get("GEMINI_API_KEY", "").strip()
        self._keys = keys or ((single_key,) if single_key else ())
        self._clients = {}
        self._index = 0
        self._lock = Lock()

    def next_client(self):
        # No await or network I/O under the lock. Never mutate a client's key.
        with self._lock:
            if not self._keys:
                raise AssistantUnavailable(
                    "AI is off: set GEMINI_API_KEYS or GEMINI_API_KEY in .env "
                    "and restart the backend."
                )
            index = self._index
            if index not in self._clients:
                key = self._keys[index]
                self._clients[index] = (
                    genai.Client(
                        api_key=key,
                        vertexai=False,
                        http_options=types.HttpOptions(
                            # Three total attempts, with SDK exponential backoff.
                            retry_options=types.HttpRetryOptions(attempts=3),
                        ),
                    )
                )
            self._index = (index + 1) % len(self._keys)
            return self._clients[index]


# Configuration is read once, after the backend's existing dotenv loading.
_client_rotation = _ClientRotation()


def _get_client():
    return _client_rotation.next_client()


async def _generate(client, contents, config):
    """Retries stay on the selected client; never fail over on a quota error."""
    try:
        return await client.aio.models.generate_content(
            model=MODEL, contents=contents, config=config,
        )
    except errors.APIError as error:
        if error.code == 429:
            raise AssistantUnavailable("Too many requests; try again in a minute.") from error
        if error.code in (401, 403):
            raise AssistantUnavailable("AI is off: the selected Gemini API key was rejected.") from error
        # Do not log provider messages, which can contain request details.
        logger.warning("Gemini API error %s.", error.code)
        raise AssistantUnavailable(f"Gemini API error {error.code}.") from error
    except httpx.RequestError as error:
        raise AssistantUnavailable("Could not reach the Gemini API.") from error


def _blocked(response):
    return bool(
        (response.prompt_feedback and response.prompt_feedback.block_reason)
        or any(candidate.finish_reason in (
            "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "RECITATION", "SPII",
        ) for candidate in (response.candidates or []))
    )


def _ambulance(item):
    """The parts of an ambulance summary worth asking about (no geometry)."""
    keys = (
        "label", "level_name", "condition_name", "condition", "status", "leg",
        "start_name", "base_name", "hospital_name", "eta_seconds",
        "distance_left_meters", "trip_time_seconds", "speed", "stops",
        "delay_reason", "give_way", "next_signal", "advised_speed",
        "unit", "pre_alert", "diverted_from", "stage", "handover_left_seconds",
    )
    result = {key: item.get(key) for key in keys if item.get(key) is not None}
    if result.get("leg"):
        result["patient_on_board"] = result["leg"] == "to_hospital"
    if item.get("routing_decision"):
        result["route_note"] = item["routing_decision"].get("explanation")
    if isinstance(result.get("speed"), (int, float)):
        result["speed_kmh"] = round(result.pop("speed") * 3.6)
    return result


def _police_alerts(state):
    """Police alerts of every ambulance, without map shapes."""
    labels = {
        item.get("vehicle_id"): item.get("label")
        for item in state.get("ambulances", [])
    }
    details = state.get("ambulance_details") or {}
    watches = [(None, state.get("police_watch"))] + [
        (vehicle_id, detail.get("police_watch"))
        for vehicle_id, detail in details.items()
    ]
    alerts = []
    seen = set()
    for vehicle_id, watch in watches:
        for alert in (watch or {}).get("alerts", []):
            if alert.get("alert_id") in seen:
                continue
            seen.add(alert.get("alert_id"))
            item = {
                key: alert.get(key)
                for key in ("road", "status", "cause", "detail", "station",
                            "police_eta_seconds", "ambulance_eta_seconds",
                            "late", "vehicles_waved", "closed_reason")
                if alert.get(key) is not None
            }
            if vehicle_id in labels:
                item["for"] = labels[vehicle_id]
            alerts.append(item)
    return alerts


def snapshot():
    """What Gemini sees: the live state, trimmed to what matters."""
    state = simulation_service.get_state()
    return {
        "status": state.get("status"),
        "simulation_time_seconds": state.get("simulation_time"),
        "ambulances": [_ambulance(item) for item in state.get("ambulances", [])],
        "police_alerts": _police_alerts(state),
        "recent_messages": [
            item.get("text") if isinstance(item, dict) else item
            for item in list(state.get("agent_feed", []))[-15:]
        ],
        "priority_changes": list(state.get("priority_changes", []))[-10:],
        "units_108": [
            {key: unit.get(key) for key in ("id", "kind", "station", "status_label", "ambulance")}
            for unit in state.get("units", [])
        ],
        "live_traffic": state.get("live_traffic"),
    }


async def ask(question, history=()):
    """Answer one question. history: earlier [{"role", "content"}] turns."""

    client = _get_client()

    messages = [
        {"role": turn["role"], "content": turn["content"]}
        for turn in list(history)[-MAX_HISTORY:]
        if turn.get("role") in ("user", "assistant") and turn.get("content")
    ]
    # The conversation must start with the user.
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    messages.append({
        "role": "user",
        "content": (
            f"Snapshot now:\n{json.dumps(snapshot(), default=str)}\n\n"
            f"Question: {question}"
        ),
    })

    response = await _generate(
        client,
        [types.Content(
            role="model" if turn["role"] == "assistant" else "user",
            parts=[types.Part(text=turn["content"])],
        ) for turn in messages],
        types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=4000,
            thinking_config=types.ThinkingConfig(thinking_level=EFFORT),
        ),
    )
    if _blocked(response):
        return "I can't answer that."
    return (response.text or "").strip() or "No answer."


# ---------------------------------------------------------------------
# Emergency request intake: the operator describes the emergency in
# their own words; Gemini picks out the place, the patient's condition
# and any hospital wish, and the deterministic planner does the rest
# (find the place on the map, call the suitable hospitals). The AI only
# fills in the planner: the operator checks it and presses Start, and
# the AI never touches signals.
# ---------------------------------------------------------------------

INTAKE_PROMPT = """You turn an ambulance call into the fields of a dispatch form for Pune, India.

The call may be in English, Hindi or Marathi (or a mix), typed or spoken
(speech-to-text, so words may be misheard). Write every field in English,
and place names in English letters as people in Pune spell them (e.g.
"कसबा पेठ" -> "Kasba Peth", "शनिवार वाडा" -> "Shaniwar Wada").

Pick out:
- english: the caller's words translated into plain English (if already English, the same words, tidied).
- place: where the patient is, in a few words, as in the message (e.g. "Crossword book store, FC Road"). Empty if not given.
- landmark: only the building, shop or landmark name (e.g. "Crossword"); empty if none.
- area: only the road or neighbourhood as people say it (e.g. "FC Road", "Kasba Peth"); empty if none.
- locality: the Pune neighbourhood the place is in, from your own knowledge of Pune (e.g. Paud Road -> "Kothrud", FC Road -> "Deccan Gymkhana", Laxmi Road -> "Budhwar Peth"). Empty if you don't know.
- area_other_names: other names of that road or area, separated by ";" (e.g. for FC Road: "Fergusson College Road; Gopal Krishna Gokhale Road"; for JM Road: "Jangli Maharaj Road"; for MG Road: "Mahatma Gandhi Road"). Empty if you don't know any.
- condition: the closest matching condition from the list.
- hospital: a hospital the caller asked for by name; empty if none.
- government_only: true only if they ask for a government / free hospital.
- patient_on_board: true only if the patient is already in the ambulance.
- summary: one short line in plain English, e.g. "Heart attack at Kasba Peth".
- reason: one short sentence on why you chose this condition.

Never invent a place or a hospital that is not in the message."""


class IntakeError(Exception):
    """The request could not be turned into a trip (shown to the operator)."""


def _intake_schema():
    from corridor import priority
    return {
        "type": "object",
        "properties": {
            "english": {"type": "string"},
            "place": {"type": "string"},
            "landmark": {"type": "string"},
            "area": {"type": "string"},
            "area_other_names": {"type": "string"},
            "locality": {"type": "string"},
            "condition": {"type": "string", "enum": list(priority.CONDITIONS)},
            "hospital": {"type": "string"},
            "government_only": {"type": "boolean"},
            "patient_on_board": {"type": "boolean"},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["english", "place", "landmark", "area", "area_other_names", "locality", "condition", "hospital", "government_only",
                     "patient_on_board", "summary", "reason"],
        "additionalProperties": False,
    }


async def _extract(text):
    from corridor import priority
    client = _get_client()
    conditions = "\n".join(
        f"- {key}: {label} ({priority.LEVEL_NAMES[level]})"
        for key, (label, level) in priority.CONDITIONS.items()
    )
    response = await _generate(
        client,
        [types.Content(role="user", parts=[types.Part(text=text)])],
        types.GenerateContentConfig(
            system_instruction=f"{INTAKE_PROMPT}\n\nConditions:\n{conditions}",
            max_output_tokens=2000,
            thinking_config=types.ThinkingConfig(thinking_level=EFFORT),
            response_mime_type="application/json",
            response_json_schema=_intake_schema(),
        ),
    )
    if _blocked(response):
        raise IntakeError("The AI could not read this request. Fill in the form by hand.")
    if not response.text:
        raise IntakeError("No answer from the AI. Fill in the form by hand.")
    try:
        fields = json.loads(response.text)
    except (ValueError, TypeError) as error:
        raise IntakeError("The AI returned an incomplete form. Please try again.") from error
    # Truncated or malformed output must not reach the deterministic planner.
    schema = _intake_schema()
    if not isinstance(fields, dict) or any(
        key not in fields or not isinstance(fields[key], bool if spec['type'] == 'boolean' else str)
        for key, spec in schema['properties'].items()
    ) or fields['condition'] not in priority.CONDITIONS:
        raise IntakeError("The AI returned an incomplete form. Please try again.")
    return fields


# A landmark found by name counts only this close to the road or area
# the caller named (m): shops like "Crossword" have several branches.
LANDMARK_NEAR_AREA_METERS = 1000


def _meters(a, b):
    import math
    return math.hypot(
        (a["latitude"] - b["latitude"]) * 111_320,
        (a["longitude"] - b["longitude"]) * 111_320 * math.cos(math.radians(a["latitude"])),
    )


def _find_place(planning, fields):
    """Where the patient is, on the map: the landmark near the named road
    or area if it can be found there, else that road or area, else the
    whole place text. Returns (place, note for the operator or None)."""

    def first(*queries):
        for query in queries:
            query = query.strip(" ,;")
            if len(query) >= 3:
                found = planning.search_places(query)
                if found:
                    return found
        return []

    landmark = fields.get("landmark", "").strip()
    area = fields.get("area", "").strip()
    other = [name.strip() for name in fields.get("area_other_names", "").split(";")]
    areas = first(area, *other) if area else []
    landmarks = first(f"{landmark} {area}", landmark) if landmark else []

    def pick(item):
        # "FC Road, 1216" -> "FC Road" (house numbers say nothing here)
        name = ", ".join(
            part for part in item["name"].split(", ") if not part.strip().isdigit()
        ) or item["name"]
        return {"name": name, "latitude": item["latitude"], "longitude": item["longitude"]}

    if landmarks and areas:
        near = [
            item for item in landmarks
            if min(_meters(item, road) for road in areas) <= LANDMARK_NEAR_AREA_METERS
        ]
        if near:
            return pick(near[0]), None
        return pick(areas[0]), (
            f"'{landmark}' isn't on the map near {area}, so the ambulance goes to {pick(areas[0])['name']}."
        )
    if areas:
        return pick(areas[0]), (
            f"'{landmark}' isn't on the map, so the ambulance goes to {pick(areas[0])['name']}."
            if landmark else None
        )
    if landmarks:
        return pick(landmarks[0]), None
    place = fields.get("place", "").strip()
    locality = fields.get("locality", "").strip()
    whole = first(place) if place else []
    if whole:
        return pick(whole[0]), None
    # The neighbourhood itself (geocoded across Pune, not a name match:
    # "Kothrud" would match "Janata Bank, Kothrud Branch" elsewhere).
    from simulation.sumo import route_planner
    if locality:
        found = planning.geocode_pune(locality)
        if found and route_planner.inside_area(found[1], found[2]):
            return {"name": found[0], "latitude": found[1], "longitude": found[2]}, (
                f"Couldn't find '{place or area}' itself, so the ambulance goes to the "
                f"middle of {locality}. Correct the place below if needed."
            )
    if not (place or landmark or area or locality):
        raise IntakeError("Where is the patient? Add the place (e.g. 'near Kasba Peth').")

    # Not inside the simulated area: is it somewhere else in Pune?
    for query in (f"{landmark} {area}".strip(), area, *other, locality, place):
        query = query.strip(" ,;")
        if len(query) < 3:
            continue
        found = planning.geocode_pune(query)
        if found and not route_planner.inside_area(found[1], found[2]):
            raise IntakeError(
                f"Found {found[0]}{f' ({locality})' if locality else ''}, but it is outside the "
                "simulated part of Pune (central Pune: Deccan, Shivajinagar, the Peths, "
                "Camp, Station). Pick a place inside the marked area on the map."
            )
    raise IntakeError(
        f"Couldn't find '{place or area or landmark}' on the map. "
        "Try the road or area name, or use Pick on map."
    )


def _plan_intake(fields):
    """The deterministic part: place on the map, suitable hospital."""
    from backend.api.routes import planning
    from corridor import hospital_care, priority
    from simulation.sumo import route_planner

    start, place_note = _find_place(planning, fields)

    condition = fields["condition"]
    calls, hospital, note = [], None, None
    wanted = fields["hospital"].strip().lower()
    if wanted:
        named = [item for item in route_planner.hospitals() if wanted in item["name"].lower()]
        suitable = [item for item in named if hospital_care.can_treat(item["name"], condition)]
        if suitable:
            hospital = suitable[0]
        elif named:
            note = f"{named[0]['name']} can't treat this patient, so the nearest suitable hospital was chosen."
        else:
            note = f"No hospital called '{fields['hospital']}' on the map, so the nearest suitable one was chosen."
    if hospital is None:
        result = planning.call_hospitals(planning.HospitalCall(
            latitude=start["latitude"], longitude=start["longitude"],
            condition=condition, government_only=fields["government_only"],
        ))
        calls, hospital = result["calls"], result["hospital"]
    if hospital is None:
        raise IntakeError("No suitable hospital nearby can take this patient right now.")

    return {
        "start": start,
        "condition": condition,
        **{key: value for key, value in priority.describe(condition).items() if key != "condition"},
        "hospital": {key: hospital[key] for key in ("name", "latitude", "longitude")},
        "from_base": not fields["patient_on_board"],
        "government_only": fields["government_only"],
        "summary": fields["summary"],
        "reason": fields["reason"],
        "english": fields["english"],
        "note": " ".join(item for item in (place_note, note) if item) or None,
        "calls": calls,
    }


async def intake(text):
    """Turn a typed emergency request into the planner's fields."""
    fields = await _extract(text)
    return await asyncio.to_thread(_plan_intake, fields)
