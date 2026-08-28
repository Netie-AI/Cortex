"""C4 submit seam — verify → bind → enforce → pool → execute.

This is the production caller of ``enforce_manifest``. Contract HTTP and the
answer engine both enter here; nothing opens DuckDB for governed SQL except
through this module.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from cortex_contract.execution import Manifest, QueryResult, SubmitRequest

from CortexOS.execution.manifest import (
    ManifestError,
    ManifestMalformed,
    ManifestVerifier,
    VerifiedManifest,
    enforce_manifest,
)
from CortexOS.execution.pool import PoolSaturated, get_read_pool
from CortexOS.execution.session_manifests import get_session_registry
from CortexOS.execution.telemetry import new_run_id, record_run
from CortexOS.execution.warehouse import (
    DEFAULT_DB,
    get_connection,
    read_only_queries_enabled,
    warehouse_path,
)


class PoolMismatch(ManifestError):
    code = "pool_mismatch"


class PoolRequired(ManifestError):
    code = "pool_required"


class SqlRequired(ManifestError):
    code = "sql_required"


_VERIFIER: ManifestVerifier | None = None


def get_verifier() -> ManifestVerifier:
    global _VERIFIER
    if _VERIFIER is None:
        _VERIFIER = ManifestVerifier()
    return _VERIFIER


def set_verifier_for_tests(verifier: ManifestVerifier | None) -> None:
    global _VERIFIER
    _VERIFIER = verifier


def refresh_jwks(*, timeout: float = 2.0) -> dict[str, Any]:
    """Cold-path JWKS refresh — call after DMS mints a new intermediate.

    Hot-path verify() never hits the network; this updates the shared verifier
    cache so newly minted issuer kids are trusted without restarting the engine.
    """
    cache = get_verifier().cache
    ok = cache.refresh(timeout=timeout)
    return {
        "ok": ok,
        "kids": list(cache.known_kids),
        "fetched_at": cache._fetched_at,
    }


def verify_and_check_pool(manifest: Manifest, pool_id: str) -> VerifiedManifest:
    if not (manifest.pool_id or "").strip():
        raise PoolRequired("manifest.pool_id is required")
    if pool_id != manifest.pool_id:
        raise PoolMismatch(
            f"request pool {pool_id!r} does not match manifest.pool_id {manifest.pool_id!r}"
        )
    return get_verifier().verify(manifest)


def _plan_kind(plan: dict[str, Any]) -> str:
    kind = plan.get("kind") if isinstance(plan, dict) else None
    if isinstance(kind, str) and kind.strip():
        return kind.strip().lower()
    # Default: treat as SQL when body carries sql, else bind.
    return "sql"


def submit_request(body: SubmitRequest) -> QueryResult:
    """HTTP/contract entry: bind and/or execute under a signed manifest."""
    run_id = new_run_id()
    kind = _plan_kind(dict(body.plan or {}))
    session_id = body.manifest.session_id
    pool_id = body.pool.id
    issuer_kid: str | None = None
    queue_ms: float | None = None
    exec_ms: float | None = None
    row_count: int | None = None

    try:
        verified = verify_and_check_pool(body.manifest, pool_id)
        issuer_kid = verified.issuer_kid
        get_session_registry().bind(verified)

        if kind in {"session_bind", "bind"}:
            record_run(
                run_id=run_id,
                kind="session_bind",
                status="bound",
                session_id=session_id,
                pool_id=pool_id,
                issuer_kid=issuer_kid,
            )
            return QueryResult(ok=True, status="bound", run_id=run_id, output={"session_id": session_id})

        if kind != "sql":
            raise ManifestMalformed(f"unknown plan.kind {kind!r}; expected session_bind or sql")

        sql = (body.body or {}).get("sql") if isinstance(body.body, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            raise SqlRequired("body.sql is required for plan.kind=sql")

        rows, queue_ms, exec_ms = execute_sql(verified, sql, pool_id=pool_id)
        row_count = len(rows)
        record_run(
            run_id=run_id,
            kind="sql",
            status="ok",
            session_id=session_id,
            pool_id=pool_id,
            queue_ms=queue_ms,
            exec_ms=exec_ms,
            row_count=row_count,
            issuer_kid=issuer_kid,
        )
        return QueryResult(
            ok=True,
            status="ok",
            run_id=run_id,
            output={"columns": list(rows[0].keys()) if rows else [], "rows": rows},
        )
    except PoolSaturated as exc:
        record_run(
            run_id=run_id,
            kind=kind,
            status=exc.code,
            session_id=session_id,
            pool_id=pool_id,
            issuer_kid=issuer_kid,
            error=str(exc),
        )
        return QueryResult(ok=False, status=exc.code, run_id=run_id, error=str(exc))
    except ManifestError as exc:
        code = getattr(exc, "code", "manifest_error")
        record_run(
            run_id=run_id,
            kind=kind,
            status=code,
            session_id=session_id,
            pool_id=pool_id,
            queue_ms=queue_ms,
            exec_ms=exec_ms,
            issuer_kid=issuer_kid,
            error=str(exc),
        )
        return QueryResult(ok=False, status=code, run_id=run_id, error=str(exc))


def execute_sql(
    verified: VerifiedManifest,
    sql: str,
    *,
    pool_id: str | None = None,
    db_path: Any = None,
    explain_gate: bool | None = None,
    params: Sequence[Any] | None = None,
) -> tuple[list[dict[str, Any]], float, float]:
    """Enforce + execute rewritten SQL. Returns (rows, queue_ms, exec_ms).

    When ``explain_gate`` is True (default: env ``DMS_EXPLAIN_GATE`` unset or
    truthy), run DuckDB EXPLAIN after manifest enforce and before fetch.
    ``params`` are forwarded to both EXPLAIN and the fetch execute so bind
    placeholders in caller SQL survive predicate wrapping.
    """
    import os

    from CortexOS.dms.sql_validate_gate import SqlGateAbstain, explain_dry_run

    safe_sql = enforce_manifest(sql, verified)
    use_explain = (
        explain_gate
        if explain_gate is not None
        else os.environ.get("DMS_EXPLAIN_GATE", "1").lower() not in ("0", "false", "no")
    )
    pool = get_read_pool()
    pid = pool_id or verified.manifest.pool_id or pool.pool_id
    with pool.acquire(pool_id=pid) as queue_ms:
        started = time.perf_counter()
        path = warehouse_path(db_path) if db_path is not None else DEFAULT_DB
        con = get_connection(path, read_only=read_only_queries_enabled() or True)
        try:
            if use_explain:
                ok, detail = explain_dry_run(con, safe_sql, params=params)
                if not ok:
                    raise SqlGateAbstain(
                        f"EXPLAIN rejected SQL: {detail}",
                        violations=[f"EXPLAIN_FAILED:{detail}"],
                    )
            # Best-effort statement timeout (DuckDB may ignore unknown settings).
            timeout_s = pool.config.statement_timeout_s
            if timeout_s > 0:
                try:
                    con.execute(f"SET statement_timeout='{int(timeout_s * 1000)}ms'")
                except Exception:  # noqa: BLE001
                    pass
            rel = con.execute(safe_sql, params) if params is not None else con.execute(safe_sql)
            rows_raw = rel.fetchall()
            columns = [d[0] for d in rel.description] if rel.description else []
            rows = [dict(zip(columns, row, strict=False)) for row in rows_raw]
        finally:
            con.close()
        exec_ms = (time.perf_counter() - started) * 1000.0
    return rows, float(queue_ms), exec_ms


def execute_count(
    verified: VerifiedManifest,
    sql: str,
    *,
    pool_id: str | None = None,
    db_path: Any = None,
) -> int | None:
    """COUNT(*) over LIMIT/ORDER-stripped SQL, through ``enforce_manifest`` once.

    Pass the same pre-enforce SQL used for the listing query (not already
    rewritten SQL) so predicates are injected exactly once on the count path.
    """
    import sqlglot

    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
        tree.set("limit", None)
        tree.set("order", None)
        inner = tree.sql(dialect="duckdb")
        count_sql = f"SELECT COUNT(*) AS n FROM ({inner}) _t"
        rows, _, _ = execute_sql(verified, count_sql, pool_id=pool_id, db_path=db_path)
        if not rows:
            return None
        return int(next(iter(rows[0].values())))
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "PoolMismatch",
    "PoolRequired",
    "SqlRequired",
    "execute_count",
    "execute_sql",
    "get_verifier",
    "refresh_jwks",
    "set_verifier_for_tests",
    "submit_request",
    "verify_and_check_pool",
]
