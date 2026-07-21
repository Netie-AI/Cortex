"""OpenDMS declarative pipelines (L2) — bronze→silver transforms gated by
data-quality expectations (warn/drop/fail), with quarantine + an event log.

Deterministic engine: the LLM may PROPOSE expectations (see propose.py) but only
a human-approved, deterministic ruleset ever mutates data. See
docs/dms/BUILD_PLAN_V2_LAKEHOUSE.md Feature L2.
"""
from packs.dms.pipelines.runner import (
    ExpectationResult,
    PipelineError,
    PipelineRun,
    load_pipeline,
    pipeline_events,
    run_pipeline,
)

__all__ = [
    "ExpectationResult", "PipelineError", "PipelineRun",
    "load_pipeline", "pipeline_events", "run_pipeline",
]
