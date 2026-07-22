"""OpenDMS watcher agents (S1) — the "AI employee".

A deterministic detector watches a lakehouse table (e.g. an S0 stream); when it
fires, the agent drafts a report, a deterministic compliance verdict is recorded,
and the run parks in `pending_approval`. Nothing publishes without a human
approval — the F5 value-threshold safety rail generalized to agent actions.
Durable-resume (DBOS) is a later slice; this is the governed workflow first.

See docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md Feature S1 and
docs/research/findings/RETRIEVAL_ORCHESTRATION_2026.md (deterministic detector →
agent → human-in-the-loop pattern).
"""
from packs.dms.agents import detectors, employee, registry

__all__ = ["detectors", "employee", "registry"]
