"""
MCP server: the live emergency-corridor simulation as tools an AI agent
(e.g. the LangGraph agent, Claude Desktop or Claude Code) can call.

Served by the backend at http://127.0.0.1:8000/mcp (streamable HTTP).

The agent can look (ambulance, traffic, signals, decisions) and can
*request* early green at a signal ahead. It never switches a signal
directly: requests go through the safety rules in corridor/safety.py.

Tools that read the live simulation run inside the simulation loop via
simulation_service.run_in_simulation(), because SUMO may only be driven
from one thread.
"""

import asyncio

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from backend.services.simulation_service import (
    SimulationNotRunning,
    simulation_service,
)
from corridor import feed, safety

mcp = MCPServer(
    name="emergency-corridor",
    title="AI-Powered Emergency Corridor",
    instructions=(
        "Tools for a live traffic simulation of Pune in which an ambulance "
        "drives to a hospital. Traffic signals on its route "
        "turn green automatically just before it reaches them. You can "
        "inspect the ambulance, the traffic on the road ahead and the "
        "signals ahead, and request an early green at a signal further "
        "ahead when a queue there would slow the ambulance down. Requests "
        "are checked by safety rules and may be refused. Signals are "
        "numbered along the route from 1 (first) upward."
    ),
)


def _ensure_driving(context):
    if context.phase == "warming_up":
        raise ToolError(
            "The ambulance has not set off yet; traffic is still building "
            "up. Try again in a few seconds."
        )
    if context.phase == "arrived":
        raise ToolError("The ambulance has already reached the hospital.")


async def _live(function):
    """Run function(context) inside the simulation loop."""

    try:
        return await asyncio.to_thread(
            simulation_service.run_in_simulation, function
        )
    except SimulationNotRunning as error:
        raise ToolError(f"{error} Call start_simulation first.") from error
    except TimeoutError as error:
        raise ToolError("The simulation did not respond in time.") from error


def _junction_for(context, signal_number):
    for signal in context.engine.route_signals:
        if signal["number"] == signal_number:
            return signal["signal_id"], signal["route_index"]
    raise ToolError(
        f"There is no signal {signal_number} on the route "
        f"(signals are numbered 1 to {len(context.engine.route_signals)})."
    )


@mcp.tool()
async def start_simulation() -> dict:
    """
    Start the simulation if it is not running. Traffic builds up for
    about 10 simulated minutes (a few real seconds), then the ambulance
    sets off.
    """
    return simulation_service.start()


@mcp.tool()
async def get_simulation_status() -> dict:
    """
    Whether the simulation is running, the simulated time, how many
    vehicles are on the roads, and whether the ambulance is waiting to
    leave, driving, or has arrived.
    """

    state = simulation_service.get_state()
    ambulance = state.get("ambulance") or {}

    if state["status"] == "warming_up":
        ambulance_status = "waiting to depart (traffic building up)"
    elif ambulance.get("status") == "COMPLETED":
        ambulance_status = "arrived at the hospital"
    elif ambulance:
        ambulance_status = "driving to the hospital"
    else:
        ambulance_status = "not started"

    return {
        "simulation": state["status"],
        "simulated_time_seconds": state.get("simulation_time"),
        "vehicles_on_roads": state.get(
            "vehicle_count", len(state.get("vehicles", []))
        ),
        "ambulance": ambulance_status,
        "trip": state.get("trip"),
    }


@mcp.tool()
async def get_ambulance_state() -> dict:
    """
    Where the ambulance is and how its trip is going: speed, distance
    left, estimated arrival (AI model and simple formula), time to the
    next signal, time on the road, how many times it had to stop, and
    signals already passed.
    """

    state = simulation_service.get_state()
    ambulance = state.get("ambulance")

    if not ambulance:
        raise ToolError(
            "The ambulance is not on the road (simulation "
            f"{state['status']})."
        )

    route_signals = state.get("route_signals", [])
    passed = sum(
        1 for signal in route_signals
        if ambulance.get("status") == "COMPLETED"
        or signal["route_index"] < ambulance.get("route_index", 0)
    )

    return {
        "status": (
            "arrived" if ambulance.get("status") == "COMPLETED" else "driving"
        ),
        "latitude": ambulance.get("latitude"),
        "longitude": ambulance.get("longitude"),
        "speed_kmh": round(ambulance.get("speed", 0) * 3.6, 1),
        "distance_left_meters": ambulance.get("distance_left_meters"),
        "eta_seconds": ambulance.get("eta_seconds"),
        "eta_source": ambulance.get("eta_source"),
        "formula_eta_seconds": ambulance.get("formula_eta_seconds"),
        "next_signal_eta_seconds": ambulance.get("next_signal_eta_seconds"),
        "trip_time_seconds": ambulance.get("trip_time_seconds"),
        "stops_so_far": ambulance.get("stops"),
        "signals_passed": passed,
        "signals_total": len(route_signals),
    }


@mcp.tool()
async def get_route_traffic(max_roads: int = 20) -> dict:
    """
    Traffic on the roads ahead of the ambulance, nearest first, like the
    colours on a navigation map. Each road has a level: "free", "slow"
    or "jammed", the number of vehicles and stopped vehicles, and how
    fast traffic moves as a percent of the speed limit.
    """

    def read(context):
        _ensure_driving(context)
        return feed.route_traffic(
            context.route_cache, context.traffic, max_roads
        )

    roads = await _live(read)

    return {
        "summary": {
            level: sum(1 for road in roads if road["level"] == level)
            for level in ("free", "slow", "jammed")
        },
        "roads": roads,
    }


@mcp.tool()
async def get_upcoming_signals() -> dict:
    """
    The next traffic signals ahead of the ambulance (up to 6), nearest
    first: signal number, distance, estimated seconds to reach it, the
    light for the ambulance's lane, cars queued there, its corridor role
    (ACTIVE = next; it switches green automatically just in time, about
    10-45 s before the ambulance arrives, depending on the queue) and any
    requested priority.
    """

    def read(context):
        _ensure_driving(context)
        return feed.upcoming_signal_details(
            context.engine, context.route_cache
        )

    return {"signals": await _live(read)}


@mcp.tool()
async def request_signal_priority(signal_number: int, reason: str) -> dict:
    """
    Ask for an early green at a signal further ahead (not the next one,
    which already gets green automatically), e.g. so a queue there can
    drive away before the ambulance arrives. Give a short reason.

    Safety rules may refuse: only signals within 1000 m, at most one
    extra signal at a time, and a granted green is released after 90 s
    or once the ambulance has passed.
    """

    def act(context):
        _ensure_driving(context)
        signal_id, route_index = _junction_for(context, signal_number)
        return context.engine.request_priority(signal_id, route_index, reason)

    return {"signal_number": signal_number, **await _live(act)}


@mcp.tool()
async def release_signal_priority(signal_number: int) -> dict:
    """Cancel an early green requested earlier with request_signal_priority."""

    def act(context):
        signal_id, route_index = _junction_for(context, signal_number)
        return context.engine.release_priority(signal_id, route_index)

    return {"signal_number": signal_number, **await _live(act)}


@mcp.tool()
async def get_deadlock_watch() -> dict:
    """
    The AI deadlock watch: chance that the ambulance gets stuck on the
    route ahead, the jam it found, and what it decided (re-route the
    ambulance or send traffic police from the nearest station, with the
    time each option would save), plus the police unit's progress.
    """

    def read(context):
        _ensure_driving(context)
        if context.response is None:
            return {"status": "not started"}
        return context.response.summary()

    return await _live(read)


@mcp.tool()
async def get_police_alerts() -> dict:
    """
    Police for the stretches of the route without traffic signals: each
    stretch (jammed / clear / passed), and every alert sent to a police
    station because a stretch ahead jammed (station, road, ambulance and
    police arrival times, status ALERTED / EN_ROUTE / ON_SCENE / PASSED /
    CANCELLED, and the phone call's status).
    """

    def read(context):
        _ensure_driving(context)
        if context.police_watch is None:
            return {"status": "not started"}
        return simulation_service._police_summary(context.police_watch)

    summary = await _live(read)
    for stretch in summary.get("stretches", []):
        stretch.pop("geometry", None)  # map lines: not useful to an agent
    return summary


@mcp.tool()
async def get_corridor_events(limit: int = 15) -> dict:
    """
    The most recent corridor decisions, newest last: which signal became
    ACTIVE (green for the ambulance), which were restored after the
    ambulance passed, and every priority request (granted or refused,
    with the reason).
    """

    def read(context):
        numbers = {
            (signal["signal_id"], signal["route_index"]): signal["number"]
            for signal in context.engine.route_signals
        }
        events = list(context.engine.events)[-limit:]
        return [
            {
                "simulated_time": event["time"],
                "type": event["type"],
                "signal_number": numbers.get(
                    (event["signal_id"], event["route_index"])
                ),
                "message": event["message"],
            }
            for event in events
        ]

    return {"events": await _live(read)}


@mcp.tool()
async def post_agent_message(message: str, kind: str = "note") -> dict:
    """
    Show a short message on the live dashboard's AI agent panel, e.g. a
    decision and its reason or a status update. Keep it to one or two
    plain sentences for a non-expert audience. kind: "decision"
    (you requested or chose not to request priority), "warning"
    (a problem ahead) or "note" (status update).
    """

    if kind not in ("decision", "warning", "note"):
        kind = "note"

    return simulation_service.add_agent_message(message.strip()[:400], kind)


@mcp.tool()
async def get_safety_rules() -> dict:
    """The rules every early-green request is checked against."""

    return {
        "max_request_distance_meters": safety.MAX_REQUEST_DISTANCE_METERS,
        "max_junctions_held_green_at_once": safety.MAX_HELD_JUNCTIONS,
        "max_hold_seconds": safety.MAX_HOLD_SECONDS,
        "note": (
            "The next junction switches green automatically just before "
            "the ambulance arrives (10-45 s, via yellow and all red). Requests "
            "can only add early green further ahead, never switch a signal "
            "to red, and are released automatically."
        ),
    }
