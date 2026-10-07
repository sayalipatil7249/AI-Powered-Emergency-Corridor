"""
Police stations and their contact numbers in the database
(backend/models/police_station.py), and the log of police alerts and
phone calls (backend/models/police_call.py).

    ensure_schema()      adds new columns to an older police_stations table
    sync_stations()      stations from OpenStreetMap -> database (keeps phones)
    set_phone()          save a station's contact number
    phone_for_station()  the number to call for a station, or None
    save_call()          insert / update one alert + call in the log

Database problems never stop the simulation: the calls that the live
simulation makes (phone_for_station, save_call) log a warning and go on.
"""

import logging
import re
from datetime import datetime

from geoalchemy2.elements import WKTElement
from sqlalchemy import text

from backend.database import SessionLocal, engine
from backend.models.police_call import PoliceCall
from backend.models.police_station import PoliceStation

logger = logging.getLogger(__name__)

# International format: + and 8-15 digits, e.g. +919876543210.
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def clean_phone(number):
    """The number in the form Twilio calls: "+91" and 10 digits for an
    Indian mobile. Accepts "+91 98765-43210", "9876543210",
    "09876543210" and "919876543210" (all -> "+919876543210"), or any
    international number starting with "+". Raises ValueError otherwise.
    (Same rules as frontend/src/phone.js.)"""

    cleaned = "".join(ch for ch in (number or "") if ch.isdigit() or ch == "+")
    digits = cleaned.lstrip("+")
    if not cleaned.startswith("+"):
        if len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
        if len(digits) == 10 and digits[0] in "6789":
            cleaned = "+91" + digits
    if cleaned.startswith("+91") and not re.fullmatch(r"\+91[6-9]\d{9}", cleaned):
        raise ValueError(
            "An Indian mobile number has 10 digits after +91, starting "
            "with 6, 7, 8 or 9, e.g. +91 98765 43210."
        )
    if not PHONE_PATTERN.match(cleaned):
        raise ValueError(
            "Type the mobile number as +91 followed by its 10 digits, "
            "e.g. +91 98765 43210."
        )
    return cleaned


def mask_phone(phone):
    """+9198XXXXXX10: enough to recognise, not the whole number."""
    if not phone:
        return None
    return phone[:5] + "X" * max(0, len(phone) - 7) + phone[-2:]


# -------------------------------------------------------------
# Schema and sync (start-up)
# -------------------------------------------------------------

def ensure_schema():
    """The police_stations table may come from an older version of the
    project: add the columns it is missing (PostgreSQL)."""

    with engine.begin() as connection:
        for column, kind in (
            ("name_local", "VARCHAR(255)"),
            ("kind", "VARCHAR(50)"),
            ("road_name", "VARCHAR(255)"),
            ("phone", "VARCHAR(20)"),
            ("updated_at", "TIMESTAMP"),
        ):
            connection.execute(text(
                f"ALTER TABLE police_stations ADD COLUMN IF NOT EXISTS {column} {kind}"
            ))


def _slug(name):
    return "ps-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def sync_stations(stations):
    """
    Stations from route_planner.police_stations() -> database: insert new
    ones, refresh names / type / road / position of known ones. Phone
    numbers are never touched. Returns the number of stations.
    """

    db = SessionLocal()
    try:
        existing = db.query(PoliceStation).all()
        for station in stations:
            # Older rows may carry the Marathi name after the English one.
            row = next(
                (
                    item for item in existing
                    if item.name == station["name"]
                    or item.name.startswith(station["name"] + " ")
                ),
                None,
            )
            if row is None:
                row = PoliceStation(station_id=_slug(station["name"]))
                db.add(row)
                existing.append(row)

            row.name = station["name"]
            row.name_local = station.get("name_local")
            row.kind = station.get("kind")
            row.road_name = station.get("road_name")
            row.latitude = station["latitude"]
            row.longitude = station["longitude"]
            row.location = WKTElement(
                f"POINT({station['longitude']} {station['latitude']})", srid=4326
            )
        db.commit()
        return len(stations)
    finally:
        db.close()


# -------------------------------------------------------------
# Stations and contact numbers
# -------------------------------------------------------------

def station_dict(row, full_phone=False):
    return {
        "station_id": row.station_id,
        "name": row.name,
        "name_local": row.name_local,
        "kind": row.kind,
        "road_name": row.road_name,
        "latitude": row.latitude,
        "longitude": row.longitude,
        "has_phone": bool(row.phone),
        "phone": row.phone if full_phone else mask_phone(row.phone),
        "updated_at": row.updated_at,
    }


def list_stations(db):
    return db.query(PoliceStation).order_by(PoliceStation.name).all()


def get_station(db, station_id):
    return db.query(PoliceStation).filter(PoliceStation.station_id == station_id).first()


def set_phone(db, station_id, phone):
    """Save (or with phone=None remove) a station's contact number."""

    row = get_station(db, station_id)
    if row is None:
        return None
    row.phone = clean_phone(phone) if phone else None
    row.updated_at = datetime.now()
    db.commit()
    db.refresh(row)
    return row


def contacts_by_name():
    """{station name: masked phone or None} for the dashboard; {} if the
    database cannot be reached."""

    db = SessionLocal()
    try:
        return {row.name: mask_phone(row.phone) for row in list_stations(db)}
    except Exception as error:
        logger.warning("Could not read police contacts: %s", error)
        return {}
    finally:
        db.close()


def phone_for_station(name):
    """The station's contact number from the database, or None."""

    db = SessionLocal()
    try:
        row = db.query(PoliceStation).filter(PoliceStation.name == name).first()
        return row.phone if row else None
    except Exception as error:
        logger.warning("Could not read the phone number of %s: %s", name, error)
        return None
    finally:
        db.close()


# -------------------------------------------------------------
# Log of alerts and calls
# -------------------------------------------------------------

_ALERT_FIELDS = {
    "station": "station_name",
    "road": "road",
    "latitude": "latitude",
    "longitude": "longitude",
    "ambulance_eta_seconds": "ambulance_eta_seconds",
    "police_eta_seconds": "police_eta_seconds",
    "status": "alert_status",
    "stopped_on_arrival": "stopped_on_arrival",
    "stopped_after": "stopped_after",
    "vehicles_waved": "vehicles_waved",
}


def save_call(alert_id, alert=None, **call):
    """Insert or update the log row of one alert. alert: the police
    watch's alert dict; call: call_status, call_sid, call_detail,
    phone_called, phone_source."""

    db = SessionLocal()
    try:
        row = db.query(PoliceCall).filter(PoliceCall.alert_id == alert_id).first()
        if row is None:
            if alert is None:
                return
            row = PoliceCall(alert_id=alert_id, created_at=datetime.now())
            db.add(row)
        for key, column in _ALERT_FIELDS.items():
            if alert is not None and key in alert:
                setattr(row, column, alert[key])
        for column, value in call.items():
            if value is not None:
                setattr(row, column, value)
        row.updated_at = datetime.now()
        db.commit()
    except Exception as error:
        db.rollback()
        logger.warning("Could not save police call %s: %s", alert_id, error)
    finally:
        db.close()


def list_calls(db, limit=50):
    return (
        db.query(PoliceCall)
        .order_by(PoliceCall.created_at.desc())
        .limit(limit)
        .all()
    )
