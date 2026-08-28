"""Verify the Cortex implementation satisfies every contract Protocol."""
from __future__ import annotations

from inspect import signature
from pathlib import Path

from cortex_contract.version import CONTRACT_VERSION

from CortexOS.api import engine_routes
from CortexOS.execution import run_plan, tool_runner
from packs.dms.audit import ledger


def test_contract_major_is_one() -> None:
    """DMS pins contract major 1. Bumping to 2 is a coordinated break, not a commit."""
    assert CONTRACT_VERSION.split(".")[0] == "1"


def test_every_published_spec_is_committed() -> None:
    """Each contract version DMS could be pinned to must still have its spec on disk."""
    contract_dir = Path(__file__).resolve().parents[2] / "contract"
    published = sorted(p.name for p in contract_dir.glob("openapi-*.json"))
    assert f"openapi-{CONTRACT_VERSION}.json" in published, published
    # 1.0.0 shipped to a consumer; deleting it would strand anyone still pinned.
    assert "openapi-1.0.0.json" in published, published


def test_manifest_1_0_0_producer_still_validates() -> None:
    """A minor bump may add fields; it may never make an old payload invalid."""
    from cortex_contract.execution import Manifest

    minted_by_1_0_0 = Manifest(
        session_id="s1",
        org_id="acme",
        allowed_paths=["/data/pool/acme/*.parquet"],
        row_predicate_sql="tenant_id = 'acme'",
        expires_at="2030-01-01T00:00:00Z",
        signature="deadbeef",
    )
    assert minted_by_1_0_0.row_predicates == {}
    assert minted_by_1_0_0.issuer_key_id is None
    assert minted_by_1_0_0.pool_id is None


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
