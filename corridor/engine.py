"""
The emergency corridor: which signals lie ahead of the ambulance, and
turning the next one green for the ambulance's movement - just in time.

The signal ahead keeps its normal cycle until the ambulance is predicted
(by the AI next-signal model) to arrive within the time the junction
needs: yellow + all-red for cross traffic, time for the cars queued in
front of the ambulance to drive off, and a safety margin. Real
pre-emption systems work the same way; turning every signal green as
soon as it is next would stop cross traffic for minutes.

Uses only the connectors in corridor/interfaces.py, so it works the
same on SUMO today and on real signals later.

A junction on the route is identified by (signal_id, route_index):
the same signal can be passed twice at different points of a route.
"""

import logging
from collections import deque

from corridor import safety

logger = logging.getLogger(__name__)

# Number of upcoming signals included in the moving corridor.
CORRIDOR_LOOKAHEAD = 6

# Role of each upcoming signal, by position (the rest are UPCOMING).
CORRIDOR_ROLES = ["ACTIVE", "PREPARING", "STANDBY"]

# Switching a signal for the ambulance (seconds).
YELLOW_SECONDS = 3            # cross traffic sees yellow...
ALL_RED_SECONDS = 2           # ...then all red, then green for the ambulance
QUEUE_START_SECONDS = 2       # first queued car starting to move
QUEUE_HEADWAY_SECONDS = 2     # each further queued car
SAFETY_MARGIN_SECONDS = 5     # for prediction error
MAX_LEAD_SECONDS = 45         # never switch earlier than this

# With the AI clearance model (ai/clearance.py): its cautious prediction
# of the clearance time (which already includes yellow and all red) plus
# this margin, capped.
AI_SAFETY_MARGIN_SECONDS = 3
AI_MAX_LEAD_SECONDS = 60

# A queue can stretch over several road pieces before the junction.
# The whole queue in front of the ambulance must drive off: this long
# per stopped vehicle per lane (and at most MAX_QUEUE_LEAD_SECONDS).
# While such a queue holds the ambulance back, its arrival is judged by
# how fast it would get there once the road clears (CLEARED_SPEED),
# not by its current speed - otherwise a stuck ambulance always looks
# "far away" and the signal never switches.
MAX_QUEUE_LEAD_SECONDS = 90
CLEARED_SPEED = 10.0  # m/s

# Stuck in the queue: when the ambulance is crawling below this speed
# with vehicles stopped between it and the next signal, that queue is
# what holds it up, so the signal switches at once. Queues drain much
# slower than "2 s per car" when they pass small unsignalled junctions.
STUCK_SPEED = 2.0  # m/s

# Signals this close after the next one belong to the same physical
# junction (big junctions are often several signal controllers a few
# metres apart). They switch together; otherwise the ambulance and the
# cars in front of it get stuck inside the junction at the next one.
CLUSTER_METERS = 60

# Switch anyway this close to the junction (a crawling ambulance can
# have a long predicted arrival time while right at the stop line).
ALWAYS_SWITCH_METERS = 60

# Without a prediction: distance / this speed (m/s).
FALLBACK_SPEED = 8.0

# With other ambulances (corridor/referee.py): signals up to this far
# ahead are claimed, so the referee knows who arrives when.
CLAIM_METERS = 1500

# Giving way: the ambulance is slowed so it reaches the stop line only
# once the signal can turn green for it, and waits this far before it.
GIVE_WAY_STOP_METERS = 10
GIVE_WAY_CRAWL_SPEED = 0.5  # m/s, the slowest advised speed


def transition_states(current, target_state):
    """
    Light colours for switching from `current` to the phase
    `target_state`: (yellow, all_red). Movements losing green get
    yellow; then everything is red except movements that stay green.
    """

    def green(states, index):
        return index < len(states) and states[index] in "Gg"

    yellow = "".join(
        "y" if light in "Gg" and not green(target_state, index) else light
        for index, light in enumerate(current)
    )
    all_red = "".join(
        light if light in "Gg" and green(target_state, index) else "r"
        for index, light in enumerate(current)
    )
    return yellow, all_red


def green_lead_seconds(queued_cars):
    """How long before the ambulance arrives a signal must start
    switching: yellow + all-red + queue clearing + safety margin."""

    return min(
        MAX_LEAD_SECONDS,
        YELLOW_SECONDS + ALL_RED_SECONDS + SAFETY_MARGIN_SECONDS
        + QUEUE_START_SECONDS + QUEUE_HEADWAY_SECONDS * queued_cars,
    )


def _road_of(lane_id):
    return lane_id.rsplit("_", 1)[0]


class CorridorEngine:
    def __init__(self, ambulance, traffic, signals,
                 lookahead=CORRIDOR_LOOKAHEAD, clearance_predictor=None,
                 timing="ai", referee=None):
        self.ambulance = ambulance

        # With several ambulances: the junction referee
        # (corridor/referee.py) decides who may take over a signal.
        # None: this engine is the only one and switches freely.
        self.referee = referee

        # The referee's give-way instruction for the junction ahead, and
        # the speed this ambulance was slowed to (None: not slowed).
        self.give_way = None
        self.advised_speed = None

        # (signal_id, links) -> (phase, emergency colours, colours shown)
        self._targets = {}

        # Junctions another ambulance cut in at: asked for again at once,
        # so the queue that was draining through them keeps moving.
        self._reclaim = set()

        # When the junction ahead switches: "ai" (AI clearance model,
        # the rule if none is trained), "rule" (green_lead_seconds), or
        # "immediate" (as soon as it is the next junction; the old
        # behaviour, kept for comparison).
        self.timing = timing

        # (signal_id, tls_index) -> predicted seconds the junction needs
        # to get ready (AI model), or None; without it the fixed rule
        # green_lead_seconds is used.
        self.clearance_predictor = clearance_predictor
        self.traffic = traffic
        self.signals = signals
        self.lookahead = lookahead

        # Every signal on the route, in order (for the dashboard).
        self.route_signals = []

        # Junction currently given priority, and every signal whose
        # normal program was overridden.
        self.active_junction = None
        self.overridden_signals = set()

        # Extra early-green requests (from an AI agent or operator),
        # checked by corridor/safety.py:
        # {(signal_id, route_index): {"tls_index", "granted_at", "reason"}}
        self.priority_requests = {}

        # Junctions switching for the ambulance (yellow, then all red):
        # {key: {"signal_id", "started", "target", "yellow", "red", "stage"}}
        # and junctions held green: {key: {"signal_id", "phase"}}.
        self.switching = {}
        self.held = {}

        # Timing of the junction ahead, for the dashboard and the agent.
        self.next_timing = None

        # Recent decisions, newest last, for explanations.
        self.events = deque(maxlen=100)
        self.now = None

    # -------------------------------------------------------------
    # Route signals
    # -------------------------------------------------------------

    def build_route_signals(self):
        """Find, once per trip, every signal on the ambulance route."""

        if self.route_signals or not self.ambulance.is_on_road():
            return

        route = self.ambulance.route()

        positions = {}
        for index, road_id in enumerate(route):
            positions.setdefault(road_id, []).append(index)

        keys = set()

        for signal_id in self.signals.signal_ids():
            for link_group in self.signals.controlled_links(signal_id):
                for link in link_group:
                    if not link:
                        continue
                    for route_index in positions.get(_road_of(link[0]), []):
                        keys.add((signal_id, route_index))

        self.route_signals = []
        number = 0
        previous_signal = None

        for signal_id, route_index in sorted(keys, key=lambda key: (key[1], key[0])):
            # Crossing the same signal again straight away (a big joined
            # junction) is the same junction for the driver: same number.
            if signal_id != previous_signal:
                number += 1
            previous_signal = signal_id

            route_signal = {
                "number": number,
                "signal_id": signal_id,
                "name": self.signals.name(signal_id, route[route_index]),
                "route_index": route_index,
                "latitude": None,
                "longitude": None,
            }

            position = self.signals.position(signal_id)
            if position:
                route_signal.update(position)

            self.route_signals.append(route_signal)

        logger.info("Route has %d traffic signals.", len(self.route_signals))

    # -------------------------------------------------------------
    # Upcoming signals
    # -------------------------------------------------------------

    def upcoming_signals(self):
        """
        The next `lookahead` junctions ahead of the ambulance, nearest
        first: [{"signal_id", "route_index", "tls_index", "distance",
        "sumo_state", "movement_match"}].

        tls_index is the link index of the ambulance's own movement
        through the junction (the one that continues along its route).
        """

        if not self.ambulance.is_on_road():
            return []

        route = self.ambulance.route()
        if not route:
            return []

        current_index = self.ambulance.route_index()
        current_lane = self.ambulance.lane_id()
        lane_position = self.ambulance.lane_position()

        positions = {}
        for index, road_id in enumerate(route):
            positions.setdefault(road_id, []).append(index)

        candidates = self._junction_candidates(
            route, positions, current_index
        )

        upcoming = []

        for candidate in candidates.values():
            distance = self._distance_to(
                candidate, route, current_index, current_lane, lane_position
            )

            light_states = self.signals.light_states(candidate["signal_id"])
            tls_index = candidate["tls_index"]

            upcoming.append({
                "signal_id": candidate["signal_id"],
                "route_index": candidate["route_index"],
                "tls_index": tls_index,
                "distance": round(distance, 2),
                "sumo_state": (
                    light_states[tls_index]
                    if tls_index < len(light_states)
                    else None
                ),
                "movement_match": candidate["movement_match"],
            })

        upcoming.sort(key=lambda item: (item["route_index"], item["distance"]))

        return upcoming[: self.lookahead]

    def _junction_candidates(self, route, positions, current_index):
        """
        One candidate movement per junction ahead, preferring the
        movement whose outgoing road is the ambulance's next road.
        """

        candidates = {}

        for signal_id in self.signals.signal_ids():
            links = self.signals.controlled_links(signal_id)

            for tls_index, link_group in enumerate(links):
                for link in link_group:
                    if not link:
                        continue

                    incoming_lane = link[0]
                    incoming_road = _road_of(incoming_lane)

                    if incoming_road not in positions:
                        continue

                    outgoing_road = (
                        _road_of(link[1]) if len(link) > 1 and link[1]
                        else None
                    )

                    for route_index in positions[incoming_road]:
                        if route_index < current_index:
                            continue

                        next_road = (
                            route[route_index + 1]
                            if route_index + 1 < len(route)
                            else None
                        )

                        candidate = {
                            "signal_id": signal_id,
                            "route_index": route_index,
                            "tls_index": tls_index,
                            "incoming_lane": incoming_lane,
                            "movement_match": outgoing_road == next_road,
                        }

                        key = (signal_id, route_index)
                        existing = candidates.get(key)

                        if existing is None or (
                            candidate["movement_match"]
                            and not existing["movement_match"]
                        ):
                            candidates[key] = candidate

        return candidates

    def _distance_to(self, candidate, route, current_index,
                     current_lane, lane_position):
        """Metres from the ambulance to the stop line of a junction."""

        rest_of_current = max(
            0.0, self.traffic.lane_length(current_lane) - lane_position
        )

        if candidate["route_index"] == current_index:
            return rest_of_current

        distance = rest_of_current

        for index in range(current_index + 1, candidate["route_index"]):
            distance += self.traffic.lane_length(f"{route[index]}_0")

        distance += self.traffic.lane_length(candidate["incoming_lane"])

        return distance

    # -------------------------------------------------------------
    # Signal control
    # -------------------------------------------------------------

    def apply(self, upcoming, now, seconds_to_next=None):
        """
        Switch the junction ahead to green for the ambulance just in
        time, and return the previous one to normal once passed. Also
        switches any granted priority requests further ahead.

        now: simulation time (s). seconds_to_next: predicted seconds
        until the ambulance reaches the junction ahead (AI model), or
        None to estimate from distance.
        """

        self.now = now

        # Signals the ambulance has passed go back to normal.
        ahead = {self._key(item) for item in upcoming}
        self._reclaim &= ahead
        for passed in [
            key for key in list(self.switching) + list(self.held)
            if key not in ahead and key not in self.priority_requests
        ]:
            logger.info("Passed %s (route index %s), back to normal.", *passed)
            self._event(
                "restored",
                {"signal_id": passed[0], "route_index": passed[1]},
                "Ambulance passed; signal back to its normal cycle.",
            )
            self._restore_junction(passed)

        if not upcoming:
            self.active_junction = None
            self.next_timing = None
            for key in list(self.priority_requests):
                self._release_request(key, "the ambulance has no signals ahead")
            if self.referee is not None:
                self.referee.keep_claims(self.ambulance.vehicle_id, set())
                self.give_way = None
                self._advise_speed(None)
            return

        nearest = upcoming[0]
        junction = {
            "signal_id": nearest["signal_id"],
            "route_index": nearest["route_index"],
            "tls_index": nearest["tls_index"],
        }
        key = self._key(junction)
        self.active_junction = junction

        # The next junction, plus signals right behind it (same junction).
        # (including further crossings of the same signal: a big joined
        # junction can be crossed several times and switches as one).
        cluster = [nearest] + [
            item for item in upcoming[1:]
            if item["distance"] - nearest["distance"] <= CLUSTER_METERS
        ]


        # When must this junction start switching?
        if seconds_to_next is None:
            seconds_to_next = nearest["distance"] / FALLBACK_SPEED
        queued = self.traffic.road_halting_count(
            self.ambulance.route()[nearest["route_index"]]
        )
        lead, lead_source = self._lead_seconds(nearest, queued)

        # The whole queue between the ambulance and the stop line.
        queued_ahead = self._queued_ahead(nearest)
        queue_lead = min(
            MAX_QUEUE_LEAD_SECONDS,
            YELLOW_SECONDS + ALL_RED_SECONDS + QUEUE_START_SECONDS
            + QUEUE_HEADWAY_SECONDS * queued_ahead + SAFETY_MARGIN_SECONDS,
        )
        if queued_ahead >= 1:
            seconds_to_next = min(
                seconds_to_next, nearest["distance"] / CLEARED_SPEED
            )
            if queue_lead > lead:
                lead, lead_source = queue_lead, f"{lead_source} + queue ahead"

        stuck_in_queue = (
            queued_ahead >= 1 and self.ambulance.speed() < STUCK_SPEED
        )
        if stuck_in_queue:
            lead_source = "stuck in the queue"

        if self.referee is not None:
            self._claim(upcoming, nearest, seconds_to_next, lead, stuck_in_queue)

        started = (
            key in self.switching or key in self.held or key in self._reclaim
        )
        if started or (
            seconds_to_next <= lead
            or stuck_in_queue
            or nearest["distance"] <= ALWAYS_SWITCH_METERS
        ):
            why = (
                f"Ambulance {seconds_to_next:.0f} s away "
                f"({nearest['distance']:.0f} m), {queued} cars queued in "
                f"front: switching now ({lead_source} timing, needs "
                f"{lead:.0f} s)."
            )
            for item in cluster:
                item_key = self._key(item)
                if item_key not in self.switching and item_key not in self.held:
                    switched = self._start_switch(
                        item,
                        why if item is nearest else
                        f"Part of the same junction as {nearest['signal_id']}: "
                        "switching together.",
                    )
                    # Giving way to another ambulance: the rest of the
                    # junction waits too.
                    if not switched:
                        break

        if self.referee is not None:
            instruction = self.referee.current_instruction(self.ambulance.vehicle_id)
            self.give_way = (
                {**instruction, "distance_meters": round(nearest["distance"])}
                if instruction
                and instruction["signal_id"] in {item["signal_id"] for item in cluster}
                else None
            )
            self._advise_speed(nearest["distance"] if self.give_way else None)

        # Keep every switched signal ahead moving on / held green.
        for switched in list(self.switching) + list(self.held):
            if switched in ahead:
                self._advance(switched)

        self.next_timing = {
            "signal_id": nearest["signal_id"],
            "route_index": nearest["route_index"],
            "seconds_to_arrival": round(seconds_to_next, 1),
            "lead_seconds": round(lead, 1) if lead != float("inf") else None,
            "lead_source": lead_source,
            "queued_cars": queued,
            "queued_ahead_per_lane": round(queued_ahead, 1),
            "stage": "give_way" if self.give_way else self.stage(key),
            "switch_in_seconds": (
                round(max(0.0, seconds_to_next - lead), 1)
                if self.stage(key) == "normal" and lead != float("inf")
                else 0.0
            ),
        }

        self._hold_requests(upcoming)

    def _queued_ahead(self, junction):
        """Stopped vehicles per lane between the ambulance and the stop
        line of `junction` (all road pieces up to it)."""

        route = self.ambulance.route()
        current_index = self.ambulance.route_index()
        total = 0.0

        def per_lane(road_id):
            return self.traffic.road_halting_count(road_id) / max(
                1, self.traffic.road_lane_count(road_id)
            )

        road_id = self.ambulance.road_id()
        if not road_id.startswith(":"):
            # Only the part of the current road ahead of the ambulance.
            length = self.traffic.lane_length(self.ambulance.lane_id())
            if length > 0:
                ahead = max(0.0, length - self.ambulance.lane_position())
                total += per_lane(road_id) * ahead / length

        for index in range(current_index + 1, junction["route_index"] + 1):
            total += per_lane(route[index])

        return total

    def _lead_seconds(self, junction, queued):
        """How long before the ambulance arrives this junction must start
        switching: (seconds, "ai" or "rule")."""

        if self.timing == "immediate":
            return float("inf"), "immediate"

        if self.timing == "ai" and self.clearance_predictor is not None:
            predicted = self.clearance_predictor(
                junction["signal_id"], junction["tls_index"]
            )
            if predicted is not None:
                return (
                    min(AI_MAX_LEAD_SECONDS, predicted + AI_SAFETY_MARGIN_SECONDS),
                    "ai",
                )
        return green_lead_seconds(queued), "rule"

    # -------------------------------------------------------------
    # Switching one junction: yellow -> all red -> held green
    # -------------------------------------------------------------

    @staticmethod
    def _key(junction):
        return (junction["signal_id"], junction["route_index"])

    def stage(self, key):
        """"normal", "yellow", "all_red" or "green" for a junction."""
        if key in self.held:
            return "green"
        if key in self.switching:
            return self.switching[key]["stage"]
        return "normal"

    def _ambulance_links(self, signal_id):
        """Link indices of this signal on the ambulance's path ahead:
        every lane of every movement from a route road to the next one
        (a big "joined" junction can be crossed several times)."""

        route = self.ambulance.route()
        start = self.ambulance.route_index()
        next_road = {
            route[index]: route[index + 1]
            for index in range(start, len(route) - 1)
        }

        links = set()
        for tls_index, group in enumerate(self.signals.controlled_links(signal_id)):
            for link in group:
                if link and next_road.get(_road_of(link[0])) == _road_of(link[1]):
                    links.add(tls_index)
        return links

    def _green_target(self, signal_id, needed):
        """
        What to show for the ambulance's links `needed`: (phase number,
        None) when one phase of the normal program gives its whole path
        through the signal a protected green ("G"), otherwise (None,
        emergency colours): green for the ambulance's links and for links
        green in every phase it needs, red for everything else.

        A movement that only ever gets a give-way green ("g", e.g. a turn
        across oncoming traffic) is never served by a normal phase: the
        oncoming traffic keeps its green and the ambulance has to wait
        for a gap. It gets emergency colours instead, as real emergency
        pre-emption does: green for every movement from the ambulance's
        own approach road(s) - the cars queued in front of it may be
        going elsewhere and must drive off - and red for all others.
        """

        phases = self.signals.phase_states(signal_id)

        def green(state, index):
            return index < len(state) and state[index] in "Gg"

        def protected(state, index):
            return index < len(state) and state[index] == "G"

        for number, state in enumerate(phases):
            if all(protected(state, index) for index in needed):
                return number, None

        used = [state for state in phases if any(green(state, i) for i in needed)]
        if not used:
            return None, None

        give_way_only = any(
            not any(protected(state, index) for state in phases)
            for index in needed
        )

        if give_way_only:
            links = self.signals.controlled_links(signal_id)
            approach = {
                _road_of(link[0])
                for index in needed
                for link in links[index] if link
            }
            green_links = {
                index for index, group in enumerate(links)
                if any(link and _road_of(link[0]) in approach for link in group)
            } | set(needed)
            return None, "".join(
                "G" if index in green_links else "r"
                for index in range(len(used[0]))
            )

        emergency = "".join(
            "G" if index in needed
            else (light if all(green(state, index) for state in used) else "r")
            for index, light in enumerate(used[0])
        )
        return None, emergency

    def _start_switch(self, junction, why):
        """Start switching a junction for the ambulance. Returns False
        when it cannot (no green phase, or giving way to another
        ambulance)."""

        signal_id = junction["signal_id"]
        tls_index = junction["tls_index"]
        key = self._key(junction)

        # Same signal already switched for an earlier crossing of it: that
        # setting already covers the ambulance's whole path through it.
        for other in (self.held, self.switching):
            for other_key, entry in list(other.items()):
                if other_key[0] == signal_id and other_key != key:
                    other[key] = entry
                    return True

        needed = self._ambulance_links(signal_id) | {tls_index}
        phase, emergency, target_state = self._target(signal_id, needed)
        if target_state is None:
            logger.warning("No green phase for %s link %s.", signal_id, tls_index)
            return False

        hold = {"signal_id": signal_id, "phase": phase, "state": emergency}

        if self.referee is not None:
            decision = self.referee.acquire(
                self.ambulance.vehicle_id, signal_id, needed, target_state,
                self.now,
            )
            if decision == "wait":
                return False
            self._reclaim.discard(key)
            if decision == "share":
                # Another ambulance's green is green for this one too:
                # ride along without touching the lights.
                self.held[key] = {**hold, "shared": True}
                self._event(
                    "active", junction,
                    "Signal already green for another ambulance on the "
                    "same movement: sharing it.",
                )
                return True

        current = self.signals.light_states(signal_id)
        self.overridden_signals.add(signal_id)

        # Already green for the ambulance's whole path: keep it green.
        if all(
            index < len(current) and current[index] in "Gg"
            for index in needed
        ):
            self.held[key] = hold
            self._hold(hold)
            self._event("active", junction, f"{why} Already green; held.")
            return True

        yellow, all_red = transition_states(current, target_state)

        self.switching[key] = {
            **hold,
            "started": self.now,
            "yellow": yellow,
            "red": all_red,
            "stage": "yellow",
        }
        self.signals.set_light_states(signal_id, yellow)
        logger.info("Switching %s for the ambulance: %s", signal_id, why)
        self._event("active", junction, why)
        return True

    def _target(self, signal_id, needed):
        """(phase, emergency colours, colours shown) for the ambulance's
        links `needed`; colours shown is None without a green phase."""

        cache_key = (signal_id, frozenset(needed))
        if cache_key not in self._targets:
            phase, emergency = self._green_target(signal_id, needed)
            if phase is None and emergency is None:
                shown = None
            else:
                shown = emergency or self.signals.phase_state(signal_id, phase)
            self._targets[cache_key] = (phase, emergency, shown)
        return self._targets[cache_key]

    def _claim(self, upcoming, nearest, seconds_to_next, lead, stuck):
        """Tell the referee when this ambulance reaches the signals ahead
        (stuck: held up by the queue before the nearest one, which only
        its green can clear)."""

        me = self.ambulance.vehicle_id
        claimed = set()

        for item in upcoming:
            if item is not nearest and item["distance"] > CLAIM_METERS:
                break
            signal_id = item["signal_id"]
            needed = self._ambulance_links(signal_id) | {item["tls_index"]}
            _, _, shown = self._target(signal_id, needed)
            if shown is None:
                continue

            if item is nearest:
                arrival, item_lead = seconds_to_next, lead
            else:
                arrival = item["distance"] / FALLBACK_SPEED
                item_lead = green_lead_seconds(self.traffic.road_halting_count(
                    self.ambulance.route()[item["route_index"]]
                ))
            if item_lead == float("inf"):
                item_lead = MAX_LEAD_SECONDS

            self.referee.claim(me, {
                "signal_id": signal_id,
                "name": self.signals.name(
                    signal_id, self.ambulance.route()[item["route_index"]]
                ),
                "links": needed,
                "target": shown,
                "arrival_seconds": round(arrival, 1),
                "lead_seconds": round(item_lead, 1),
                "distance_meters": round(item["distance"]),
                "stuck": stuck and item is nearest,
            }, self.now)
            claimed.add(signal_id)

        self.referee.keep_claims(me, claimed)

    def _advise_speed(self, distance):
        """Giving way: slow down to reach the stop line only when the
        signal can turn green for this ambulance. distance None: drive
        normally again."""

        speed = None
        if distance is not None and self.give_way is not None:
            free_in = self.referee.free_in(
                self.ambulance.vehicle_id, self.give_way["signal_id"]
            )
            if free_in > 0:
                speed = max(
                    GIVE_WAY_CRAWL_SPEED,
                    (distance - GIVE_WAY_STOP_METERS) / free_in,
                )
                if speed >= self.ambulance.max_speed():
                    speed = None

        if speed is None and self.advised_speed is None:
            return
        if (
            speed is not None and self.advised_speed is not None
            and abs(speed - self.advised_speed) < 0.3
        ):
            return
        self.advised_speed = speed
        self.ambulance.advise_speed(speed)

    def _hold(self, hold):
        if hold.get("shared"):
            return  # another ambulance's engine controls these lights
        if hold["state"]:
            self.signals.set_light_states(hold["signal_id"], hold["state"])
        else:
            self.signals.hold_phase(hold["signal_id"], hold["phase"])

    def _advance(self, key):
        """Move a switching junction on to all red and then green; keep
        held junctions green."""

        if self._taken_over(key):
            return

        if key in self.switching:
            switch = self.switching[key]
            elapsed = self.now - switch["started"]

            if elapsed >= YELLOW_SECONDS + ALL_RED_SECONDS:
                hold = {
                    "signal_id": switch["signal_id"],
                    "phase": switch["phase"],
                    "state": switch["state"],
                }
                for other_key, entry in list(self.switching.items()):
                    if entry is switch:
                        del self.switching[other_key]
                        self.held[other_key] = hold
            elif elapsed >= YELLOW_SECONDS and switch["stage"] == "yellow":
                switch["stage"] = "all_red"
                self.signals.set_light_states(switch["signal_id"], switch["red"])

        if key in self.held:
            hold = self.held[key]
            if (
                hold.get("shared")
                and self.referee.owner(hold["signal_id"]) == self.ambulance.vehicle_id
            ):
                # The ambulance whose green this one shared has passed:
                # this engine now holds the signal itself.
                hold["shared"] = False
                self.overridden_signals.add(hold["signal_id"])
            self._hold(hold)

    def _taken_over(self, key):
        """Another ambulance crossing first took this signal (the referee
        let it cut in): stop holding it, leave its lights to that
        ambulance, and ask for it again later."""

        if self.referee is None:
            return False
        entry = self.held.get(key) or self.switching.get(key)
        if entry is None or entry.get("shared"):
            return False
        signal_id = entry["signal_id"]
        if self.referee.owner(signal_id) == self.ambulance.vehicle_id:
            return False

        for table in (self.held, self.switching):
            for other_key in [k for k in table if k[0] == signal_id]:
                del table[other_key]
                self._reclaim.add(other_key)
        self.overridden_signals.discard(signal_id)
        self._event(
            "restored",
            {"signal_id": key[0], "route_index": key[1]},
            "Another ambulance at the stop line crosses first; this signal "
            "is asked for again when it has passed.",
        )
        return True

    def _restore_junction(self, key):
        held = self.held.pop(key, None)
        switching = self.switching.pop(key, None)

        # Another crossing of the same signal still ahead: keep it.
        still_used = any(
            other[0] == key[0] for other in list(self.held) + list(self.switching)
        )
        if still_used:
            return
        if self._give_back(key[0], shared=bool(held and held.get("shared"))):
            return
        self._restore(key[0], held["phase"] if held else None)
        del switching

    def _give_back(self, signal_id, shared=False):
        """Tell the referee this ambulance is done with the signal.
        True when the lights must be left alone: they belong to another
        ambulance (shared, or handed over to one riding the same green)."""

        if self.referee is None:
            return False
        owner = self.referee.owner(signal_id)
        handed_over = self.referee.release(self.ambulance.vehicle_id, signal_id)
        taken = owner is not None and owner != self.ambulance.vehicle_id
        if shared or handed_over or taken:
            self.overridden_signals.discard(signal_id)
            return True
        return False

    # -------------------------------------------------------------
    # Priority requests (extra early green further ahead)
    # -------------------------------------------------------------

    def request_priority(self, signal_id, route_index, reason=""):
        """
        Ask for early green at a junction ahead of the nearest one.
        The safety rules decide. Returns {"granted": bool, "reason": str}.
        """

        upcoming = self.upcoming_signals()
        junction = next(
            (
                item for item in upcoming
                if item["signal_id"] == signal_id
                and item["route_index"] == route_index
            ),
            None,
        )

        if junction is None:
            return {
                "granted": False,
                "reason": (
                    f"Not one of the next {self.lookahead} signals ahead "
                    "of the ambulance."
                ),
            }

        allowed, why = safety.check_request(
            junction, upcoming, self.priority_requests,
            self.active_junction or {},
        )

        key = (signal_id, route_index)
        detail = f"{why} Requested because: {reason or 'no reason given'}."

        if allowed:
            self.priority_requests[key] = {
                "tls_index": junction["tls_index"],
                "granted_at": self.now if self.now is not None else 0.0,
                "reason": reason,
            }
            logger.info("Priority granted for %s: %s", signal_id, reason)

        self._event(
            "request_granted" if allowed else "request_refused",
            {"signal_id": signal_id, "route_index": route_index},
            detail,
        )

        return {"granted": allowed, "reason": why}

    def release_priority(self, signal_id, route_index):
        key = (signal_id, route_index)
        if key not in self.priority_requests:
            return {"released": False, "reason": "No request for that junction."}
        self._release_request(key, "released on request")
        return {"released": True, "reason": "Signal back to its normal cycle."}

    def _hold_requests(self, upcoming):
        ahead = {(item["signal_id"], item["route_index"]) for item in upcoming}
        nearest_key = (upcoming[0]["signal_id"], upcoming[0]["route_index"])

        for key, request in list(self.priority_requests.items()):
            signal_id, route_index = key

            if key == nearest_key:
                # Now the next junction: the engine handles it itself.
                del self.priority_requests[key]
                continue

            if key not in ahead:
                self._release_request(key, "the ambulance has passed it")
                continue

            if self.now is not None and safety.is_expired(
                request["granted_at"], self.now
            ):
                self._release_request(
                    key, f"held for over {safety.MAX_HOLD_SECONDS} s (fail-safe)"
                )
                continue

            if signal_id == upcoming[0]["signal_id"]:
                # Same controller as the nearest junction: that wins.
                continue

            if key not in self.switching and key not in self.held:
                junction = next(
                    item for item in upcoming
                    if self._key(item) == key
                )
                self._start_switch(
                    junction, f"Early green requested: {request['reason']}"
                )
            self._advance(key)

    def _release_request(self, key, why):
        signal_id, route_index = key
        self.priority_requests.pop(key, None)

        active_signal = (self.active_junction or {}).get("signal_id")
        if signal_id != active_signal:
            self._restore_junction(key)
        else:
            self.held.pop(key, None)
            self.switching.pop(key, None)

        self._event(
            "request_released",
            {"signal_id": signal_id, "route_index": route_index},
            f"Early green ended: {why}.",
        )

    def _event(self, kind, junction, message):
        self.events.append({
            "time": self.now,
            "type": kind,
            "signal_id": junction["signal_id"],
            "route_index": junction["route_index"],
            "message": message,
        })

    def reset(self):
        """Return every overridden signal to normal (trip finished)."""

        logger.info("Trip finished. Restoring all signals to normal.")

        held_phases = {
            hold["signal_id"]: hold.get("phase") for hold in self.held.values()
        }
        shared = {
            hold["signal_id"] for hold in self.held.values() if hold.get("shared")
        }
        for signal_id in shared:
            self._give_back(signal_id, shared=True)
        for signal_id in list(self.overridden_signals):
            if not self._give_back(signal_id):
                self._restore(signal_id, held_phases.get(signal_id))

        self.active_junction = None
        self.next_timing = None
        self.switching.clear()
        self.held.clear()
        self.priority_requests.clear()
        self._targets.clear()
        self._reclaim.clear()
        if self.referee is not None:
            self.give_way = None
            self._advise_speed(None)

    def _restore(self, signal_id, held_phase=None):
        try:
            self.signals.restore_normal(signal_id, held_phase)
        except Exception as error:
            logger.warning("Could not restore %s: %s", signal_id, error)
        self.overridden_signals.discard(signal_id)

    @staticmethod
    def role(index):
        """ACTIVE / PREPARING / STANDBY / UPCOMING by position ahead."""
        return CORRIDOR_ROLES[index] if index < len(CORRIDOR_ROLES) else "UPCOMING"
