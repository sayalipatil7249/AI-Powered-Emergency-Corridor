"""
Save the contact numbers of police stations in the database, all at once,
from police_contacts.json ({"<station name>": "+91...", ...}; copy
police_contacts.example.json to start). Then list every station and its
number.

    python -m scripts.set_police_phones            save + list
    python -m scripts.set_police_phones --list     only list

TESTING: only test phones (yours, your team's) - never a real police
number: a test call would be a false emergency. On a Twilio trial account
every number must also be verified in the Twilio console.

One station at a time works too: PUT /police-stations/{station_id}/phone
(http://127.0.0.1:8000/docs).
"""

import argparse
import json
import os

from backend.database import SessionLocal
from backend.services.police_station_service import (
    ensure_schema,
    list_stations,
    mask_phone,
    set_phone,
    sync_stations,
)
from simulation.sumo import route_planner

CONTACTS_FILE = "police_contacts.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--list", action="store_true", help="only list the stations")
    args = parser.parse_args()

    ensure_schema()
    sync_stations(route_planner.police_stations())

    db = SessionLocal()
    try:
        if not args.list:
            if not os.path.exists(CONTACTS_FILE):
                print(f"No {CONTACTS_FILE}: copy police_contacts.example.json "
                      "and fill in test numbers.")
                return
            with open(CONTACTS_FILE, encoding="utf-8") as file:
                contacts = json.load(file)

            stations = {row.name: row for row in list_stations(db)}
            for name, phone in contacts.items():
                if name.startswith("_"):
                    continue  # notes
                row = stations.get(name)
                if row is None:
                    print(f"  ? {name}: no station with this name (see the list below)")
                    continue
                if not phone or "X" in phone:
                    continue  # placeholder left as it was
                try:
                    set_phone(db, row.station_id, phone)
                    print(f"  saved {mask_phone(row.phone)} for {name}")
                except ValueError as error:
                    print(f"  ! {name}: {error}")

        print("\nPolice stations in the database:")
        for row in list_stations(db):
            print(f"  {row.station_id:24} {row.name:32} {mask_phone(row.phone) or '(no number)'}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
