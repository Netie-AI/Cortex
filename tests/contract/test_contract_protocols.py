"""Verify the Cortex implementation satisfies every contract Protocol."""
from __future__ import annotations

from inspect import signature

from packages.cortex_contract.version import CONTRACT_VERSION
from CortexOS.execution import run_plan
from CortexOS.execution import tool_runner
from packs.dms.audit import ledger
from CortexOS.api import engine_routes


def test_contract_version_pinned() -> None:
    assert CONTRACT_VERSION == "1.0.0"


def test_engine_submit_surface_matches_contract() -> None:
    """execute_run_plan(plan, body, *, caller) satisfies EngineSubmitter shape."""
    sig = signature(run_plan.execute_run_plan)
    assert list(sig.parameters.keys())[:2] == ["plan", "body"]
    assert "caller" in sig.parameters


def test_tool_runtime_surface_matches_contract() -> None:
    """run_tool_call(tool, params, *, actor, ...) satisfies ToolRuntime shape."""
    sig = signature(tool_runner.run_tool_call)
    assert sig.parameters["tool"].kind.name in {"POSITIONAL_OR_KEYWORD"}
    assert "actor" in sig.parameters


def test_ledger_surface_matches_contract() -> None:
    """packs.dms.audit.ledger exposes append/verify/list_entries."""
    assert callable(ledger.append)
    assert callable(ledger.verify)
    assert callable(ledger.list_entries)


def test_engine_route_exists() -> None:
    sig = signature(engine_routes.engine_run)
    assert "body" in sig.parameters
