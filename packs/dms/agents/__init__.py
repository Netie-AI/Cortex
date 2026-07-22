"""OpenDMS watcher agents (S1) — the "AI employee".

A deterministic detector watches a lakehouse table (e.g. an S0 stream); when it
fires, the agent drafts a report, a deterministic compliance verdict is recorded,
and the run parks in `pending_approval`. Nothing publishes without a human
approval — the F5 value-threshold safety rail generalized to agent actions.

Durable resume: ops-DB step checkpoints always; optional ``dbos`` via
``pip install -e ".[agents]"`` (``dbos>=2.28.0,<3``). Temporal is not used.

See docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md Feature S1 and
docs/research/findings/S1_DBOS_RESUME.md.
"""
from packs.dms.agents import detectors, employee, registry

__all__ = ["detectors", "employee", "registry"]
