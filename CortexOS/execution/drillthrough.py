"""T7 drill-through — rewrite answer SQL to contributing rows + provenance.

Architecture §4.5–4.6: strip aggregates, keep joins/WHERE, project provenance
columns, LIMIT 5000. Execution must use the same signed session manifest as
the original answer (HMAC token binds session + sql digest).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import sqlglot
from sqlglot import exp

# Flat T7 (DMS) + optional STRUCT Appendix A.
_PROVENANCE_COLS = ("_src_ref_id", "_src_row", "_ingest_id", "_src")
_DRILL_LIMIT = 5000
_TOKEN_TTL_S = 3600


class DrillthroughError(Exception):
    code = "drillthrough_error"


class DrillthroughTokenInvalid(DrillthroughError):
    code = "drillthrough_token_invalid"


class DrillthroughSessionMismatch(DrillthroughError):
    code = "drillthrough_session_mismatch"


@dataclass(slots=True)
class DrillRewrite:
    sql: str
    count_sql: str
    approximate: bool
    measure_expr: str | None


def _hmac_key() -> bytes:
    raw = os.environ.get("CORTEX_DRILLTHROUGH_HMAC") or "cortex-drillthrough-dev-only"
    return raw.encode("utf-8")


def _b64url(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    import base64

    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def sql_digest(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()


def mint_token(
    *,
    answer_id: str,
    session_id: str,
    manifest_hash: str,
    sql: str,
    expires_at: float | None = None,
) -> str:
    """HMAC token binding answer + session + manifest + sql digest."""
    payload = {
        "answer_id": answer_id,
        "session_id": session_id,
        "manifest_hash": manifest_hash,
        "sql_digest": sql_digest(sql),
        "sql": sql,
        "expires_at": expires_at if expires_at is not None else time.time() + _TOKEN_TTL_S,
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url(hmac.new(_hmac_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"dt_{body}.{sig}"


def verify_token(token: str) -> dict[str, Any]:
    if not token or not token.startswith("dt_"):
        raise DrillthroughTokenInvalid("token must start with dt_")
    try:
        body, sig = token[3:].rsplit(".", 1)
    except ValueError as exc:
        raise DrillthroughTokenInvalid("malformed token") from exc
    expect = _b64url(hmac.new(_hmac_key(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expect, sig):
        raise DrillthroughTokenInvalid("token signature mismatch")
    try:
        payload = json.loads(_b64url_decode(body))
    except Exception as exc:  # noqa: BLE001
        raise DrillthroughTokenInvalid("token payload corrupt") from exc
    if float(payload.get("expires_at") or 0) < time.time():
        raise DrillthroughTokenInvalid("token expired")
    for key in ("answer_id", "session_id", "manifest_hash", "sql_digest", "sql"):
        if key not in payload:
            raise DrillthroughTokenInvalid(f"token missing {key}")
    return payload


def _strip_aggregates(select: exp.Select) -> tuple[list[exp.Expression], str | None, bool]:
    """Replace aggregate projections with their args; return measure for ORDER BY."""
    approximate = False
    measure: str | None = None
    new_exprs: list[exp.Expression] = []
    for proj in select.expressions:
        alias = proj.alias_or_name if isinstance(proj, exp.Alias) else None
        node = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(node, exp.AggFunc):
            args = list(node.expressions) or ([node.this] if node.this is not None else [])
            if not args:
                approximate = True
                continue
            inner = args[0].copy()
            if measure is None:
                measure = inner.sql(dialect="duckdb")
            if alias:
                new_exprs.append(exp.alias_(inner, alias, table=False))
            else:
                new_exprs.append(inner)
        else:
            new_exprs.append(proj.copy())
    return new_exprs, measure, approximate


def _table_aliases(select: exp.Select) -> list[tuple[str | None, str]]:
    """Return (alias_or_None, table_name) for FROM + JOINs."""
    out: list[tuple[str | None, str]] = []
    from_ = select.args.get("from_")
    if from_ is not None:
        tbl = from_.this
        if isinstance(tbl, exp.Table):
            out.append((tbl.alias_or_name if tbl.alias else None, tbl.name))
        elif isinstance(tbl, exp.Alias) and isinstance(tbl.this, exp.Table):
            out.append((tbl.alias, tbl.this.name))
    for join in select.args.get("joins") or []:
        tbl = join.this
        if isinstance(tbl, exp.Table):
            out.append((tbl.alias_or_name if tbl.alias else None, tbl.name))
        elif isinstance(tbl, exp.Alias) and isinstance(tbl.this, exp.Table):
            out.append((tbl.alias, tbl.this.name))
    return out


def _provenance_projections(aliases: list[tuple[str | None, str]]) -> list[exp.Expression]:
    projs: list[exp.Expression] = []
    for alias, name in aliases:
        prefix = alias or name
        for col in _PROVENANCE_COLS:
            # Project as prefix_col so multi-table drills stay distinct.
            qualified = exp.column(col, table=prefix)
            projs.append(exp.alias_(qualified, f"{prefix}_{col}", table=False))
    return projs


def rewrite_for_drillthrough(sql: str, *, dialect: str = "duckdb") -> DrillRewrite:
    """Architecture §4.5 rewrite rules."""
    tree = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(tree, exp.Select):
        raise DrillthroughError("drill-through requires a SELECT statement")

    approximate = False
    # HAVING → WHERE when possible
    having = tree.args.get("having")
    if having is not None:
        where = tree.args.get("where")
        having_pred = having.this
        if where is None:
            tree.set("where", exp.Where(this=having_pred.copy()))
        else:
            tree.set(
                "where",
                exp.Where(this=exp.and_(where.this.copy(), having_pred.copy())),
            )
        tree.set("having", None)
        # Aggregates in HAVING after strip may be wrong — flag approximate
        approximate = True

    tree.set("group", None)
    new_exprs, measure, agg_approx = _strip_aggregates(tree)
    approximate = approximate or agg_approx
    if not new_exprs:
        raise DrillthroughError("no projectable columns after stripping aggregates")

    aliases = _table_aliases(tree)
    prov = _provenance_projections(aliases)
    tree.set("expressions", new_exprs + prov)

    if measure:
        tree.set("order", exp.Order(expressions=[exp.Ordered(this=sqlglot.parse_one(measure, read=dialect), desc=True)]))
    tree.set("limit", exp.Limit(expression=exp.Literal.number(_DRILL_LIMIT)))

    drill_sql = tree.sql(dialect=dialect)
    # COUNT(*) over the same joins/filters without limit/order/provenance-only noise
    count_tree = sqlglot.parse_one(sql, read=dialect)
    if not isinstance(count_tree, exp.Select):
        raise DrillthroughError("count rewrite failed")
    count_tree.set("group", None)
    count_tree.set("having", None)
    count_tree.set("order", None)
    count_tree.set("limit", None)
    count_tree.set("expressions", [exp.alias_(exp.Count(this=exp.Star()), "contributing_count", table=False)])
    # Re-apply WHERE merge if original had HAVING — already handled above for drill;
    # for count, strip aggregates from WHERE if we moved HAVING (use drill where).
    if tree.args.get("where") is not None:
        count_tree.set("where", tree.args["where"].copy())
    count_sql = count_tree.sql(dialect=dialect)

    return DrillRewrite(
        sql=drill_sql,
        count_sql=count_sql,
        approximate=approximate,
        measure_expr=measure,
    )


def execute_drillthrough(
    token: str,
    *,
    expected_session_id: str | None = None,
    expected_manifest_hash: str | None = None,
) -> dict[str, Any]:
    """Verify token, resolve session manifest, rewrite + enforce_manifest execute."""
    from cortex_contract.execution import canonical_manifest_bytes

    from CortexOS.execution.session_manifests import (
        SessionExpired,
        SessionUnbound,
        get_session_registry,
    )
    from CortexOS.execution.submit import execute_sql

    payload = verify_token(token)
    session_id = str(payload["session_id"])
    if expected_session_id and expected_session_id != session_id:
        raise DrillthroughSessionMismatch("session_id does not match token")
    try:
        verified = get_session_registry().resolve(session_id)
    except (SessionUnbound, SessionExpired) as exc:
        raise DrillthroughSessionMismatch(str(exc)) from exc

    mhash = hashlib.sha256(canonical_manifest_bytes(verified.manifest)).hexdigest()
    token_hash = expected_manifest_hash or str(payload["manifest_hash"])
    if str(mhash) != token_hash:
        raise DrillthroughSessionMismatch("manifest_hash mismatch — refusing ACL bypass")

    sql = str(payload["sql"])
    if sql_digest(sql) != str(payload["sql_digest"]):
        raise DrillthroughTokenInvalid("sql digest mismatch")

    rewritten = rewrite_for_drillthrough(sql)
    rows, _, _ = execute_sql(verified, rewritten.sql)
    try:
        count_rows, _, _ = execute_sql(verified, rewritten.count_sql)
        total = (
            int(count_rows[0].get("contributing_count") or 0) if count_rows else len(rows)
        )
    except Exception:  # noqa: BLE001 — count is best-effort
        total = len(rows)

    return {
        "answer_id": payload["answer_id"],
        "session_id": session_id,
        "sql_used": rewritten.sql,
        "approximate": rewritten.approximate,
        "row_count": len(rows),
        "total_count": total,
        "rows": rows,
    }


def manifest_content_hash(manifest: Any) -> str:
    from cortex_contract.execution import canonical_manifest_bytes

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
