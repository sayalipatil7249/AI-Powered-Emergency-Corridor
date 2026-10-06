"""
Junction referee: several ambulances, one set of signals.

Every ambulance has its own corridor engine (corridor/engine.py). Without
a referee two engines would both set the same signal every second, each
to its own green, and the light would flicker between them. The engines
therefore ask the referee before they take over a signal:

    claim()    every step, each engine reports the signals ahead of its
               ambulance: when it arrives, how long before that the
               signal must start switching (lead), which movements it
               needs green, and the colours it would show.
    acquire()  an engine wants to start switching a signal now:
                 "go"     it may (it now owns the signal)
                 "share"  another ambulance owns it, but that green is
                          also green for this ambulance: ride along
                 "wait"   give way: the signal is, or will first be,
                          green for another ambulance
    release()  the ambulance has passed; the next one may take it.

Who goes first when both need the signal: the order with the smaller
total weighted delay. Each ambulance needs the signal green from
    need = arrival - lead     (0 when it is stuck in the queue before
                               the signal: only the green frees it)
until it has passed (arrival + PASS_SECONDS). Serving A first makes B's
green late by
    max(0, A's passing time - B's need)
and each delay counts by the ambulance's priority weight
(corridor/priority.py: the patient's condition, plus time already stood
still). When the ambulances are far apart both orders cost nothing extra
and the earlier one simply goes first; priority decides the near-ties.

A signal is never taken away while it is changing (yellow, all red) or
when its ambulance is close (PROTECT_SECONDS). But an ambulance that took
a signal to clear the queue in front of it can still be minutes away
once the queue has gone; another ambulance at the stop line may then
cross first ("cut in") when that costs clearly less weighted delay
(CUT_IN_FACTOR, so decisions do not flip back and forth).

Uses no simulator code.
"""

import logging
from collections import deque

from corridor import priority

logger = logging.getLogger(__name__)

# Seconds an ambulance needs from the stop line to clear the junction.
PASS_SECONDS = 4

# Yellow + all red before the signal is green for the next ambulance.
SWITCH_SECONDS = 5

# A claim not refreshed for this long is ignored (s).
CLAIM_TTL_SECONDS = 3

# Reason for waiting: the other ambulance already owns the signal.
ALREADY_SWITCHING = "already switching"

# An ambulance this close to its signal (s) keeps it, whatever happens.
PROTECT_SECONDS = 20

# Cutting in must save this many times the delay it causes (weighted).
CUT_IN_FACTOR = 1.5


def _green(state, index):
    return index < len(state) and state[index] in "Gg"


def compatible(links, target):
    """True if every link in `links` is green in the colours `target`."""
    return bool(target) and all(_green(target, index) for index in links)


class JunctionReferee:
    def __init__(self, notify=None):
        # notify(text): referee decisions for the dashboard feed.
        self.notify = notify or (lambda text: None)

        # ambulance id -> {"label", "condition", "stood_still": fn}
        self.ambulances = {}

        # signal id -> {ambulance id: claim}
        self.claims = {}

        # signal id -> {"ambulance_id", "target", "since", "sharers": set}
        self.owners = {}

        # ambulance id -> give-way instruction (see _wait); only current
        # while its "updated" is the latest time
        self.instructions = {}

        # (signal id, ambulance id) -> the wait going on there:
        # {"since", "last", "decision"}
        self._waiting = {}

        self.decisions = deque(maxlen=50)
        self.now = 0.0

    # -------------------------------------------------------------
    # Ambulances
    # -------------------------------------------------------------

    def register(self, ambulance_id, label, condition, stood_still=None):
        self.ambulances[ambulance_id] = {
            "label": label,
            "condition": condition,
            "stood_still": stood_still or (lambda: 0.0),
        }

    def set_condition(self, ambulance_id, condition):
        if ambulance_id in self.ambulances:
            self.ambulances[ambulance_id]["condition"] = condition

    def forget(self, ambulance_id):
        """The ambulance has arrived (or left): drop its claims and
        instructions. Its engine restores the signals it held."""

        for claims in self.claims.values():
            claims.pop(ambulance_id, None)
        for owner in self.owners.values():
            owner["sharers"].discard(ambulance_id)
        self.instructions.pop(ambulance_id, None)
        for key in [key for key in self._waiting if key[1] == ambulance_id]:
            self._end_wait(key)

    def weight(self, ambulance_id):
        info = self.ambulances.get(ambulance_id)
        if info is None:
            return priority.LEVEL_WEIGHTS[3]
        try:
            stood_still = info["stood_still"]()
        except Exception:
            stood_still = 0.0
        return priority.weight(info["condition"], stood_still)

    def _who(self, ambulance_id):
        """'Ambulance 2 (Stable)'."""
        info = self.ambulances.get(ambulance_id)
        if info is None:
            return ambulance_id
        level = priority.LEVEL_NAMES[priority.level(info["condition"])]
        return f"{info['label']} ({level})"

    # -------------------------------------------------------------
    # Claims
    # -------------------------------------------------------------

    def claim(self, ambulance_id, junction, now):
        """
        junction: {"signal_id", "name", "links", "target",
        "arrival_seconds", "lead_seconds"} for a signal ahead. Several
        crossings of one signal: the nearest counts.
        """

        self.now = now
        signal_claims = self.claims.setdefault(junction["signal_id"], {})
        existing = signal_claims.get(ambulance_id)
        if (
            existing is not None
            and existing["updated"] == now
            and existing["arrival_seconds"] <= junction["arrival_seconds"]
        ):
            return
        signal_claims[ambulance_id] = {**junction, "updated": now}

    def keep_claims(self, ambulance_id, signal_ids):
        """Drop this ambulance's claims on signals no longer ahead."""

        for signal_id, claims in self.claims.items():
            if signal_id not in signal_ids and ambulance_id in claims:
                del claims[ambulance_id]
                self._end_wait((signal_id, ambulance_id))
                instruction = self.instructions.get(ambulance_id)
                if instruction and instruction["signal_id"] == signal_id:
                    del self.instructions[ambulance_id]

    def _fresh(self, signal_id, other_than):
        return [
            (ambulance_id, claim)
            for ambulance_id, claim in self.claims.get(signal_id, {}).items()
            if ambulance_id != other_than
            and self.now - claim["updated"] <= CLAIM_TTL_SECONDS
        ]

    # -------------------------------------------------------------
    # Taking and giving back a signal
    # -------------------------------------------------------------

    def owner(self, signal_id):
        entry = self.owners.get(signal_id)
        return entry["ambulance_id"] if entry else None

    def acquire(self, ambulance_id, signal_id, links, target, now):
        """May this ambulance start switching the signal now?
        Returns "go", "share" or "wait"."""

        self.now = now
        entry = self.owners.get(signal_id)

        if entry is not None and entry["ambulance_id"] == ambulance_id:
            self._granted(ambulance_id, signal_id)
            return "go"

        if entry is not None:
            if compatible(links, entry["target"]):
                entry["sharers"].add(ambulance_id)
                self._granted(ambulance_id, signal_id)
                return "share"
            if self._may_cut_in(ambulance_id, signal_id, entry):
                self._cut_in(ambulance_id, signal_id, entry, target)
                return "go"
            self._wait(
                ambulance_id, signal_id, entry["ambulance_id"],
                ALREADY_SWITCHING,
            )
            return "wait"

        # Nobody holds it: would going now block someone who should go first?
        mine = self.claims.get(signal_id, {}).get(ambulance_id)
        for other_id, other in self._fresh(signal_id, ambulance_id):
            if compatible(other["links"], target):
                continue  # my green is green for them too
            if mine is None or not self._goes_first(ambulance_id, mine, other_id, other):
                reason = (
                    "needs the signal first"
                    if mine is not None
                    and self._needs_green_in(other) < self._needs_green_in(mine)
                    else "has the higher priority"
                )
                self._wait(ambulance_id, signal_id, other_id, reason)
                return "wait"

        self.owners[signal_id] = {
            "ambulance_id": ambulance_id,
            "target": target,
            "since": now,
            "sharers": set(),
        }
        self._granted(ambulance_id, signal_id)
        return "go"

    def release(self, ambulance_id, signal_id):
        """The ambulance no longer needs the signal. Returns True when
        another ambulance riding the same green takes it over (the
        caller must then leave the lights alone)."""

        entry = self.owners.get(signal_id)
        if entry is None:
            return False
        if entry["ambulance_id"] != ambulance_id:
            entry["sharers"].discard(ambulance_id)
            return False

        del self.owners[signal_id]
        sharers = sorted(entry["sharers"], key=self.weight, reverse=True)
        if not sharers:
            return False

        heir = sharers[0]
        claim = self.claims.get(signal_id, {}).get(heir)
        self.owners[signal_id] = {
            "ambulance_id": heir,
            "target": claim["target"] if claim else entry["target"],
            "since": self.now,
            "sharers": set(sharers[1:]),
        }
        return True

    @staticmethod
    def _needs_green_in(claim):
        """Seconds until this ambulance needs the signal green."""
        if claim.get("stuck"):
            return 0.0
        return max(0.0, claim["arrival_seconds"] - claim["lead_seconds"])

    @staticmethod
    def _passed_in(claim):
        return claim["arrival_seconds"] + PASS_SECONDS

    def _may_cut_in(self, ambulance_id, signal_id, entry):
        """May this ambulance take the signal from its owner, who is still
        far away (see the module notes)?"""

        if self.now - entry["since"] < SWITCH_SECONDS:
            return False  # still changing for the owner
        owner_id = entry["ambulance_id"]
        owner = dict(self._fresh(signal_id, ambulance_id)).get(owner_id)
        mine = self.claims.get(signal_id, {}).get(ambulance_id)
        if owner is None or mine is None:
            return False  # the owner is at / in the junction
        if owner["arrival_seconds"] <= PROTECT_SECONDS:
            return False

        # Waiting: until the owner has passed and the signal has changed.
        my_delay = max(
            0.0, self._passed_in(owner) + SWITCH_SECONDS - self._needs_green_in(mine)
        )
        # Cutting in: the owner's green is gone while this one changes the
        # signal, crosses, and it changes back. Counted in full: a queue in
        # front of the owner may be draining through that green.
        owner_delay = self._passed_in(mine) + 2 * SWITCH_SECONDS
        return (
            self.weight(ambulance_id) * my_delay
            > CUT_IN_FACTOR * self.weight(owner_id) * owner_delay
        )

    def _cut_in(self, ambulance_id, signal_id, entry, target):
        owner_id = entry["ambulance_id"]
        owner = self.claims.get(signal_id, {}).get(owner_id, {})
        self.owners[signal_id] = {
            "ambulance_id": ambulance_id,
            "target": target,
            "since": self.now,
            "sharers": set(),
        }
        name = self._signal_name(signal_id, ambulance_id)
        text = (
            f"{name}: {self._who(ambulance_id)} goes first. "
            f"{self._who(owner_id)} is still {owner.get('arrival_seconds', 0):.0f} s away."
        )
        self.decisions.append({
            "time": self.now,
            "signal_id": signal_id,
            "signal_name": name,
            "winner": ambulance_id,
            "loser": owner_id,
            "reason": "cut in",
            "text": text,
            "waited_seconds": None,
        })
        logger.info("Referee: %s", text)
        self.notify(text)
        self._granted(ambulance_id, signal_id)

    def _goes_first(self, mine_id, mine, other_id, other):
        """Honor patient severity when two incompatible green windows overlap.

        Waiting time breaks ties within a level. If the windows do not
        overlap, the earlier ambulance can use the signal without delaying
        the other; an already switching signal is handled by acquire().
        """

        mine_needs = self._needs_green_in(mine)
        other_needs = self._needs_green_in(other)
        competing = (
            mine_needs < self._passed_in(other) + SWITCH_SECONDS
            and other_needs < self._passed_in(mine) + SWITCH_SECONDS
        )
        if competing:
            mine_level = priority.level(self.ambulances[mine_id]["condition"])
            other_level = priority.level(self.ambulances[other_id]["condition"])
            if mine_level != other_level:
                return mine_level < other_level

        delay_other = max(0.0, self._passed_in(mine) - other_needs)
        delay_mine = max(0.0, self._passed_in(other) - mine_needs)
        cost_mine_first = self.weight(other_id) * delay_other
        cost_other_first = self.weight(mine_id) * delay_mine

        if abs(cost_mine_first - cost_other_first) > 1e-6:
            return cost_mine_first < cost_other_first
        if mine["arrival_seconds"] != other["arrival_seconds"]:
            return mine["arrival_seconds"] < other["arrival_seconds"]
        return mine_id < other_id

    # -------------------------------------------------------------
    # Give-way instructions and decisions
    # -------------------------------------------------------------

    def free_in(self, ambulance_id, signal_id):
        """Seconds until the signal can turn green for this ambulance
        (the other ambulance's arrival + passing + this one's switch)."""

        entry = self.owners.get(signal_id)
        holder = entry["ambulance_id"] if entry else None
        instruction = self.instructions.get(ambulance_id)
        if holder is None and instruction:
            holder = instruction["give_way_to_id"]
        claim = self.claims.get(signal_id, {}).get(holder) if holder else None
        if claim is None:
            return 0.0
        return max(0.0, self._passed_in(claim)) + SWITCH_SECONDS

    def _wait(self, ambulance_id, signal_id, other_id, reason):
        key = (signal_id, ambulance_id)
        name = self._signal_name(signal_id, ambulance_id)

        wait = self._waiting.get(key)
        if wait is not None and self.now - wait["last"] > CLAIM_TTL_SECONDS:
            self._end_wait(key)  # it had stopped asking: a new wait
            wait = None

        if wait is None:
            why = {
                ALREADY_SWITCHING: "the signal is already turning for it",
                "needs the signal first": "it gets there first",
                "has the higher priority": "its patient is more serious",
            }.get(reason, reason)
            text = (
                f"{name}: {self._who(ambulance_id)} lets "
                f"{self._who(other_id)} go first ({why})."
            )
            decision = {
                "time": self.now,
                "signal_id": signal_id,
                "signal_name": name,
                "winner": other_id,
                "loser": ambulance_id,
                "reason": reason,
                "text": text,
                "waited_seconds": None,
            }
            self.decisions.append(decision)
            wait = {"since": self.now, "last": self.now, "decision": decision}
            self._waiting[key] = wait
            logger.info("Referee: %s", text)
            self.notify(text)

        wait["last"] = self.now
        self.instructions[ambulance_id] = {
            "signal_id": signal_id,
            "signal_name": name,
            "give_way_to_id": other_id,
            "give_way_to": self._who(other_id),
            "reason": reason,
            "since": wait["since"],
            "updated": self.now,
            "free_in_seconds": round(self.free_in(ambulance_id, signal_id), 1),
        }

    def _end_wait(self, key, granted=False):
        """A wait is over: the green came (granted), or the ambulance
        stopped asking / passed / left."""

        wait = self._waiting.pop(key, None)
        if wait is None:
            return
        end = self.now if granted else wait["last"] + 1
        wait["decision"]["waited_seconds"] = round(max(0.0, end - wait["since"]))
        return wait["decision"]["waited_seconds"]

    def _granted(self, ambulance_id, signal_id):
        key = (signal_id, ambulance_id)
        instruction = self.instructions.get(ambulance_id)
        if instruction and instruction["signal_id"] == signal_id:
            del self.instructions[ambulance_id]

        wait = self._waiting.get(key)
        if wait is None:
            return
        granted = self.now - wait["last"] <= CLAIM_TTL_SECONDS
        waited = self._end_wait(key, granted=granted)
        if granted:
            text = (
                f"{self._signal_name(signal_id, ambulance_id)}: "
                f"{self._who(ambulance_id)} gets green after {waited} s."
            )
            logger.info("Referee: %s", text)
            self.notify(text)

    def current_instruction(self, ambulance_id):
        """The give-way instruction given this step, or None."""
        instruction = self.instructions.get(ambulance_id)
        if instruction and instruction["updated"] >= self.now:
            return instruction
        return None

    def _signal_name(self, signal_id, ambulance_id):
        claim = self.claims.get(signal_id, {}).get(ambulance_id)
        if claim is None:
            for other in self.claims.get(signal_id, {}).values():
                claim = other
                break
        name = claim.get("name") if claim else None
        return name or f"Signal {signal_id[:12]}"

    def summary(self):
        """For the dashboard and the admin view."""

        return {
            "owners": [
                {
                    "signal_id": signal_id,
                    "signal_name": self._signal_name(signal_id, entry["ambulance_id"]),
                    "ambulance_id": entry["ambulance_id"],
                    "sharers": sorted(entry["sharers"]),
                    "since": entry["since"],
                }
                for signal_id, entry in self.owners.items()
            ],
            "instructions": {
                ambulance_id: dict(instruction)
                for ambulance_id, instruction in self.instructions.items()
                if self.now - instruction["updated"] <= 1
            },
            "decisions": list(self.decisions),
        }
