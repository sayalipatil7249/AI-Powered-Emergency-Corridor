"""
Run the AI supervisor agent for one ambulance trip.

Needs the backend running (uvicorn backend.main:app) and a Claude API
key: ANTHROPIC_API_KEY in the environment or in .env.

Usage (from the project root):
    python -m agent.run            # start the simulation and supervise it
    python -m agent.run --no-start # supervise an already running one
"""

import argparse
import asyncio
import logging
import sys

import anthropic
from dotenv import load_dotenv

from agent.brain import EFFORT, MODEL, Brain, MissingCredentials
from agent.graph import build_graph
from agent.mcp_link import DEFAULT_URL, CorridorTools

logger = logging.getLogger("agent")


async def main(url, interval, start):
    async with CorridorTools(url) as tools:
        brain = Brain(tools)
        await brain.check_access()

        if start:
            result = await tools.call("start_simulation")
            logger.info("Simulation: %s", result.get("status"))

        logger.info(
            "Supervising the trip (model %s, effort %s). Ctrl+C to stop.",
            MODEL, EFFORT,
        )

        graph = build_graph(tools, brain, interval)

        try:
            final = await graph.ainvoke({}, config={"recursion_limit": 100_000})
        finally:
            usage = brain.usage
            logger.info(
                "Claude calls: %d | tokens in %d (cache read %d, cache write "
                "%d), out %d | about $%.3f",
                usage["calls"], usage["input_tokens"],
                usage["cache_read_tokens"], usage["cache_write_tokens"],
                usage["output_tokens"], brain.estimated_cost(),
            )

        logger.info(
            "Done: %s after %d decisions.",
            final.get("phase"), final.get("decisions", 0),
        )


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="MCP server URL")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="real seconds between observations")
    parser.add_argument("--no-start", dest="start", action="store_false",
                        help="do not start the simulation")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    # Keep third-party HTTP logs out of the agent's output.
    for noisy in ("httpx", "httpx2", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        asyncio.run(main(args.url, args.interval, args.start))
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            MissingCredentials):
        sys.exit(
            "No valid Claude API key. Add ANTHROPIC_API_KEY=... to .env "
            "(get one at console.anthropic.com)."
        )
    except anthropic.NotFoundError:
        sys.exit(f"The model {MODEL} is not available to this API key.")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
