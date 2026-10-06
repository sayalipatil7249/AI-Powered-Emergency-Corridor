"""
The "Ask" chat: answers questions about the live simulation.

Claude gets a compact snapshot of what is happening right now (every
ambulance, its stage, ETA and delay, police alerts, recent messages,
priority changes) and answers from that only, so it cannot make up
facts about the run.
"""

import asyncio
import json
import logging
import os

import anthropic

from backend.services.simulation_service import simulation_service

logger = logging.getLogger(__name__)

MODEL = os.environ.get("ASSISTANT_MODEL", "claude-opus-5-5")

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
    """No credentials, or the Claude API could not answer."""


_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile).
            _client = anthropic.AsyncAnthropic()
        except (anthropic.AnthropicError, TypeError) as error:
            raise AssistantUnavailable(
                "AI chat is off: set ANTHROPIC_API_KEY in .env and restart the backend."
            ) from error
    return _client


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
    """What Claude sees: the live state, trimmed to what matters."""
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

    try:
        response = await client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=messages,
            output_config={"effort": EFFORT},
            # If a safety classifier declines, retry server-side on
            # Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except TypeError as error:
        # The SDK found no API key, auth token or profile at all.
        raise AssistantUnavailable(
            "AI chat is off: set ANTHROPIC_API_KEY in .env and restart the backend."
        ) from error
    except anthropic.AuthenticationError as error:
        raise AssistantUnavailable(
            "AI chat is off: the ANTHROPIC_API_KEY was rejected."
        ) from error
    except anthropic.RateLimitError as error:
        raise AssistantUnavailable("Too many questions; try again in a minute.") from error
    except anthropic.APIStatusError as error:
        logger.warning("Claude API error %s for the chat.", error.status_code)
        raise AssistantUnavailable(f"Claude API error {error.status_code}.") from error
    except anthropic.APIConnectionError as error:
        raise AssistantUnavailable("Could not reach the Claude API.") from error

    if response.stop_reason == "refusal":
        return "I can't answer that."

    answer = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    return answer or "No answer."


# ---------------------------------------------------------------------
# Emergency request intake: the operator describes the emergency in
# their own words; Claude picks out the place, the patient's condition
# and any hospital wish, and the deterministic planner does the rest
# (find the place on the map, call the suitable hospitals). The AI only
# fills in the planner: the operator checks it and presses Start, and
# the AI never touches signals.
# ---------------------------------------------------------------------

INTAKE_PROMPT = """You turn an ambulance call into the fields of a dispatch form for Pune, India.

Pick out:
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
        "required": ["place", "landmark", "area", "area_other_names", "locality", "condition", "hospital", "government_only",
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
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=f"{INTAKE_PROMPT}\n\nConditions:\n{conditions}",
            messages=[{"role": "user", "content": text}],
            output_config={
                "effort": EFFORT,
                "format": {"type": "json_schema", "schema": _intake_schema()},
            },
        )
    except TypeError as error:
        raise AssistantUnavailable(
            "AI is off: set ANTHROPIC_API_KEY in .env and restart the backend."
        ) from error
    except anthropic.AuthenticationError as error:
        raise AssistantUnavailable("AI is off: the ANTHROPIC_API_KEY was rejected.") from error
    except anthropic.RateLimitError as error:
        raise AssistantUnavailable("Too many requests; try again in a minute.") from error
    except anthropic.APIStatusError as error:
        logger.warning("Claude API error %s for the intake.", error.status_code)
        raise AssistantUnavailable(f"Claude API error {error.status_code}.") from error
    except anthropic.APIConnectionError as error:
        raise AssistantUnavailable("Could not reach the Claude API.") from error

    if response.stop_reason == "refusal":
        raise IntakeError("The AI could not read this request. Fill in the form by hand.")
    text_block = next((block.text for block in response.content if block.type == "text"), None)
    if not text_block:
        raise IntakeError("No answer from the AI. Fill in the form by hand.")
    return json.loads(text_block)


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
        "note": " ".join(item for item in (place_note, note) if item) or None,
        "calls": calls,
    }


async def intake(text):
    """Turn a typed emergency request into the planner's fields."""
    fields = await _extract(text)
    return await asyncio.to_thread(_plan_intake, fields)
