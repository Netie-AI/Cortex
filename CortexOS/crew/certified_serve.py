"""GEN-CERTIFIED-MEASURE-01: a certified measure that resolves the ask is served, not re-derived.

dms#231 F3: on ``cq_supplier_ranking`` the model wrote its own risk formula and
the static gate validated it. Judging whether model-written SQL "equals" a
certified formula is an equivalence problem this module does not attempt.
Instead, when the intent contains a certified question (or synonym) phrase and
that certified query computes a measure, the certified SQL itself is the
answer's SQL and the model is not called. If the ask carries something the
certified query cannot express (extra words, a filter, a different grain, a
conflicting top-N), the ask is a named ABSTAIN:
``certified_measure_cannot_express:<what>``.

Only typed parameters the certified query already declares are applied: today
that is ``LIMIT`` (top-N) from the caller's ``query_plan``.

The residue check fails closed: a character it cannot read (non-ASCII script,
full-width Latin, operators such as ``!=``) abstains as ``chars(...)``, a short
all-caps token (``MY``) is never filler, and an unknown ``query_plan`` key
abstains as ``plan_key(...)``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CANNOT_EXPRESS = "certified_measure_cannot_express"
CHECK = "certified_query"
IDENTITY = "cortex:crew:certified-query"
# DMS bind_plan's "no explicit top-N" marker (semantic_retrieve.bind_plan).
_DMS_DEFAULT_LIMIT = 50
# Words that ask for nothing: politeness and question scaffolding only. Any
# other leftover word could be a filter, a grain or a time window. Single
# letters stay out: "warehouse A" is a code, not an article.
_FILLER = frozenset(
    {
        "an",
        "the",
        "please",
        "show",
        "me",
        "give",
        "tell",
        "list",
        "what",
        "whats",
        "is",
        "are",
        "our",
        "can",
        "could",
        "you",
        "want",
        "to",
        "see",
        "need",
        "would",
        "like",
        "hi",
        "hello",
        "thanks",
        "thank",
    }
)
# Characters the residue check can see. _norm keeps only [a-z0-9], so any other
# character (a non-ASCII script, full-width Latin, an operator such as != or <)
# would vanish before the check; an intent carrying one abstains instead.
_PLAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789?.,!' \t\n")
_MAX_LIMIT = 10_000
# The keys DMS semantic_retrieve.bind_plan emits; each one is checked below.
_PLAN_KEYS = frozenset({"measure", "group_by", "filters", "keep_gt", "limit"})
_NUM = re.compile(r"\b\d+\b")


def _certified_rows(pack_dir: Path | str | None) -> dict[str, Mapping[str, Any]]:
    from CortexOS.crew import insights as insights_mod

    base = insights_mod._pack_dir(pack_dir)
    doc = insights_mod._read_yaml(base / "semantic" / "certified_queries.yaml")
    out: dict[str, Mapping[str, Any]] = {}
    for row in doc.get("certified") or []:
        if isinstance(row, Mapping) and str(row.get("id") or "").strip():
            out[str(row["id"]).strip()] = row
    return out


def _parse(sql: str) -> Any:
    import sqlglot
    from sqlglot import exp

    try:
        stmt = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001 - unparsable certified SQL cannot be shaped
        return None
    return stmt if isinstance(stmt, exp.Select) else None


def _is_measure(stmt: Any) -> bool:
    """The outer SELECT projects a computed value (arithmetic), not bare columns/COUNT."""
    from sqlglot import exp

    arith = (exp.Add, exp.Sub, exp.Mul, exp.Div)
    return any(
        isinstance(node, arith) for proj in stmt.expressions for node in proj.walk()
    )


def _grain(stmt: Any) -> set[str]:
    """Column names the certified answer is keyed by."""
    from sqlglot import exp

    group = stmt.args.get("group")
    if group is not None:
        return {c.name.lower() for g in group.expressions for c in g.find_all(exp.Column)}
    return {
        p.name.lower()
        for p in (proj.unalias() for proj in stmt.expressions)
        if isinstance(p, exp.Column)
    }


def _phrase_chars(row: Mapping[str, Any]) -> set[str]:
    """Punctuation the certified wording itself uses (e.g. the '-' in high-risk)."""
    raw = [row.get("question"), *(row.get("synonyms") or [])]
    return {ch for p in raw for ch in str(p or "") if ch.isascii()}


def _limit(stmt: Any) -> int | None:
    lim = stmt.args.get("limit")
    if lim is None:
        return None
    try:
        return int(lim.expression.this)
    except (AttributeError, TypeError, ValueError):
        return None


def _bound(intent_norm: str, ranking: Mapping[str, Any], rows: Mapping[str, Mapping[str, Any]]) -> tuple[str, str] | None:
    """(certified id, matched phrase): longest certified phrase inside the intent."""
    from CortexOS.crew import insights as insights_mod

    best: tuple[int, str, str] | None = None
    for ranked in ranking.get("certified") or []:
        if not isinstance(ranked, Mapping):
            continue
        cid = str(ranked.get("id") or "").strip()
        row = rows.get(cid)
        if row is None:
            continue
        raw = [row.get("question"), *(row.get("synonyms") or [])]
        for phrase in (insights_mod._norm(str(p or "")) for p in raw):
            if not phrase or not re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", intent_norm):
                continue
            if best is None or len(phrase) > best[0]:
                best = (len(phrase), cid, phrase)
    return (best[1], best[2]) if best else None


def _abstain(cid: str, what: str) -> dict[str, Any]:
    return {"action": "abstain", "id": cid, "reason": f"{CANNOT_EXPRESS}:{what}"}


def resolve(
    intent: str,
    ranking: Mapping[str, Any],
    *,
    query_plan: Mapping[str, Any] | None = None,
    pack_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """None: no certified measure resolves this ask (generate as before).

    Else ``{"action": "serve", "id", "sql"}`` or
    ``{"action": "abstain", "id", "reason"}``.
    """
    from CortexOS.crew import insights as insights_mod

    intent_norm = insights_mod._norm(intent)
    rows = _certified_rows(pack_dir)
    hit = _bound(intent_norm, ranking, rows)
    if hit is None:
        return None
    cid, phrase = hit
    sql = str(rows[cid].get("sql") or "").strip()
    stmt = _parse(sql) if sql else None
    if stmt is None or not _is_measure(stmt):
        # The ask resolves to a certified lookup/count, not a measure: out of scope.
        return None

    allowed = _PLAIN | _phrase_chars(rows[cid])
    odd = sorted({ch for ch in intent if ch not in allowed})
    if odd:
        return _abstain(cid, "chars(" + "".join(odd)[:20] + ")")
    # A short all-caps token ("MY", "US", "IS") is a code, never filler.
    codes = {t.lower() for t in re.findall(r"[A-Za-z0-9]+", intent) if t.isupper() and len(t) <= 3}

    residue = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", " ", intent_norm, count=1)
    extra = [w for w in residue.split() if w not in _FILLER or w in codes]
    if extra:
        return _abstain(cid, "terms(" + " ".join(extra)[:80] + ")")

    plan = query_plan if isinstance(query_plan, Mapping) else {}
    unknown = sorted(str(k) for k in plan if k not in _PLAN_KEYS)
    if unknown:
        # A key this check does not read could carry a filter it would ignore.
        return _abstain(cid, "plan_key(" + ",".join(unknown)[:60] + ")")
    if plan.get("filters"):
        return _abstain(cid, "filter")
    if plan.get("keep_gt") is not None:
        return _abstain(cid, "keep_gt")
    grain = _grain(stmt)
    for pair in plan.get("group_by") or []:
        col = str(pair[-1] if isinstance(pair, (list, tuple)) and pair else pair).strip().lower()
        if col and col not in grain:
            return _abstain(cid, f"grain({col})")

    raw_limit = plan.get("limit")
    want: int | None = None
    if raw_limit is not None:
        # Only a plain int in range: a bool, float, string or huge value is not a top-N.
        if type(raw_limit) is not int or not 0 < raw_limit <= _MAX_LIMIT:
            return _abstain(cid, "limit")
        want = raw_limit
        if want == _DMS_DEFAULT_LIMIT:
            want = None
    have = _limit(stmt)
    if want is not None and want != have:
        if have is None:
            return _abstain(cid, f"limit({want})")
        if _NUM.search(phrase):
            # The certified wording itself fixes N ("top 5"): a different N contradicts it.
            return _abstain(cid, f"limit({want})")
        stmt = stmt.limit(want)
        sql = stmt.sql(dialect="duckdb")
    return {"action": "serve", "id": cid, "sql": sql}


def served_envelope(hit: Mapping[str, Any]) -> dict[str, Any]:
    """generative_ask body for a served certified query. No model was called."""
    from CortexOS.crew import cot_climb

    cid = str(hit.get("id") or "")
    reason = f"{CHECK}:{cid} (no model called)"
    return {
        "ok": True,
        "status": "OK",
        "phase": "generate",
        "sql": str(hit.get("sql") or ""),
        "extracted_sql": None,
        "valid": True,
        "values": [],
        "identity": IDENTITY,
        "route": None,
        "stamp": {
            "served_provider": None,
            "served_model": None,
            "served_local": False,
            "served_reason": reason,
        },
        "check": f"{CHECK}:{cid}",
        "refuse_reason": "",
        "text": "",
        "tables": [],
        "note": (
            f"Served certified query {cid} as stored; no model called. "
            "Numbers not certified here (not executed)."
        ),
        "climb": cot_climb._climb_meta(final="CERTIFIED_QUERY_SERVED", certified_id=cid),
        "complete": False,
    }


def abstain_envelope(hit: Mapping[str, Any]) -> dict[str, Any]:
    from CortexOS.crew import cot_climb

    return {
        "ok": False,
        "status": "ABSTAIN",
        "phase": "generate",
        "sql": None,
        "valid": False,
        "values": [],
        "identity": IDENTITY,
        "route": None,
        "stamp": None,
        "check": "",
        "refuse_reason": str(hit.get("reason") or CANNOT_EXPRESS),
        "note": "",
        "climb": cot_climb._climb_meta(
            final="CERTIFIED_MEASURE_CANNOT_EXPRESS", certified_id=str(hit.get("id") or "")
        ),
        "complete": False,
    }


__all__ = ["CANNOT_EXPRESS", "CHECK", "IDENTITY", "abstain_envelope", "resolve", "served_envelope"]
