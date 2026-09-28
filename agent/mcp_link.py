"""
Connection from the agent to the corridor MCP server
(backend/mcp_server.py, http://127.0.0.1:8000/mcp).
"""

import json
from contextlib import AsyncExitStack

from mcp import Client

# 127.0.0.1, not "localhost": the backend listens on IPv4 only.
DEFAULT_URL = "http://127.0.0.1:8000/mcp"

# MCP tools Claude may use while reasoning. The agent's own loop uses
# the status tools itself, and Claude must not start the simulation.
CLAUDE_TOOLS = [
    "get_ambulance_state",
    "get_corridor_events",
    "get_route_traffic",
    "get_upcoming_signals",
    "post_agent_message",
    "release_signal_priority",
    "request_signal_priority",
]


class ToolFailed(Exception):
    """An MCP tool returned an error (e.g. the ambulance has arrived)."""


class CorridorTools:
    """Long-lived MCP session with helpers for the agent."""

    def __init__(self, url=DEFAULT_URL):
        self.url = url
        self._stack = AsyncExitStack()
        self._client = None
        self.claude_tools = []

    async def __aenter__(self):
        self._client = await self._stack.enter_async_context(Client(self.url))

        listed = await self._client.list_tools()
        by_name = {tool.name: tool for tool in listed.tools}

        # Fixed order so the request prefix stays identical (prompt cache).
        self.claude_tools = [
            {
                "name": name,
                "description": (by_name[name].description or "").strip(),
                "input_schema": by_name[name].input_schema,
            }
            for name in CLAUDE_TOOLS
            if name in by_name
        ]
        return self

    async def __aexit__(self, *exc_info):
        await self._stack.aclose()

    async def call(self, name, arguments=None):
        """Call a tool; returns its parsed result or raises ToolFailed."""

        result = await self._client.call_tool(name, arguments or {})
        text = " ".join(
            getattr(item, "text", "") for item in result.content
        ).strip()

        if result.is_error:
            raise ToolFailed(text)

        try:
            return json.loads(text)
        except ValueError:
            return text

    async def call_for_claude(self, name, arguments):
        """Call a tool for Claude: (result text, is_error)."""

        if name not in CLAUDE_TOOLS:
            return f"Tool {name} is not available.", True

        try:
            result = await self.call(name, arguments)
        except ToolFailed as error:
            return str(error), True

        return json.dumps(result) if not isinstance(result, str) else result, False
