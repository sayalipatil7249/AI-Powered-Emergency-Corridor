"""
End-to-end check of the MCP server, acting like an AI agent would:
connect, list tools, start the simulation, wait for the ambulance, call
every read tool, request early green at a signal ahead and read the
decision log.

Needs the backend running (uvicorn backend.main:app).

Usage (from the project root):
    python -m scripts.tests.test_mcp
"""

import asyncio
import json

from mcp import Client

URL = "http://127.0.0.1:8000/mcp"


def _content(result):
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if structured is not None:
        return structured
    text = " ".join(getattr(item, "text", "") for item in result.content)
    try:
        return json.loads(text)
    except ValueError:
        return text


def _is_error(result):
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


async def call(client, name, arguments=None, show=True):
    result = await client.call_tool(name, arguments or {})
    content = _content(result)
    if show:
        label = "ERROR" if _is_error(result) else "ok"
        text = json.dumps(content, indent=2) if isinstance(content, dict) else content
        if len(text) > 1500:
            text = text[:1500] + "\n  ..."
        print(f"\n--- {name} {arguments or ''} [{label}]\n{text}")
    return result, content


async def main():
    async with Client(URL) as client:
        tools = await client.list_tools()
        print("Tools:", ", ".join(tool.name for tool in tools.tools))

        await call(client, "start_simulation")

        # Wait until the ambulance is driving.
        for _ in range(60):
            _, status = await call(client, "get_simulation_status", show=False)
            if status.get("ambulance") == "driving to the hospital":
                break
            await asyncio.sleep(2)

        await asyncio.sleep(8)

        await call(client, "get_simulation_status")
        await call(client, "get_ambulance_state")
        await call(client, "get_route_traffic", {"max_roads": 6})
        _, signals = await call(client, "get_upcoming_signals")

        upcoming = signals["signals"]
        if len(upcoming) >= 3:
            # Too many at once / next one / a good candidate.
            await call(client, "request_signal_priority", {
                "signal_number": upcoming[0]["signal_number"],
                "reason": "test: next junction",
            })
            await call(client, "request_signal_priority", {
                "signal_number": upcoming[1]["signal_number"],
                "reason": "test: queue ahead",
            })
            await call(client, "request_signal_priority", {
                "signal_number": upcoming[2]["signal_number"],
                "reason": "test: second extra request",
            })

        await asyncio.sleep(3)
        await call(client, "get_upcoming_signals")
        await call(client, "get_corridor_events", {"limit": 8})
        await call(client, "request_signal_priority", {
            "signal_number": 99, "reason": "test: invalid number",
        })


if __name__ == "__main__":
    asyncio.run(main())
