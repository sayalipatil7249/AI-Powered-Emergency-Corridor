"""
Safety rules for extra signal priority requested by an AI agent (or an
operator), on top of the corridor engine's automatic priority for the
nearest junction.

The agent can only *request*; these rules decide. They exist so that a
wrong or over-eager request can never starve cross traffic for long.
"""

# Only junctions this close to the ambulance can be held green early.
MAX_REQUEST_DISTANCE_METERS = 1000

# At most this many junctions held green at once, counting the
# nearest junction that the engine controls automatically.
MAX_HELD_JUNCTIONS = 2

# A requested green is released automatically after this long, even if
# the ambulance has not arrived yet (fail-safe).
MAX_HOLD_SECONDS = 90


def check_request(junction, upcoming, held_requests, active_junction):
    """
    Decide whether extra priority may be given to `junction`
    (an entry of CorridorEngine.upcoming_signals()).

    Returns (allowed: bool, reason: str).
    """

    if not upcoming:
        return False, "The ambulance has no signals ahead."

    key = (junction["signal_id"], junction["route_index"])

    if key == (upcoming[0]["signal_id"], upcoming[0]["route_index"]):
        return False, "This is the next junction; it already has priority."

    if junction["signal_id"] == active_junction.get("signal_id"):
        return False, (
            "This signal controller is already held for the ambulance "
            "at another point of the route."
        )

    if key in held_requests:
        return False, "Priority was already granted for this junction."

    if junction["distance"] > MAX_REQUEST_DISTANCE_METERS:
        return False, (
            f"Too far ahead ({junction['distance']:.0f} m). Early green is "
            f"only allowed within {MAX_REQUEST_DISTANCE_METERS} m, so cross "
            "traffic is not stopped for too long."
        )

    # The nearest junction always counts as one held junction.
    if 1 + len(held_requests) >= MAX_HELD_JUNCTIONS:
        return False, (
            f"At most {MAX_HELD_JUNCTIONS} junctions may be held green at "
            "once. Release another request first."
        )

    return True, "Granted."


def is_expired(granted_at, now):
    return now - granted_at > MAX_HOLD_SECONDS
