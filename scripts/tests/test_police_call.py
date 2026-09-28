"""
Place ONE test phone call exactly like a real police alert, to check the
Twilio settings (no simulation needed).

    python -m scripts.tests.test_police_call

Calls the number saved for the station in the database (or else
POLICE_ALERT_PHONE from .env) and prints the call's progress:
queued -> ringing -> in-progress -> completed.
On a Twilio trial account, press any key when the call starts to hear
the message.
"""

import time

from backend.services.police_notifier import FINAL_STATUSES, police_notifier

ALERT = {
    "alert_id": "test-call",
    "station": "Faraskhana Police Station",
    "road": "Ganesh Path",
    "ambulance_eta_seconds": 240,
    "status": "ALERTED",
}


def main():
    if not police_notifier.enabled():
        print("Calls are off: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and "
              "TWILIO_FROM_NUMBER in .env (and not POLICE_CALLS=0).")
        return
    phone, source = police_notifier.phone_for(ALERT["station"])
    if not phone:
        print("No number: save one for the station (PUT /police-stations/"
              "{station_id}/phone) or set POLICE_ALERT_PHONE in .env.")
        return
    print(f"Calling the {source} number for {ALERT['station']}.")

    police_notifier.new_trip()
    police_notifier.alert(ALERT)

    last = None
    for _ in range(60):
        call = police_notifier.call_status(ALERT["alert_id"])
        if call != last:
            print(call)
            last = call
        if call and call.get("status") in FINAL_STATUSES + ("off", "no_number", "limit"):
            break
        time.sleep(2)


if __name__ == "__main__":
    main()
