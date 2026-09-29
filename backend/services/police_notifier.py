"""
Phone calls to police stations (Twilio voice) when a stretch of the
ambulance's route without signals jams (corridor/police_watch.py).

A voice reads the alert out twice: which road, how soon the ambulance
arrives, please clear it.

Who is called: the station's contact number in the database
(police_stations.phone, set with PUT /police-stations/{id}/phone), or,
for a station without one, POLICE_ALERT_PHONE from .env.

SAFETY: while testing, only test phones - never a real police number: a
test call would be a false emergency.

Every alert and call is saved in the police_calls table.

Settings (.env):
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
    POLICE_ALERT_PHONE     test phone (+91XXXXXXXXXX)
    POLICE_CALLS=0         turn real calls off (the call is only logged)

Calls are made in a background thread: the simulation never waits for
Twilio. At most MAX_CALLS_PER_TRIP calls per trip (cost and spam guard).
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

from backend.services.police_station_service import phone_for_station, save_call

load_dotenv()

logger = logging.getLogger(__name__)

MAX_CALLS_PER_TRIP = 3

# Indian English voice (Amazon Polly through Twilio).
VOICE = "Polly.Aditi"
LANGUAGE = "en-IN"

# Follow a placed call until it ends (answered, busy, no answer...).
STATUS_POLL_SECONDS = 5
STATUS_POLL_LIMIT_SECONDS = 120
FINAL_STATUSES = ("completed", "busy", "no-answer", "failed", "canceled")


def _setting(name):
    return (os.environ.get(name) or "").strip()


def _phone(number):
    """"+1 (234) 567-8940" -> "+12345678940" (the form Twilio expects)."""
    return "".join(ch for ch in (number or "") if ch.isdigit() or ch == "+")


def message_for(alert):
    """What the officer hears."""
    minutes = max(1, round(alert["ambulance_eta_seconds"] / 60))
    problem = (
        "There is an accident blocking the road."
        if alert.get("cause") == "accident"
        else "Traffic there is heavy."
    )
    return (
        "This is a test call from the Emergency Corridor simulation. "
        f"Alert for {alert['station']}. "
        f"An ambulance is approaching {alert['road']} in about {minutes} "
        f"minute{'s' if minutes != 1 else ''}. {problem} "
        "Please send officers to clear the road for the ambulance."
    )


class PoliceNotifier:
    def __init__(self):
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="police-call")
        # Database writes one at a time, in order (police_calls table).
        self._db = ThreadPoolExecutor(max_workers=1, thread_name_prefix="police-db")
        self._lock = threading.Lock()
        self._client = None
        self.calls = {}           # alert id -> call status for the dashboard
        self._calls_this_trip = 0

    # -------------------------------------------------------------

    def enabled(self):
        """Real calls possible: Twilio configured and not switched off."""
        return (
            _setting("POLICE_CALLS") != "0"
            and all(_setting(name) for name in (
                "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
            ))
        )

    def new_trip(self):
        with self._lock:
            self.calls.clear()
            self._calls_this_trip = 0

    def phone_for(self, station):
        """(number to call, "station" or "default"), or (None, None): the
        station's number in the database, else POLICE_ALERT_PHONE."""
        phone = phone_for_station(station)
        if phone:
            return _phone(phone), "station"
        default = _phone(_setting("POLICE_ALERT_PHONE"))
        return (default, "default") if default else (None, None)

    def record(self, alert):
        """Save an alert (new, or its status changed) in police_calls."""
        self._db.submit(save_call, alert["alert_id"], dict(alert))

    def call_status(self, alert_id):
        with self._lock:
            call = self.calls.get(alert_id)
            return dict(call) if call else None

    # -------------------------------------------------------------

    def alert(self, alert):
        """A new alert: phone the station (in the background)."""

        text = message_for(alert)
        phone, source = self.phone_for(alert["station"])
        alert_id = alert["alert_id"]

        with self._lock:
            if not self.enabled():
                call = {
                    "status": "off", "to": _mask(phone), "source": source,
                    "detail": "Calls are off (no Twilio settings, or POLICE_CALLS=0).",
                }
                logger.info("Police call (not placed, calls off): %s", text)
            elif not phone:
                call = {
                    "status": "no_number", "to": None, "source": None,
                    "detail": "No contact number for this station and no POLICE_ALERT_PHONE.",
                }
                logger.warning("No phone number for %s", alert["station"])
            elif self._calls_this_trip >= MAX_CALLS_PER_TRIP:
                call = {
                    "status": "limit", "to": _mask(phone), "source": source,
                    "detail": f"Call limit reached ({MAX_CALLS_PER_TRIP} per trip).",
                }
            else:
                self._calls_this_trip += 1
                call = {
                    "status": "calling", "to": _mask(phone), "source": source,
                    "detail": None,
                }
            self.calls[alert_id] = call

        self._db.submit(
            save_call, alert_id, dict(alert),
            call_status=call["status"], call_detail=call["detail"],
            phone_called=call["to"], phone_source=call["source"],
        )
        if call["status"] == "calling":
            self._pool.submit(self._call, alert_id, phone, text)

    def _twilio(self):
        if self._client is None:
            from twilio.rest import Client
            self._client = Client(
                _setting("TWILIO_ACCOUNT_SID"), _setting("TWILIO_AUTH_TOKEN")
            )
        return self._client

    def _call(self, alert_id, phone, text):
        from twilio.twiml.voice_response import VoiceResponse

        speech = VoiceResponse()
        speech.say(text, voice=VOICE, language=LANGUAGE)
        speech.pause(length=1)
        speech.say("Repeating. " + text, voice=VOICE, language=LANGUAGE)

        try:
            call = self._twilio().calls.create(
                to=phone, from_=_phone(_setting("TWILIO_FROM_NUMBER")),
                twiml=str(speech),
            )
        except Exception as error:
            logger.error("Police call to %s failed: %s", _mask(phone), error)
            self._update(alert_id, status="failed", detail=_short(error))
            return

        logger.info("Police call placed to %s (%s)", _mask(phone), call.sid)
        self._update(alert_id, status=call.status, sid=call.sid)

        # Follow the call so the dashboard shows ringing / answered / ...
        waited = 0
        while waited < STATUS_POLL_LIMIT_SECONDS:
            time.sleep(STATUS_POLL_SECONDS)
            waited += STATUS_POLL_SECONDS
            try:
                status = self._twilio().calls(call.sid).fetch().status
            except Exception as error:
                logger.warning("Could not read call status: %s", error)
                return
            self._update(alert_id, status=status)
            if status in FINAL_STATUSES:
                return

    def _update(self, alert_id, **values):
        with self._lock:
            self.calls.setdefault(alert_id, {}).update(values)
        self._db.submit(
            save_call, alert_id,
            call_status=values.get("status"),
            call_sid=values.get("sid"),
            call_detail=values.get("detail"),
        )


def _mask(phone):
    """+9198XXXXXX10: enough to recognise, not the whole number."""
    return phone[:5] + "X" * max(0, len(phone) - 7) + phone[-2:] if phone else None


# Twilio errors worth explaining in plain words (twilio.com/docs/errors).
TWILIO_ERRORS = {
    21219: "Number not verified in Twilio: a trial account can only call "
           "numbers verified in the Twilio console (Verified Caller IDs).",
    21215: "Calls to this country are not enabled in Twilio's Voice "
           "geographic permissions.",
    21211: "Not a valid phone number.",
}


def _short(error):
    """A readable reason, e.g. "Twilio 21219: Number not verified ..."."""
    code = getattr(error, "code", None)
    if code is not None:
        reason = TWILIO_ERRORS.get(code) or getattr(error, "msg", "") or ""
        return f"Twilio {code}: {reason}".strip()[:200]
    return str(error).strip().splitlines()[-1][:200]


police_notifier = PoliceNotifier()
