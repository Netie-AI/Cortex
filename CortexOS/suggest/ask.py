"""SUGGEST (#308) ask seam: attach grounded follow-ups to a contract answer.

Off unless ``CORTEX_SUGGEST=1``. Off returns the answer dict untouched, so the
``/v1/contract/ask`` envelope stays byte-identical to contract 1.4.0. On, the
grant is the signed session manifest the route already resolved, the schema is
the active pack's ontology (agent-visible properties and link types), and the
result is the rows the route is about to serve. Abstains and clarifies never
get follow-ups. ``CORTEX_SUGGEST_MODEL=1`` additionally rewords them through
OpenVault FreeRoute; the templates are served whenever that fails.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.suggest.followups import Join, Rephrase, Result, Schema, Table, suggest

ENABLED_ENV = "CORTEX_SUGGEST"

_ANSWERED_BADGES = frozenset({"certified", "governed_metric", "query_skill", "session"})
_CLARIFY_TOKENS = frozenset({"needs_clarification", "clarify", "clarification"})
_ABSTAIN_TOKENS = frozenset({"abstain", "blocked", "refused"})
_CLARIFY_KEYS = ("clarify", "clarification", "clarifying_question", "needs_clarification")


def suggest_enabled() -> bool:
    return os.environ.get(ENABLED_ENV, "").strip() == "1"


def _badge(data: Mapping[str, Any]) -> str:
    prov = data.get("provenance")
    raw = prov.get("badge") if isinstance(prov, Mapping) else getattr(prov, "badge", None)
    return str(getattr(raw, "value", raw) or "").lower()


def not_answered(data: Mapping[str, Any]) -> str | None:
    """Why this answer gets no follow-ups (abstain, clarify, nothing served), else None."""
    tokens = {str(data.get(k) or "").lower() for k in ("route", "layer", "badge")}
    if tokens & _CLARIFY_TOKENS or any(data.get(k) for k in _CLARIFY_KEYS):
        return "clarify: no follow-ups on a clarify"
    badge = _badge(data)
    if tokens & _ABSTAIN_TOKENS or badge not in _ANSWERED_BADGES:
        return "abstain: no follow-ups on an abstain"
    if not str(data.get("sql_used") or "").strip():
        return "no served SQL"
    if not data.get("rows"):
        return "no result rows"
    return None


def sql_tables(sql: str) -> tuple[str, ...]:
    try:
        root = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - unparseable SQL names no table
        return ()
    ctes = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    names = (t.name.lower() for t in root.find_all(exp.Table) if t.name)
    return tuple(dict.fromkeys(n for n in names if n not in ctes))


def load_schema() -> Schema:
    from CortexOS.ontology.registry import load_link_types, load_object_types

    tables: dict[str, Table] = {}
    hidden: set[str] = set()
    for obj in load_object_types():
        visible = [p for p in obj.properties if p.agent_visible]
        hidden.update(p.name for p in obj.properties if not p.agent_visible)
        tables[obj.id] = Table(
            name=obj.id,
            columns=tuple(p.name for p in visible),
            types={p.name: p.type for p in visible},
            primary_key=obj.primary_key,
        )
    joins = tuple(
        Join(lt.from_object, lt.from_property, lt.to_object, lt.to_property)
        for lt in load_link_types()
    )
    return Schema(tables=tables, joins=joins, hidden=frozenset(hidden))


def _granted(verified: Any) -> frozenset[str]:
    predicates = getattr(verified, "row_predicates", None) or {}
    return frozenset(str(name).lower() for name in predicates)


def _default_rephrase() -> Rephrase | None:
    from CortexOS.suggest.freeroute_rephrase import freeroute_rephrase, model_enabled

    return freeroute_rephrase if model_enabled() else None


def attach_followups(
    data: dict[str, Any],
    *,
    question: str,
    verified: Any,
    schema: Schema | None = None,
    rephrase: Rephrase | None = None,
) -> dict[str, Any]:
    if not suggest_enabled():
        return data
    reason = not_answered(data)
    granted = _granted(verified)
    if reason is None and not granted:
        reason = "no session grant"
    if reason is None and schema is None:
        try:
            schema = load_schema()
        except Exception as exc:  # noqa: BLE001 - no ontology means no grounding
            reason = f"schema unavailable: {type(exc).__name__}"
    if reason is not None or schema is None:
        data["followups"] = []
        data["followups_reason"] = reason
        return data
    result = Result(
        question=question,
        rows=tuple(r for r in data["rows"] if isinstance(r, Mapping)),
        tables=sql_tables(str(data["sql_used"])),
        granted=granted,
    )
    outcome = suggest(result, schema, rephrase=rephrase or _default_rephrase())
    data["followups"] = outcome.followups
    data["followups_reason"] = outcome.reason
    return data
