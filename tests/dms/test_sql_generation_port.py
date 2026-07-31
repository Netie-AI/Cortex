"""C7-full runs through an engine-owned port, not a direct pack import.

The point of these tests is the *direction of the arrow*. `answer_engine` used to
`import packs.dms.generative.*` and break the C2 contract; the fix inverts it so
the pack registers into the engine. A regression would be silent — the code would
keep working and only `lint-imports` would go red — so the import-direction check
below is asserted here too, where it fails loudly next to the behaviour it guards.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from CortexOS.dms.sql_generation_port import (
    SqlGenerationNotRegistered,
    SqlGenerationProvider,
    clear_sql_generation,
    register_sql_generation,
    registered_sql_generation,
    resolve_sql_generation,
)

ROOT = Path(__file__).resolve().parents[2]
ANSWER_ENGINE = ROOT / "CortexOS" / "dms" / "answer_engine.py"


class _StubProvider:
    """Minimal provider — records calls so the port's contract is observable."""

    def __init__(self, *, configured: bool = True, candidates: list[str] | None = None) -> None:
        self._configured = configured
        self._candidates = candidates if candidates is not None else ["SELECT 1"]
        self.recorded: list[tuple[str, str]] = []
        self.seen_violations: list[list[str]] = []

    def is_configured(self) -> bool:
        return self._configured

    def retrieve_schema(self, question: str) -> dict[str, object]:
        return {"tables": {"inventory": {"columns": ["sku"]}}, "question": question}

    def generate_candidates(self, question, schema, *, prior_violations):
        self.seen_violations.append(list(prior_violations))
        return list(self._candidates)

    def record_validated(self, question: str, sql: str) -> None:
        self.recorded.append((question, sql))


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_sql_generation()
    yield
    clear_sql_generation()


def test_stub_satisfies_the_declared_protocol():
    assert isinstance(_StubProvider(), SqlGenerationProvider)


def test_register_then_resolve_returns_the_same_provider():
    provider = _StubProvider()
    register_sql_generation(provider)
    assert registered_sql_generation() is provider
    assert resolve_sql_generation() is provider


def test_registered_does_not_trigger_a_pack_import():
    """`registered_*` is the peek that must stay side-effect free."""
    assert registered_sql_generation() is None


def test_resolve_loads_the_active_pack_which_registers_dms(monkeypatch):
    """`import packs.dms` must be enough to light L2 up — same rule as the ledger.

    The suite-wide fixture pins ``PACK=ruma``; this is the one test that cares
    which pack is active, so it names ``dms`` explicitly.
    """
    monkeypatch.setenv("PACK", "dms")
    import netie.config

    netie.config._cached_config = None
    provider = resolve_sql_generation()
    assert provider.is_configured() in (True, False)  # wired or not, it answered
    assert hasattr(provider, "generate_candidates")


def test_an_inactive_pack_abstains_rather_than_crashing():
    """PACK=ruma ships no L2 seam — that must be a typed error, not an ImportError."""
    with pytest.raises(SqlGenerationNotRegistered):
        resolve_sql_generation()


def test_missing_provider_raises_the_typed_error(monkeypatch):
    """A pack with no L2 seam must surface as abstain-able, not as an AttributeError."""
    import CortexOS.dms.sql_generation_port as port

    monkeypatch.setattr(port, "_load_active_pack", lambda: None)
    with pytest.raises(SqlGenerationNotRegistered):
        resolve_sql_generation()


def test_provider_carries_prior_violations_for_retry():
    provider = _StubProvider()
    register_sql_generation(provider)
    resolved = resolve_sql_generation()
    resolved.generate_candidates("q", {}, prior_violations=["missing predicate"])
    assert provider.seen_violations == [["missing predicate"]]


def test_answer_engine_holds_no_generative_pack_import():
    """The C2 regression guard: this is what `lint-imports` was red on."""
    tree = ast.parse(ANSWER_ENGINE.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "packs.dms.generative"
        ):
            offenders.append(f"line {node.lineno}: from {node.module} import ...")
        if isinstance(node, ast.Import):
            offenders += [
                f"line {node.lineno}: import {a.name}"
                for a in node.names
                if a.name.startswith("packs.dms.generative")
            ]
    assert not offenders, (
        "answer_engine must reach L2 through CortexOS.dms.sql_generation_port:\n"
        + "\n".join(offenders)
    )


def test_the_gate_stays_engine_side():
    """A pack proposes SQL; only the engine decides whether it may run."""
    method_names = {
        name for name in dir(SqlGenerationProvider) if not name.startswith("_")
    }
    assert method_names == {
        "is_configured",
        "retrieve_schema",
        "generate_candidates",
        "record_validated",
    }
