"""L2 generation port — engine owns the seam, the pack fills it (C2).

``CortexOS`` must not import ``packs.*``. Schema retrieval, SQL generation and
L2 promotion live in the vertical pack, so the arrow is inverted: the engine
declares this port, the active pack registers an implementation, and
``answer_engine`` only ever holds the port.
"""

from __future__ import annotations

import importlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
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


#: Stable prefix so answer() can emit refused, not coverage abstain (C7-02).
L2_MANIFEST_REASON_PREFIX = "L2_MANIFEST:"


@dataclass(slots=True)
class L2Attempt:
    """Outcome of the L2 path. ``sql`` set means the gate passed."""

    sql: str | None = None
    reason: str = ""
    layer: str = "generated"
    badge: str = "L2_VALIDATED"
    assumptions: str = ""
    violations: list[str] | None = None
    refused: bool = False
    retrieved_tables: tuple[str, ...] = ()


def _env_on(name: str) -> bool:
    return os.environ.get(name, "").lower() in ("1", "true", "yes")


def _manifest_l2_attempt(violations: list[str]) -> L2Attempt:
    from CortexOS.dms.sql_validate_gate import MANIFEST_VIOLATION_PREFIX

    code = "manifest_error"
    for item in violations:
        if item.startswith(MANIFEST_VIOLATION_PREFIX):
            code = item[len(MANIFEST_VIOLATION_PREFIX) :]
            break
    return L2Attempt(
        reason=f"{L2_MANIFEST_REASON_PREFIX}{code}",
        layer="refused",
        badge="refused",
        violations=list(violations),
        refused=True,
    )


def attempt_l2(
    question: str,
    *,
    verified: Any = None,
    force: bool = False,
    promote: bool = True,
) -> L2Attempt | None:
    """Run L2 through the registered port. ``None`` when the L2 flag is off.

    ``force=True`` runs even when ``DMS_L2_ENABLED`` is unset (shadow).
    ``promote=False`` skips steward recording so shadow cannot mutate L0.
    Lives here so ``answer_engine`` never names pack generation modules.
    """
    if not force and not _env_on("DMS_L2_ENABLED"):
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

    leftover: list[str] = []

    def _gen(prior: list[str]) -> str | None:
        if leftover:
            return leftover.pop(0)
        cands = list(
            l2.generate_candidates(
                question,
                reduced,
                prior_violations=prior,
            )
            or []
        )
        if not cands:
            return None
        leftover.extend(cands[1:])
        return cands[0]

    con_explain = None
    try:
        # EXPLAIN must run on post-enforce SQL even when a session is bound.
        con_explain = get_connection(
            DEFAULT_DB, read_only=read_only_queries_enabled()
        )
        gate = gate_with_retry(
            _gen,
            question,
            semantic_early,
            con=con_explain,
            verified=verified,
            max_retries=2,
        )
    except SqlGateAbstain as exc:
        if exc.manifest_refused:
            return _manifest_l2_attempt(list(exc.violations))
        return L2Attempt(
            reason=f"L2 generation failed validation gate: {exc}",
            violations=list(exc.violations),
        )
    finally:
        if con_explain is not None:
            con_explain.close()

    if not gate.passed or not gate.safe_sql:
        if gate.manifest_refused:
            return _manifest_l2_attempt(list(gate.violations))
        return L2Attempt(
            reason="L2 generation failed validation gate",
            violations=list(gate.violations),
        )

    # Serve pre-enforce SQL so execute_sql / enforce_manifest runs once.
    # Post-enforce SQL re-submitted as a candidate collides local bind + grant.
    sql = gate.source_sql or gate.safe_sql
    if promote:
        try:
            l2.record_validated(question, sql)
        except Exception:  # noqa: BLE001 — promotion signal must not block answers
            pass
    tables = tuple((reduced.get("tables") or {}).keys())
    return L2Attempt(
        sql=sql,
        assumptions=f"L2 FreeRoute SQL over reduced schema tables={list(tables)}",
        retrieved_tables=tables,
    )


_SKIP_SHADOW_LAYERS = frozenset({"generated", "blocked", "rag", "catalog"})


def _shadow_path() -> Path:
    override = (os.environ.get("DMS_L2_SHADOW_PATH") or "").strip()
    if override:
        return Path(override)
    from CortexOS.paths import data_path

    return data_path("engine", "l2_shadow.jsonl")


def _compact_values(rows: list[Any] | None) -> list[Any] | None:
    if not rows or len(rows) > 3:
        return None
    return json.loads(json.dumps(rows, default=str))


def _l2_execute(sql: str, verified: Any) -> tuple[list[Any] | None, str | None]:
    if verified is not None:
        from CortexOS.execution.manifest import ManifestError
        from CortexOS.execution.submit import execute_sql

        try:
            rows, _, _ = execute_sql(verified, sql)
            return list(rows), None
        except ManifestError as exc:
            return None, type(exc).__name__
        except Exception as exc:  # noqa: BLE001
            return None, type(exc).__name__
    from CortexOS.dms.sql_guardrail import guard_and_execute
    from CortexOS.dms.warehouse_db import (
        DEFAULT_DB,
        get_connection,
        load_semantic_layer,
        read_only_queries_enabled,
    )

    con = get_connection(DEFAULT_DB, read_only=read_only_queries_enabled())
    try:
        gate, rows, _ = guard_and_execute(sql, load_semantic_layer(), con)
        if not gate.passed:
            return None, "guardrail"
        return list(rows), None
    finally:
        con.close()


def _write_l2_shadow(
    question: str,
    served: dict[str, Any],
    *,
    verified: Any,
) -> None:
    started = time.perf_counter()
    refusal: str | None = None
    l2_sql: str | None = None
    l2_rows: list[Any] | None = None
    try:
        out = attempt_l2(question, verified=verified, force=True, promote=False)
        if out is None:
            refusal = "not_enabled"
        elif not out.sql:
            refusal = (out.reason or "l2_refused")[:240]
        else:
            l2_sql = out.sql
            l2_rows, exec_ref = _l2_execute(out.sql, verified)
            if exec_ref:
                refusal = exec_ref
                l2_sql = out.sql
    except Exception as exc:  # noqa: BLE001
        refusal = f"exception:{type(exc).__name__}"
    latency_ms = round((time.perf_counter() - started) * 1000.0, 3)
    served_rows = list(served.get("rows") or [])
    served_n = served.get("row_count")
    if served_n is None:
        served_n = served.get("total_count")
    if served_n is None:
        served_n = len(served_rows)
    l2_n = len(l2_rows) if l2_rows is not None else None
    served_empty = served.get("layer") in ("abstain", "refused") or not served_rows
    l2_empty = l2_rows is None
    if served_empty and l2_empty:
        agree = True
    elif served_empty or l2_empty:
        agree = False
    else:
        agree = int(served_n) == int(l2_n or 0)
    rec = {
        "question": question,
        "served_layer": served.get("layer"),
        "served_badge": served.get("badge"),
        "served_row_count": served_n,
        "served_values": _compact_values(served_rows),
        "l2_sql": l2_sql,
        "l2_refusal_type": refusal,
        "l2_row_count": l2_n,
        "l2_values": _compact_values(l2_rows),
        "agree": agree,
        "latency_ms": latency_ms,
    }
    path = _shadow_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


def maybe_record_l2_shadow(
    question: str,
    served: dict[str, Any],
    *,
    verified: Any = None,
) -> None:
    """Compare L2 to a served envelope. Never raises; never mutates ``served``."""
    if not _env_on("DMS_L2_SHADOW"):
        return
    if served.get("layer") in _SKIP_SHADOW_LAYERS:
        return
    try:
        _write_l2_shadow(question, served, verified=verified)
    except Exception:  # noqa: BLE001
        return


__all__ = [
    "L2Attempt",
    "L2GenerationPort",
    "L2NotRegistered",
    "L2_MANIFEST_REASON_PREFIX",
    "attempt_l2",
    "clear_l2_generation",
    "maybe_record_l2_shadow",
    "register_l2_generation",
    "resolve_l2_generation",
]
