"""Cortex Crew - the agentic chat surface over the engine.

Guaca-shaped local UI (spaces rail, one conversation, visible agent-to-agent
traffic) where a Manager agent answers directly or spawns teammates, teammates
talk to each other over an in-process A2A inbox, governed data questions go to
the running Cortex engine (`POST /dms/query`) and arrive with their badge and
audit id intact, and computer-control MCP servers (Windows-MCP and friends)
are callable only behind the connectors master switch + per-server arming +
per-call operator confirm.

Standalone by default (``python -m CortexOS.crew.server``); mountable into any
FastAPI host (AirGPT) via :func:`CortexOS.crew.server.build_router`.

This package must never import ``packs.*`` (C2 boundary) and never opens
DuckDB (execution owns that); its own state is SQLite under ``data/crew/``.
"""

CREW_VERSION = "0.1.0"
