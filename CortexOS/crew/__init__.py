"""Crew — the engine's own multi-agent chat runtime.

A rail of named agents that talk to the operator and to each other inside a
space, streamed live to the bundled web UI at /crew. Inference is local-first
(any OpenAI-compatible endpoint; Ollama by default). Desktop control is bridged
to allowlisted local MCP servers via a stdio client — read/act UI tools only,
never shell, registry, or file-system tools.

Engine-side only: no ``packs`` imports, SQLite under ``data/engine/``.
"""
