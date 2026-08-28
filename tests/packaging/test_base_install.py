"""Base-install import surface — must succeed without agentic/rag extras."""

from __future__ import annotations

import importlib

import pytest

# Base modules: answer engine, execution primitives, ledger, F5/F7, semantic
# layer, context assembly, contract. Must not require [agentic] or [rag].
BASE_MODULES = [
    "CortexOS",
    "CortexOS.packaging",
    "CortexOS.dms.answer_engine",
    "CortexOS.dms.sql_guardrail",
    "CortexOS.dms.warehouse_db",
    "CortexOS.context_engineering",
    "CortexOS.context_engineering.assembler",
    "CortexOS.execution.tool_runner",
    "CortexOS.execution.errors",
    "CortexOS.memory.store",
    "cortex_contract",
    "cortex_contract.version",
    "cortex_contract.answer",
    "cortex_contract.execution",
    "cortex_contract.ledger",
    "cortex_contract.proposal",
    "cortex_contract.tools",
    "cortex_contract.errors",
    "packs.dms.audit.ledger",
    "packs.dms.semantic.loader",
    "packs.dms.tasks.gate",
    "packs.dms.security.crypto",
]


@pytest.fixture(autouse=True)
def _core_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat optional extras as absent for this module's assertions."""
    monkeypatch.setenv("CORTEX_PROFILE", "core")


@pytest.mark.parametrize("module", BASE_MODULES)
def test_base_module_imports(module: str) -> None:
    importlib.import_module(module)


def test_feature_not_installed_names_extra() -> None:
    from CortexOS.packaging import FeatureNotInstalled, require_extra

    with pytest.raises(FeatureNotInstalled) as ei:
        require_extra("rag", feature="unit-test")
    assert ei.value.extra == "rag"
    assert "netie[rag]" in str(ei.value)

    with pytest.raises(FeatureNotInstalled) as ei2:
        require_extra("agentic", feature="unit-test")
    assert ei2.value.extra == "agentic"
    assert "netie[agentic]" in str(ei2.value)


def test_agentic_entrypoint_gated() -> None:
    import CortexOS.execution as execution
    from CortexOS.packaging import FeatureNotInstalled

    # Clear any prior cache from a full-profile import in the same session.
    execution.__dict__.pop("execute_run_plan", None)

    with pytest.raises(FeatureNotInstalled):
        _ = execution.execute_run_plan
