"""
The AI supervisor agent: a LangGraph loop that watches the ambulance
through the MCP server and uses Claude to decide on early greens and to
explain what is happening on the dashboard.

Run it (with the backend running) from the project root:
    python -m agent.run
"""
