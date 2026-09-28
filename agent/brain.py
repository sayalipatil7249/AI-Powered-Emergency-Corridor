"""
Claude's part of the agent: given a trigger and a snapshot, use the MCP
tools (request early green, post a dashboard message, look closer) and
stop when done.
"""

import json
import logging
import os

import anthropic

from agent.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MODEL = os.environ.get("AGENT_MODEL", "claude-opus-5")

# Frequent, short supervisory decisions: low effort keeps them fast and
# cheap. Raise to "medium" if decisions look too shallow.
EFFORT = os.environ.get("AGENT_EFFORT", "low")

MAX_TOOL_ROUNDS = 6

# Claude Opus 5 prices (USD per million tokens), for the cost estimate.
PRICE_INPUT = 5.00
PRICE_OUTPUT = 25.00
CACHE_READ_FACTOR = 0.1
CACHE_WRITE_FACTOR = 1.25


class MissingCredentials(Exception):
    pass


class Brain:
    def __init__(self, tools):
        # Reads ANTHROPIC_API_KEY (or an `ant auth login` profile).
        self.client = anthropic.AsyncAnthropic()
        self.tools = tools
        # Messages already posted to the dashboard, so Claude can avoid
        # repeating itself.
        self.posted = []
        self.usage = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    async def check_access(self):
        """Fail early with a clear error if the key or model is unusable."""
        try:
            await self.client.models.retrieve(MODEL)
        except TypeError as error:
            # The SDK found no API key, auth token or profile at all.
            raise MissingCredentials(str(error)) from error

    async def think(self, trigger, snapshot):
        """
        Let Claude handle one trigger. Returns the names of the tools it
        used (e.g. ["request_signal_priority", "post_agent_message"]).
        """

        snapshot = {**snapshot, "your_recent_messages": self.posted[-4:]}

        messages = [{
            "role": "user",
            "content": (
                f"Trigger: {trigger}\n\n"
                f"Snapshot:\n{json.dumps(snapshot, indent=1)}"
            ),
        }]
        used = []

        for _ in range(MAX_TOOL_ROUNDS):
            response = await self._ask(messages)
            if response is None:
                return used

            if response.stop_reason == "refusal":
                logger.warning(
                    "Claude declined this request (%s).",
                    getattr(response.stop_details, "category", None),
                )
                return used

            if response.stop_reason != "tool_use":
                return used

            messages.append({"role": "assistant", "content": response.content})

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                text, is_error = await self.tools.call_for_claude(
                    block.name, block.input
                )
                used.append(block.name)
                if block.name == "post_agent_message" and not is_error:
                    self.posted.append(block.input.get("message", ""))
                logger.info(
                    "  %s(%s) -> %s%s",
                    block.name,
                    json.dumps(block.input),
                    "ERROR: " if is_error else "",
                    text[:160],
                )

                result = {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": text,
                }
                if is_error:
                    result["is_error"] = True
                results.append(result)

            # All results for one turn go back in a single message.
            messages.append({"role": "user", "content": results})

        logger.warning("Stopped after %d tool rounds.", MAX_TOOL_ROUNDS)
        return used

    async def _ask(self, messages):
        try:
            response = await self.client.beta.messages.create(
                model=MODEL,
                max_tokens=8000,
                # Fixed system prompt + tools: cached across calls.
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=self.tools.claude_tools,
                messages=messages,
                output_config={"effort": EFFORT},
                # Also cache the growing conversation within one decision.
                cache_control={"type": "ephemeral"},
                # If a safety classifier declines, retry server-side on
                # Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise
        except anthropic.RateLimitError:
            logger.warning("Rate limited by the Claude API; skipping this decision.")
            return None
        except anthropic.APIStatusError as error:
            logger.warning("Claude API error %s; skipping this decision.",
                           error.status_code)
            return None
        except anthropic.APIConnectionError:
            logger.warning("Could not reach the Claude API; skipping this decision.")
            return None

        self._count(response.usage)
        return response

    def _count(self, usage):
        self.usage["calls"] += 1
        self.usage["input_tokens"] += usage.input_tokens or 0
        self.usage["output_tokens"] += usage.output_tokens or 0
        self.usage["cache_read_tokens"] += usage.cache_read_input_tokens or 0
        self.usage["cache_write_tokens"] += usage.cache_creation_input_tokens or 0

    def estimated_cost(self):
        """
        Approximate USD cost. Cache reads cost about 0.1x and 5-minute
        cache writes about 1.25x the normal input price.
        """

        usage = self.usage
        input_equivalent = (
            usage["input_tokens"]
            + usage["cache_read_tokens"] * CACHE_READ_FACTOR
            + usage["cache_write_tokens"] * CACHE_WRITE_FACTOR
        )
        return (
            input_equivalent * PRICE_INPUT
            + usage["output_tokens"] * PRICE_OUTPUT
        ) / 1_000_000
