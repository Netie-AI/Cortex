"""L2 generation port — engine owns the seam, the pack fills it (C2).

``CortexOS`` must not import ``packs.*``. Schema retrieval, SQL generation and
L2 promotion live in the vertical pack, so the arrow is inverted: the engine
declares this port, the active pack registers an implementation, and
``answer_engine`` only ever holds the port.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any, Protocol


class L2GenerationPort(Protocol):
    def is_configured(self) -> bool: ...

    def retrieve_schema(self, question: str) -> dict[str, Any]: ...

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]: ...

    def record_validated(self, question: str, sql: str) -> Any: ...


class L2NotRegistered(RuntimeError):
    """No vertical pack registered an L2 generation implementation."""


_port: L2GenerationPort | None = None


def register_l2_generation(port: L2GenerationPort) -> None:
    """Install the active L2 port. Called by the owning pack, never by the engine."""
    global _port
    _port = port


def clear_l2_generation() -> None:
    """Drop the registered port (pack swap / test teardown)."""
    global _port
    _port = None


def resolve_l2_generation() -> L2GenerationPort:
    """Return the registered L2 port, importing the active pack once so it can register."""
    if _port is None:
        _load_active_pack()
    if _port is None:
        raise L2NotRegistered(
            "No L2 generation port is registered. The active vertical pack must "
            "call CortexOS.dms.l2_generation.register_l2_generation()."
        )
    return _port


def _load_active_pack() -> None:
    """Import the configured pack so its module-level registration runs.

    Resolved dynamically on purpose: a static ``import packs.…`` here would put
    the engine straight back on the wrong side of the C2 boundary.
    """
    from CortexOS.config import get_config

    try:
        importlib.import_module(f"packs.{get_config().pack}")
    except ImportError:
        return


@dataclass(slots=True)
class L2Attempt:
    """Outcome of the L2 path. ``sql`` set means the gate passed."""

    sql: str | None = None
    reason: str = ""
    layer: str = "generated"
    badge: str = "L2_VALIDATED"
    assumptions: str = ""


def attempt_l2(question: str, *, verified: Any = None) -> L2Attempt | None:
    """Run L2 through the registered port. ``None`` when the L2 flag is off.

    Lives here so ``answer_engine`` never names pack generation modules.
    """
    if os.environ.get("DMS_L2_ENABLED", "").lower() not in ("1", "true", "yes"):
        return None

    from CortexOS.dms.sql_validate_gate import SqlGateAbstain, gate_with_retry
    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
        load_semantic_layer,
        read_only_queries_enabled,
    )

    try:
        l2 = resolve_l2_generation()
    except L2NotRegistered:
        return L2Attempt(reason="no verified answer path (L2 import failed)")

    if not l2.is_configured():
        return L2Attempt(reason="no verified answer path (L2 not wired)")

    semantic_early = load_semantic_layer()
    reduced = l2.retrieve_schema(question)

    def _gen(prior: list[str]) -> str | None:
        cands = l2.generate_candidates(
            question,
            reduced,
            prior_violations=prior,
        )
        return cands[0] if cands else None

    con_explain = None
    try:
        if verified is None:
            con_explain = get_connection(
                DEFAULT_DB, read_only=read_only_queries_enabled()
            )
        gate = gate_with_retry(
            _gen,
            question,
            semantic_early,
            con=con_explain,
            max_retries=2,
        )
    except SqlGateAbstain as exc:
        return L2Attempt(reason=f"L2 generation failed validation gate: {exc}")
    finally:
        if con_explain is not None:
            con_explain.close()

    if not gate.passed or not gate.safe_sql:
        return L2Attempt(reason="L2 generation failed validation gate")

    sql = gate.safe_sql
    try:
        l2.record_validated(question, sql)
    except Exception:  # noqa: BLE001 — promotion signal must not block answers
        pass
    tables = list((reduced.get("tables") or {}).keys())
    return L2Attempt(
        sql=sql,
        assumptions=f"L2 FreeRoute SQL over reduced schema tables={tables}",
    )


__all__ = [
    "L2Attempt",
    "L2GenerationPort",
    "L2NotRegistered",
    "attempt_l2",
    "clear_l2_generation",
    "register_l2_generation",
    "resolve_l2_generation",
]
