"""CORTEX-COT-CLIMB: CoT / route / improve above FreeRoute (Cortex #212).

Consumes the public Crew FreeRoute adapter (``arming``, ``complete``,
``extract_sql``, ``validate_sql``). Does not own arming, measured pick,
identity, or engine generative-ask wiring. Those are PR #215 / #211.

Think-path loop:
  think (FreeRoute purpose=think) -> generate SQL (purpose=generative_ask)
  -> ontology validate -> gen_cfsm.route_step -> improve or stop.

Reuses G1 already on tip: ``generate_ir`` / ``compile_ir`` / ``execute_cfsm``
(``dag_runner``) / ``route_step`` / ``collapse_score``. The compiled G1 node
plan is consumed in think/SQL prompts (not a sidecar stamp only). Improve
re-thinks with prior SQL + route_step. JEPA stays the cosine proxy
(PARKING_LOT P21). Unarmed is fail-closed REFUSE: no invented CoT, no SQL, no
values. Validated SQL is ABSTAIN (not executed). Never CERTIFIED. Never
COMPLETE. DMS #180 gen 57.69% / exact 38.46% WRONG=0 @ d2f116a6 is the frozen
baseline; this module does not replace it with a better %. Like-with-like is
the pinned 26 ids from that covering SHA -- not a 5-item fixture, not a
label-only n==26. this_run.exact scores against certified gold SQL when the
case id / expected_sql is present; missing gold is not invented 38.46%.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from CortexOS.execution.gen_cfsm import (
    ALLOWED_HORIZONS,
    DECISION_AUDIT_FAIL,
    DECISION_CONTINUE,
    DECISION_FORCE_AUDIT,
    DECISION_REGENERATE,
    DECISION_TERMINATE,
    collapse_score,
    compile_ir,
    execute_cfsm,
    generate_ir,
    route_step,
)
from CortexOS.execution.scoreboard import embed_goal

HORIZON = 3
JEPA_PATH = "proxy"
SLICE = "CORTEX-COT-CLIMB"
LIKE_WITH_LIKE_CORPUS = "dms-180-curated-26"
DMS_180_N = 26
# Frozen DMS #180 curated 26 ids+questions @ d2f116a6 (questions.yaml).
# Pin is identity only -- no scores. Measured gen is this-run, never invented.
DMS_180_CURATED: tuple[tuple[str, str], ...] = (
    ("cq_spend_by_country", "What is our total spend by supplier country?"),
    ("cq_stock_value_by_category", "What is total stock value by category?"),
    ("cq_sales_top5_value", "Top 5 selling SKUs by revenue"),
    ("cq_sku_count", "How many SKUs do we have in inventory?"),
    ("cq_capacity_utilisation", "Show warehouse capacity utilisation"),
    ("cq_sales_top3_volume", "Top 3 SKUs by quantity sold"),
    ("cq_sku_count_by_category", "Show SKU count by category"),
    ("cq_low_stock_wh_a", "Which SKUs are below reorder level in warehouse A?"),
    ("ops_stock_value", "What is total stock value by category?"),
    ("ops_shipment_cost", "Show shipment cost by destination"),
    ("ops_spend_boundary", "What is our total spend by supplier country?"),
    ("trap_categoty", "Show top 3 categoty sales"),
    ("trap_last_month", "Just give me last month's number"),
    ("trap_short_paraphrase", "Are we short on anything the warehouse should worry about?"),
    ("cq_cold_storage", "Which locations are cold storage?"),
    ("cq_capacity_above_90", "Which locations are above 90 percent capacity?"),
    ("cq_expired_items", "Which items are expired?"),
    ("cq_chemicals_list", "List chemicals in inventory"),
    ("cq_supplier_ranking", "Rank suppliers by combined risk and lead time score"),
    ("cq_cctv_wh_a", "Show the CCTV camera for warehouse A"),
    ("trap_alerts_ungranted", "List active alerts across the warehouse network"),
    ("trap_high_risk_pending", "Which high-risk suppliers have pending shipments?"),
    ("ops_supplier_rank_boundary", "Rank suppliers by combined risk and lead time score"),
    ("trap_delayed_count", "How many delayed incoming shipments per warehouse?"),
    ("trap_stock_by_bin", "Show stock by storage bin"),
    ("trap_how_full_synonym", "how full is each warehouse"),
)
DMS_180_IDS: frozenset[str] = frozenset(row[0] for row in DMS_180_CURATED)
_BANNED_IDEA = ("langgraph", "langchain", "n8n", "langflow", "mybot")


def _baseline() -> dict[str, Any]:
    from CortexOS.crew import freeroute as fr

    body = dict(fr.DMS_180_BASELINE)
    body.setdefault("cite", "DMS #180 Formal GREEN @ d2f116a6")
    body.setdefault("gen", "57.69%")
    body.setdefault("exact", "38.46%")
    body.setdefault("wrong", 0)
    return body


def public_map() -> dict[str, Any]:
    """GET-shaped law. No model call. No numbers invented."""
    return {
        "ok": True,
        "slice": SLICE,
        "issue": 212,
        "execute": "POST /crew/insights {generate:true} -> cot_climb.climb",
        "insights_wire": (
            "generate=true runs CoT/route/improve via climb; FreeRoute G1-G6 stay on #215 @ 50267289"
        ),
        "complete": False,
        "status": "INCOMPLETE",
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "like_with_like": False,
        "like_with_like_corpus": LIKE_WITH_LIKE_CORPUS,
        "like_with_like_n": DMS_180_N,
        "leftover": (
            "covering: exact vs certified gold + G1 plan consumed in think; "
            "like-with-like only when pinned 26 ids @ d2f116a6; still INCOMPLETE"
        ),
        "jepa": "proxy cosine via gen_cfsm.collapse_score; no trained path named",
        "gencfsm": (
            "reuse generate_ir, compile_ir, execute_cfsm/dag_runner, route_step; "
            "G1 plan consumed in think/SQL"
        ),
        "exact": (
            "this_run.exact vs certified gold when id/expected_sql present; "
            "never invent 38.46%"
        ),
        "freeroute": "consume crew.freeroute public API only; unarmed fail-closed",
        "prompt_harness": (
            "POST /crew/prompt-harness consumes measure_climb; leftover #212 OPEN"
        ),
        "excel_ppt": "deferred #197 #198 #199",
        "live_5000_ci": False,
        "dms_sot": False,
        "issue_211_complete": False,
        "issue_212_complete": False,
        "issue_227_complete": False,
    }


def _ranked_columns(ranking: Mapping[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in ranking.get("locations") or []:
        where = row.get("where") or {}
        table = str(where.get("table") or row.get("id") or "").lower()
        if not table:
            continue
        cols = [str(c) for c in (where.get("columns") or []) if str(c).strip()]
        if cols:
            seen = out.setdefault(table, [])
            seen.extend(c for c in cols if c not in seen)
    return out


async def _call_runner(
    runner: Any,
    *,
    purpose: str,
    prompt: str,
    bearer: str | None,
) -> dict[str, Any]:
    try:
        out = await runner(None, purpose=purpose, prompt=prompt, bearer=bearer)
    except TypeError:
        out = await runner(None, purpose=purpose, prompt=prompt)
    return out if isinstance(out, dict) else {}


def _allowed_tables(ranking: Mapping[str, Any]) -> set[str]:
    tables: set[str] = set()
    for row in ranking.get("locations") or []:
        table = ((row.get("where") or {}).get("table") or row.get("id") or "")
        if table:
            tables.add(str(table).lower())
    for row in list(ranking.get("metrics") or []) + list(ranking.get("certified") or []):
        for table in (row.get("where") or {}).get("tables") or []:
            tables.add(str(table).lower())
    return tables


def like_with_like(corpus: str, ids: Sequence[str]) -> bool:
    """True only on the frozen 26 ids. n==26 with a label is not enough."""
    got = [str(x).strip() for x in ids if str(x).strip()]
    return (
        (corpus or "").strip() == LIKE_WITH_LIKE_CORPUS
        and len(got) == DMS_180_N
        and set(got) == DMS_180_IDS
    )


def normalize_sql(sql: str) -> str:
    """Stdlib fold for exact compare. Not a second SQL engine."""
    return " ".join((sql or "").replace(";", " ").split()).strip().lower()


def sql_exact(got: str, gold: str) -> bool:
    """Exact only when both sides exist. Empty SQL is never an exact hit."""
    if not (got or "").strip() or not (gold or "").strip():
        return False
    return normalize_sql(got) == normalize_sql(gold)


_GOLD: dict[str, str] | None = None


def certified_gold_sql() -> dict[str, str]:
    """Certified query gold from the DMS pack YAML. Pack modules stay unimported."""
    global _GOLD
    if _GOLD is not None:
        return _GOLD
    from CortexOS.crew import insights as insights_mod

    doc = insights_mod._read_yaml(
        insights_mod._pack_dir() / "semantic" / "certified_queries.yaml"
    )
    out: dict[str, str] = {}
    for row in doc.get("certified") or []:
        if not isinstance(row, Mapping):
            continue
        cid = str(row.get("id") or "").strip()
        sql = str(row.get("sql") or "").strip()
        if cid and sql:
            out[cid] = sql
    _GOLD = out
    return out


def gold_sql_for(case_id: str, expected_sql: str = "") -> str:
    """Case expected_sql wins. Else certified YAML by id. Never invent gold."""
    pinned = (expected_sql or "").strip()
    if pinned:
        return pinned
    return certified_gold_sql().get(str(case_id or "").strip(), "")


# GEN-CERTIFIED-MEASURE-01: a certified formula that resolves the ask is law.
# retrieve_ontology scores a certified phrase contained in the intent at >= 25
# (20 + 5 L0 bonus). Token overlap alone scores far lower and does not bind.
CERTIFIED_MEASURE_MISS = "certified_measure_not_used"
_PHRASE_CERTIFIED = 25


def _measure_sql(mid: str, pack_dir: Any = None) -> str:
    from CortexOS.crew import insights as insights_mod

    base = insights_mod._pack_dir(pack_dir)
    doc = insights_mod._read_yaml(base / "semantic" / "certified_queries.yaml")
    for row in doc.get("certified") or []:
        if isinstance(row, Mapping) and str(row.get("id") or "").strip() == mid:
            return str(row.get("sql") or "")
    return ""


_ARITH_TYPES: tuple[str, ...] = ("Add", "Sub", "Mul", "Div", "Neg")
_WRAPPERS: tuple[str, ...] = ("Round", "Cast", "TryCast", "Alias", "Paren")
_AGG = {"Sum": "sum", "Avg": "avg", "Min": "min", "Max": "max", "Count": "count"}
_EVAL_ROWS = 5
_EVAL_TRIALS = 3


class _NoEval(Exception):
    """The subtree has a node the numeric evaluator does not model."""


def _canon(node: Any) -> Any:
    """Parens, table qualifiers and literal spelling (60 vs 60.0) dropped.

    The tree still encodes precedence. Operands of + and * are ordered, so a
    swap is not a miss. Used only where numeric evaluation cannot decide.
    """
    from sqlglot import exp

    out = node.copy()
    while True:
        parens = list(out.find_all(exp.Paren))
        if not parens:
            break
        for paren in parens:
            if paren is out:
                out = paren.this
            else:
                paren.replace(paren.this)
    for col in list(out.find_all(exp.Column)):
        col.set("table", None)
    for lit in list(out.find_all(exp.Literal)):
        if not lit.is_string:
            try:
                lit.set("this", repr(float(lit.this)))
            except (TypeError, ValueError):
                pass
    for node in reversed(list(out.walk())):
        if isinstance(node, (exp.Add, exp.Mul)):
            left, right = node.this, node.expression
            if left is not None and right is not None and left.sql() > right.sql():
                node.set("this", right)
                node.set("expression", left)
    return out


def _peel(node: Any) -> Any:
    """Outer ROUND / CAST / alias / parens: presentation, not the measure."""
    while node is not None and type(node).__name__ in _WRAPPERS:
        node = node.this
    return node


def _col_vector(name: str, trial: int) -> list[float]:
    import hashlib

    seed = hashlib.sha256(f"{name.lower()}|{trial}".encode()).digest()
    # Values in [1, 97): nonzero, so division is defined on both sides.
    return [1.0 + (seed[i] * 256 + seed[i + 1]) % 9600 / 100.0 for i in range(0, 2 * _EVAL_ROWS, 2)]


def _eval(node: Any, trial: int) -> list[float]:
    """Vector value of an arithmetic/aggregate tree over deterministic columns."""
    from sqlglot import exp

    kind = type(node).__name__
    if isinstance(node, exp.Paren):
        return _eval(node.this, trial)
    if isinstance(node, exp.Column):
        return _col_vector(node.name, trial)
    if isinstance(node, exp.Literal):
        if node.is_string:
            raise _NoEval(kind)
        return [float(node.this)] * _EVAL_ROWS
    if isinstance(node, exp.Neg):
        return [-v for v in _eval(node.this, trial)]
    if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        left = _eval(node.this, trial)
        right = _eval(node.expression, trial)
        if isinstance(node, exp.Add):
            return [a + b for a, b in zip(left, right, strict=True)]
        if isinstance(node, exp.Sub):
            return [a - b for a, b in zip(left, right, strict=True)]
        if isinstance(node, exp.Mul):
            return [a * b for a, b in zip(left, right, strict=True)]
        if any(b == 0 for b in right):
            raise _NoEval("div0")
        return [a / b for a, b in zip(left, right, strict=True)]  # DuckDB '/' is float
    if isinstance(node, exp.Round):
        digits = node.args.get("decimals")
        places = int(_eval(digits, trial)[0]) if digits is not None else 0
        return [round(v, places) for v in _eval(node.this, trial)]
    if isinstance(node, exp.Cast) and node.to.is_type(*exp.DataType.REAL_TYPES):
        return _eval(node.this, trial)
    if kind in _AGG:
        if node.args.get("distinct") or isinstance(node.this, (exp.Star, exp.Distinct)):
            raise _NoEval(kind)
        vals = _eval(node.this, trial)
        agg = {
            "sum": sum(vals),
            "avg": sum(vals) / len(vals),
            "min": min(vals),
            "max": max(vals),
            "count": float(len(vals)),
        }[_AGG[kind]]
        return [agg] * _EVAL_ROWS
    raise _NoEval(kind)


def _same_measure(got: Any, want: Any) -> bool:
    """Same value on every row: numeric check, structural fallback.

    An outer ROUND/CAST is presentation and is ignored on both sides; any inner
    ROUND is evaluated. 60 vs 60.0, regrouped chains, x/y*100 vs 100.0*x/y are
    the same measure. CASE, subqueries, window functions are not modelled and
    fall back to exact structure after canonicalisation.
    """
    import math

    a, b = _peel(got), _peel(want)
    if a is None or b is None:
        return False
    try:
        for trial in range(_EVAL_TRIALS):
            va, vb = _eval(a, trial), _eval(b, trial)
            if not all(math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12) for x, y in zip(va, vb, strict=True)):
                return False
        return True
    except _NoEval:
        return bool(_canon(a) == _canon(b))
    except (ArithmeticError, ValueError, TypeError):
        return False


def _derived_over(node: Any, cols: set[str]) -> bool:
    """Computed (not a bare column) and reads one of the measure's source columns."""
    from sqlglot import exp

    if node is None:
        return False
    bare = _peel(node)
    if isinstance(bare, exp.Column):
        return False
    return any(c.name.lower() in cols for c in node.find_all(exp.Column))


def _inner_aliases(stmt: Any) -> dict[str, Any]:
    """Alias name -> expression, from CTEs and derived tables the query reads."""
    from sqlglot import exp

    out: dict[str, Any] = {}
    selects = [s for s in stmt.find_all(exp.Select) if s is not stmt]
    # Deepest first so outer layers can resolve through inner ones.
    for sub in reversed(selects):
        for proj in sub.expressions:
            if isinstance(proj, exp.Alias) and proj.alias:
                out[proj.alias.lower()] = _resolve(proj.this, out)
    return out


def _resolve(node: Any, aliases: Mapping[str, Any], depth: int = 0) -> Any:
    """Substitute alias references with the expression they name."""
    from sqlglot import exp

    if node is None or depth > 8 or not aliases:
        return node
    out = node.copy()
    if isinstance(out, exp.Column) and out.name.lower() in aliases:
        return _resolve(aliases[out.name.lower()].copy(), aliases, depth + 1)
    for col in list(out.find_all(exp.Column)):
        name = col.name.lower()
        if name in aliases and col is not out:
            col.replace(_resolve(aliases[name].copy(), aliases, depth + 1))
    return out


def _outer_measure_view(sql: str) -> dict[str, Any] | None:
    """Outer projections and ORDER BY keys, aliases resolved. None if unparsable."""
    import sqlglot
    from sqlglot import exp

    try:
        stmt = sqlglot.parse_one(sql or "", read="duckdb")
    except Exception:  # noqa: BLE001 - cannot prove the measure is used
        return None
    if not isinstance(stmt, exp.Select):
        return None
    inner = _inner_aliases(stmt)
    projections: list[Any] = []
    outer_alias: dict[str, Any] = dict(inner)
    starred = False
    for proj in stmt.expressions:
        if isinstance(proj, exp.Star) or (
            isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
        ):
            starred = True
            projections.extend(v for v in inner.values())
            continue
        body = proj.this if isinstance(proj, exp.Alias) else proj
        resolved = _resolve(body, inner)
        projections.append(resolved)
        if isinstance(proj, exp.Alias) and proj.alias:
            outer_alias[proj.alias.lower()] = resolved
    order: list[tuple[Any, bool]] = []
    ordered = stmt.args.get("order")
    for key in ordered.expressions if ordered is not None else []:
        target = key.this
        # ORDER BY 2: the ordinal names a projection, not the literal 2.
        # With a star the ordinal's column is unknown here, so it stays a literal.
        if (
            not starred
            and isinstance(target, exp.Literal)
            and not target.is_string
            and target.this.isdigit()
        ):
            pos = int(target.this) - 1
            target = projections[pos] if 0 <= pos < len(projections) else target
            order.append((target, bool(key.args.get("desc"))))
            continue
        order.append((_resolve(target, outer_alias), bool(key.args.get("desc"))))
    return {"projections": projections, "order": order}


def certified_measure(
    ranking: Mapping[str, Any], *, pack_dir: Any = None
) -> dict[str, Any] | None:
    """The certified formula that resolves this ask, or None.

    Only the top certified query whose verified question (or synonym) is a
    phrase inside the intent binds, and only when its SELECT computes a formula
    (arithmetic). A plain column or COUNT is not a formula the model could
    invent differently. Loose metric synonyms do not bind. Once bound, the
    certified formula is law for that ask: a user-supplied alternative
    weighting ("with equal weights") abstains rather than being answered.
    """
    from sqlglot import exp

    picks: list[tuple[int, str]] = []
    for row in ranking.get("certified") or []:
        if not isinstance(row, Mapping):
            continue
        mid = str(row.get("id") or "").strip()
        score = int((row.get("importance") or {}).get("score") or 0)
        if mid and score >= _PHRASE_CERTIFIED:
            picks.append((-score, mid))
    if not picks:
        return None
    mid = sorted(picks)[0][1]
    sql = _measure_sql(mid, pack_dir)
    view = _outer_measure_view(sql) if sql.strip() else None
    if view is None:
        return None
    arith = tuple(getattr(exp, n) for n in _ARITH_TYPES)
    exprs = [p for p in view["projections"] if any(isinstance(n, arith) for n in p.walk())]
    if not exprs:
        return None
    cols = {c.name.lower() for e in exprs for c in e.find_all(exp.Column)}
    rank: dict[str, Any] | None = None
    if view["order"]:
        first, desc = view["order"][0]
        if any(_same_measure(first, e) for e in exprs):
            rank = {"desc": desc}
    return {
        "id": mid,
        "expressions": [e.sql(dialect="duckdb") for e in exprs],
        "source_columns": sorted(cols),
        "rank": rank,
    }


def certified_measure_miss(sql: str, measure: Mapping[str, Any] | None) -> str:
    """'' when ``sql`` answers with the certified measure; else the named reason.

    Presence anywhere in the tree is not enough (a WHERE, a dead CASE branch
    or an unused subquery column can carry it while the answer is invented).
    The outer SELECT must:
    - project every certified expression (directly or through an alias);
    - project no other computed value over the measure's source columns;
    - order by nothing computed over those columns except the measure;
    - when the certified query ranks by the measure, rank by it first, same
      direction. Bare-column tie-breakers are allowed.
    """
    if not measure:
        return ""
    import sqlglot

    reason = f"{CERTIFIED_MEASURE_MISS}:{measure.get('id')}"
    view = _outer_measure_view(sql)
    if view is None:
        return reason
    wants = [
        sqlglot.parse_one(f"SELECT {t}", read="duckdb").expressions[0]
        for t in measure.get("expressions") or []
    ]
    if not wants:
        return ""
    cols = {str(c).lower() for c in measure.get("source_columns") or []}

    def is_measure(node: Any) -> bool:
        return any(_same_measure(node, w) for w in wants)

    projections = view["projections"]
    for want in wants:
        if not any(_same_measure(p, want) for p in projections):
            return reason
    for proj in projections:
        if _derived_over(proj, cols) and not is_measure(proj):
            return reason
    order = view["order"]
    for key, _desc in order:
        if _derived_over(key, cols) and not is_measure(key):
            return reason
    rank = measure.get("rank")
    if rank:
        if not order or not is_measure(order[0][0]) or order[0][1] != bool(rank.get("desc")):
            return reason
    return ""


def _measure_lines(measure: Mapping[str, Any] | None) -> list[str]:
    if not measure:
        return []
    return [
        f"CERTIFIED MEASURE {measure.get('id')} (use this exact expression; "
        "do not write your own formula):",
        *[f"- {e}" for e in measure.get("expressions") or []],
    ]


def curated_intents() -> list[dict[str, str]]:
    """Pinned #180 intents. No scores. Ranking filled at measure time."""
    return [{"id": i, "intent": q} for i, q in DMS_180_CURATED]


def _idea_lines(ideas: Sequence[str] | None) -> list[str]:
    out: list[str] = []
    for raw in ideas or []:
        text = str(raw).strip()
        if not text:
            continue
        low = text.lower()
        if any(mark in low for mark in _BANNED_IDEA):
            continue
        out.append(text[:240])
        if len(out) >= 8:
            break
    return out


def _ontology_lines(ranking: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in ranking.get("locations") or []:
        where = row.get("where") or {}
        table = where.get("table") or row.get("id")
        cols = where.get("columns") or []
        lines.append(f"- {table}: {', '.join(str(c) for c in cols[:24])}")
    for row in list(ranking.get("metrics") or [])[:12]:
        mid = row.get("id") or ""
        tables = (row.get("where") or {}).get("tables") or []
        if mid:
            lines.append(
                "- metric "
                + str(mid)
                + " tables="
                + ",".join(str(t) for t in tables[:8])
            )
    for row in list(ranking.get("certified") or [])[:8]:
        where = row.get("where") or {}
        q = where.get("question") or row.get("id")
        if q:
            lines.append(f"- certified question: {q}")
    for row in list(ranking.get("joins") or [])[:12]:
        src = row.get("from") or ""
        dst = row.get("to") or ""
        if src and dst:
            lines.append(f"- join {src} -> {dst}")
    return lines


def _g1_plan_lines(g1: Mapping[str, Any] | None) -> list[str]:
    plan = list((g1 or {}).get("plan") or [])
    if not plan:
        return []
    lines = [
        "G1 cFSM PLAN (reuse generate_ir/compile_ir/dag_runner; not a second engine):"
    ]
    for row in plan:
        if not isinstance(row, Mapping):
            continue
        nid = str(row.get("id") or "")
        kind = str(row.get("kind") or "")
        if nid:
            lines.append(f"- {nid}: {kind}")
    return lines


def _think_prompt(
    intent: str,
    ranking: Mapping[str, Any],
    *,
    critique: str = "",
    ideas: Sequence[str] | None = None,
    g1: Mapping[str, Any] | None = None,
    prior_sql: str = "",
    decision: str = "",
) -> str:
    lines = [
        "Think which ontology tables and metrics answer the intent.",
        "Do not emit SQL. Do not invent warehouse numbers, keys, or live CI.",
        "ONTOLOGY:",
        *_ontology_lines(ranking),
        *_g1_plan_lines(g1),
        f"INTENT: {intent}",
    ]
    idea_lines = _idea_lines(ideas)
    if idea_lines:
        lines.extend(["", "DISTILL IDEAS (Netie-native; not a second engine):", *idea_lines])
    if prior_sql.strip():
        lines.extend(
            ["", "PRIOR SQL (improve this; do not copy numbers):", prior_sql.strip()[:1500]]
        )
    if decision.strip():
        lines.append("G1 route_step: " + decision.strip())
    if critique.strip():
        lines.extend(
            ["", "PRIOR REFUSAL (improve the plan, do not emit SQL):", critique.strip()]
        )
    return "\n".join(lines)


def _sql_prompt(
    intent: str,
    ranking: Mapping[str, Any],
    *,
    critique: str = "",
    think: str = "",
    ideas: Sequence[str] | None = None,
    g1: Mapping[str, Any] | None = None,
    query_plan: Mapping[str, Any] | None = None,
    certified: Mapping[str, Any] | None = None,
) -> str:
    lines = [
        "ONTOLOGY (use only these tables and columns):",
        *_ontology_lines(ranking),
        *_g1_plan_lines(g1),
        "",
        f"INTENT: {intent}",
        *_measure_lines(certified),
    ]
    if isinstance(query_plan, Mapping):
        measure = str(query_plan.get("measure") or "").strip()
        if measure:
            lines.append(f"ONTOLOGY PLAN measure={measure}")
            group_by = query_plan.get("group_by")
            if group_by:
                lines.append("group_by=" + str(group_by)[:240])
            filters = query_plan.get("filters")
            if filters:
                lines.append("filters=" + str(filters)[:240])
    if think.strip():
        lines.extend(["", "THINK (use this plan; do not copy numbers):", think.strip()[:2000]])
    idea_lines = _idea_lines(ideas)
    if idea_lines:
        lines.extend(["", "DISTILL IDEAS (Netie-native; not a second engine):", *idea_lines])
    lines.append("Emit one DuckDB SELECT. SQL only. No invented numeric answers.")
    if critique.strip():
        lines.extend(["", "PRIOR REFUSAL (improve, do not repeat):", critique.strip()])
    return "\n".join(lines)


def _pct(num: int, den: int) -> str:
    if den <= 0:
        return "0.00%"
    return f"{(100.0 * num / den):.2f}%"


def coverage_report(
    outcomes: Sequence[Mapping[str, Any]],
    *,
    corpus: str = "",
) -> dict[str, Any]:
    """Report this-run coverage vs frozen #180 baseline. Never replaces it."""
    n = len(outcomes)
    validated = sum(1 for row in outcomes if row.get("valid"))
    wrong = sum(1 for row in outcomes if row.get("wrong"))
    exact_hit = sum(1 for row in outcomes if row.get("exact"))
    gold_n = sum(1 for row in outcomes if row.get("gold"))
    gen_label = _pct(validated, n)
    exact_label = _pct(exact_hit, n)
    ids = [str(row.get("id") or "") for row in outcomes]
    like = like_with_like(corpus, ids)
    gen_improved = False
    exact_improved = False
    if like and wrong == 0:
        try:
            gen_improved = float(gen_label.rstrip("%")) > 57.69
        except ValueError:
            gen_improved = False
        try:
            exact_improved = float(exact_label.rstrip("%")) > 38.46
        except ValueError:
            exact_improved = False
    improved = gen_improved or exact_improved
    this_corpus = (
        LIKE_WITH_LIKE_CORPUS
        if like
        else (corpus.strip() or "cortex-cot-climb fixture, not DMS #180 curated 26")
    )
    return {
        "ok": True,
        "complete": False,
        "status": "INCOMPLETE",
        "slice": SLICE,
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "like_with_like": like,
        "this_run": {
            "n": n,
            "validated": validated,
            "wrong": wrong,
            "gen": gen_label,
            "exact": exact_label,
            "exact_matched": exact_hit,
            "gold_n": gold_n,
            "corpus": this_corpus,
            "like_with_like": like,
        },
        "vs_baseline": {
            "baseline_gen": "57.69%",
            "baseline_exact": "38.46%",
            "baseline_wrong": 0,
            "this_run_gen": gen_label,
            "this_run_exact": exact_label,
            "like_with_like": like,
            "improved": improved,
            "note": (
                "like-with-like vs DMS #180 curated 26 @ d2f116a6; baseline not replaced"
                if like
                else "different corpus; do not replace DMS #180 numbers"
            ),
        },
        "jepa": JEPA_PATH,
        "issue_211_complete": False,
        "issue_212_complete": False,
        "issue_227_complete": False,
        "outcomes": [dict(row) for row in outcomes],
    }


def _envelope(
    *,
    ok: bool,
    status: str,
    arm: Mapping[str, Any],
    identity: str,
    climb: Mapping[str, Any],
    sql: str | None = None,
    valid: bool = False,
    route: Any = None,
    refuse_reason: str = "",
    tables: list[str] | None = None,
    note: str = "",
    stamp: Any = None,
    check: str = "",
    validator: str = "",
    extracted_sql: str = "",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "phase": "generate",
        "sql": sql,
        "extracted_sql": extracted_sql or None,
        "valid": valid,
        "values": [],
        "identity": identity,
        "route": route,
        "stamp": stamp if isinstance(stamp, dict) else None,
        "check": check,
        "validator": validator,
        "refuse_reason": refuse_reason,
        "arming": dict(arm),
        "text": "",
        "tables": tables or [],
        "note": note,
        "climb": dict(climb),
        "measured_baseline": _baseline(),
        "complete": False,
    }
    return body


def _climb_meta(**extra: Any) -> dict[str, Any]:
    body = {
        "slice": SLICE,
        "horizon": HORIZON,
        "jepa": JEPA_PATH,
        "gencfsm": "reused",
        "complete": False,
        "status": "INCOMPLETE",
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "think_consumed": False,
        "g1_consumed": False,
        "prior_sql_consumed": False,
        "steps": [],
        "final": None,
        "g1": None,
    }
    body.update(extra)
    return body


def _ir_plan(ir: Mapping[str, Any]) -> list[dict[str, str]]:
    plan: list[dict[str, str]] = []
    for node in ir.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        nid = str(node.get("id") or "")
        if not nid:
            continue
        plan.append({"id": nid, "kind": str(node.get("kind") or "")})
    return plan


async def _g1_skeleton(intent: str, run_id: str) -> dict[str, Any]:
    """Reuse G1 GENERATE -> COMPILE -> dag_runner execute. Not a model call."""
    ir = generate_ir(intent, HORIZON)
    plan = _ir_plan(ir)
    compiled = compile_ir(ir)
    if not compiled["ok"]:
        return {
            "ok": False,
            "stage": "compile",
            "errors": compiled.get("errors") or [],
            "horizon": HORIZON,
            "node_count": len(ir.get("nodes") or []),
            "plan": plan,
        }
    try:
        report = await execute_cfsm(
            ir,
            {"prompt": intent, "session_id": run_id},
            predicates=[{"type": "nonempty"}],
        )
    except Exception as exc:  # noqa: BLE001 - skeleton must not invent-green
        return {
            "ok": False,
            "stage": "execute",
            "errors": [f"execute:{exc}"],
            "horizon": HORIZON,
            "plan": plan,
        }
    return {
        "ok": bool(report.get("ok")),
        "stage": report.get("stage"),
        "verdict": report.get("verdict"),
        "horizon": report.get("horizon") or HORIZON,
        "node_count": report.get("node_count"),
        "errors": report.get("errors") or [],
        "plan": plan,
    }


async def climb(
    intent: str,
    ranking: Mapping[str, Any],
    *,
    complete: Any | None = None,
    bearer: str | None = None,
    ideas: Sequence[str] | None = None,
    query_plan: Mapping[str, Any] | None = None,
    pack_dir: Any = None,
) -> dict[str, Any]:
    """CoT/route/improve through FreeRoute. Fail-closed when unarmed.

    When a certified formula resolves the ask, SQL that does not compute it is
    rejected like any other gate miss; exhausting the horizon on that miss is a
    named ABSTAIN (``certified_measure_not_used:<id>``), never an invented formula.
    """
    from CortexOS.crew import freeroute as fr

    text = (intent or "").strip()
    identity = fr.identity_for("generative_ask")
    arm = fr.arming()
    if not arm.get("armed"):
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=identity,
            climb=_climb_meta(final="UNARMED", reason="FreeRoute unarmed"),
            refuse_reason=(
                "OpenVault FreeRoute unarmed: "
                + str(arm.get("detail") or "unreachable")
                + " (no invent-green keys; no invent-green CoT)"
            ),
        )

    run_id = "cot-" + uuid.uuid4().hex[:8]
    g1 = await _g1_skeleton(text or "empty", run_id)
    if not g1.get("ok") and g1.get("stage") == "compile":
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=identity,
            climb=_climb_meta(final="G1_COMPILE_FAIL", g1=g1),
            refuse_reason="gen_cfsm compile refused the think-path IR",
        )

    allowed = _allowed_tables(ranking)
    if not allowed:
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=identity,
            climb=_climb_meta(final="NO_ONTOLOGY", g1=g1),
            refuse_reason=(
                "no ranked ontology tables; cannot prove SQL stays in scope "
                "(no invent-green CoT)"
            ),
        )
    runner = complete or fr.complete
    columns = _ranked_columns(ranking)
    measure = certified_measure(ranking, pack_dir=pack_dir)
    idea_list = _idea_lines(ideas)
    g1_consumed = bool(_g1_plan_lines(g1))
    prior_sql_consumed = False
    last_sql = ""
    steps: list[dict[str, Any]] = []
    think_text = ""
    think = await _call_runner(
        runner,
        purpose="think",
        prompt=_think_prompt(text, ranking, ideas=idea_list, g1=g1),
        bearer=bearer,
    )
    if not think.get("ok"):
        return _envelope(
            ok=False,
            status="REFUSE",
            arm=arm,
            identity=think.get("identity") or identity,
            route=think.get("route"),
            stamp=think.get("stamp"),
            climb=_climb_meta(
                final="THINK_REFUSE",
                g1=g1,
                steps=steps,
                g1_consumed=g1_consumed,
            ),
            refuse_reason=str(think.get("refused") or "FreeRoute think refused"),
        )
    think_text = str(think.get("text") or "")
    steps.append(
        {
            "step": 0,
            "kind": "think",
            "purpose": "think",
            "ok": True,
            "decision": DECISION_CONTINUE,
            "g1_consumed": g1_consumed,
        }
    )

    critique = ""
    prev_collapse = 0.0
    stall = 0
    last_reason = "no sql"
    goal_blob = text + " " + " ".join(sorted(allowed))
    goal_vec = embed_goal(goal_blob)

    for step in range(1, HORIZON + 1):
        stamps: list[Any] = []
        try:
            from CortexOS.integrations import freeroute as core

            journal = core.journal()
        except Exception:  # noqa: BLE001 - journal is optional on this consumer
            journal = None
        sql_prompt = _sql_prompt(
            text,
            ranking,
            critique=critique,
            think=think_text,
            ideas=idea_list,
            g1=g1,
            query_plan=query_plan,
            certified=measure,
        )
        if journal is not None:
            with journal as stamps:
                gen = await _call_runner(
                    runner,
                    purpose="generative_ask",
                    prompt=sql_prompt,
                    bearer=bearer,
                )
        else:
            gen = await _call_runner(
                runner,
                purpose="generative_ask",
                prompt=sql_prompt,
                bearer=bearer,
            )
        if not gen.get("ok"):
            return _envelope(
                ok=False,
                status="REFUSE",
                arm=arm,
                identity=gen.get("identity") or identity,
                route=gen.get("route"),
                stamp=gen.get("stamp"),
                climb=_climb_meta(
                    final="GENERATE_REFUSE",
                    g1=g1,
                    steps=steps,
                    think_consumed=bool(think_text),
                    g1_consumed=g1_consumed,
                    prior_sql_consumed=prior_sql_consumed,
                ),
                refuse_reason=str(gen.get("refused") or "FreeRoute generative_ask refused"),
            )

        sql = fr.extract_sql(str(gen.get("text") or ""))
        extracted = str(sql or "").strip()
        checked = fr.validate_sql(sql or "", allowed, columns=columns)
        if checked.get("ok"):
            miss = certified_measure_miss(str(checked.get("sql") or ""), measure)
            if miss:
                checked = {**checked, "ok": False, "reason": miss, "check": "refused"}
        last_sql = str(checked.get("sql") or sql or last_sql)
        try:
            from CortexOS.integrations import freeroute as core

            core.note_verdict(stamps, "static_valid" if checked.get("ok") else "static_fail")
        except Exception:  # noqa: BLE001 - scoring miss is not invent-green SQL
            pass
        predicates_pass = bool(checked.get("ok"))
        state_blob = think_text + " " + str(checked.get("sql") or sql or "")
        collapse = collapse_score(embed_goal(state_blob), goal_vec)
        routed = route_step(
            collapse=collapse,
            prev_collapse=prev_collapse,
            predicates_pass=predicates_pass,
            step_count=step,
            horizon=HORIZON,
            stall_count=stall,
        )
        stall = int(routed.get("stall_count") or 0)
        prev_collapse = collapse
        last_reason = str(checked.get("reason") or last_reason)
        granted = DECISION_TERMINATE if predicates_pass else routed["decision"]
        steps.append(
            {
                "step": step,
                "kind": "generate",
                "purpose": "generative_ask",
                "ok": predicates_pass,
                "collapse": round(collapse, 6),
                "decision": routed["decision"],
                "granted": granted,
                "reason": last_reason,
                "think_consumed": bool(think_text),
            }
        )
        if predicates_pass:
            return _envelope(
                ok=True,
                status="ABSTAIN",
                arm=arm,
                identity=gen.get("identity") or identity,
                route=gen.get("route"),
                stamp=gen.get("stamp"),
                sql=str(checked.get("sql") or ""),
                extracted_sql=extracted,
                valid=True,
                tables=list(checked.get("tables") or []),
                check=str(checked.get("check") or ""),
                note=(
                    "Validated SQL via FreeRoute CoT/route/improve. "
                    "Numbers not certified (not executed). not COMPLETE."
                ),
                climb=_climb_meta(
                    final=DECISION_TERMINATE,
                    g1=g1,
                    steps=steps,
                    attempts=step,
                    think_consumed=True,
                    g1_consumed=g1_consumed,
                    prior_sql_consumed=prior_sql_consumed,
                ),
            )
        if granted in (DECISION_FORCE_AUDIT,) or step >= HORIZON:
            break
        if granted in (
            DECISION_CONTINUE,
            DECISION_REGENERATE,
            DECISION_AUDIT_FAIL,
        ):
            critique = last_reason
            think = await _call_runner(
                runner,
                purpose="think",
                prompt=_think_prompt(
                    text,
                    ranking,
                    critique=critique,
                    ideas=idea_list,
                    g1=g1,
                    prior_sql=last_sql,
                    decision=str(granted),
                ),
                bearer=bearer,
            )
            if not think.get("ok"):
                return _envelope(
                    ok=False,
                    status="REFUSE",
                    arm=arm,
                    identity=think.get("identity") or identity,
                    route=think.get("route"),
                    stamp=think.get("stamp"),
                    climb=_climb_meta(
                        final="THINK_REFUSE",
                        g1=g1,
                        steps=steps,
                        think_consumed=bool(think_text),
                        g1_consumed=g1_consumed,
                        prior_sql_consumed=prior_sql_consumed,
                    ),
                    refuse_reason=str(think.get("refused") or "FreeRoute think refused"),
                )
            think_text = str(think.get("text") or think_text)
            prior_sql_consumed = prior_sql_consumed or bool(last_sql)
            steps.append(
                {
                    "step": step,
                    "kind": "think",
                    "purpose": "think",
                    "ok": True,
                    "decision": DECISION_CONTINUE,
                    "prior_sql_consumed": bool(last_sql),
                }
            )
            continue
        break

    if measure and last_reason.startswith(CERTIFIED_MEASURE_MISS + ":"):
        return _envelope(
            ok=False,
            status="ABSTAIN",
            arm=arm,
            identity=identity,
            climb=_climb_meta(
                final="CERTIFIED_MEASURE_NOT_USED",
                g1=g1,
                steps=steps,
                attempts=len([s for s in steps if s.get("kind") == "generate"]),
                think_consumed=bool(think_text),
                g1_consumed=g1_consumed,
                prior_sql_consumed=prior_sql_consumed,
            ),
            refuse_reason=last_reason,
        )
    return _envelope(
        ok=False,
        status="REFUSE",
        arm=arm,
        identity=identity,
        climb=_climb_meta(
            final=DECISION_FORCE_AUDIT,
            g1=g1,
            steps=steps,
            attempts=HORIZON,
            think_consumed=bool(think_text),
            g1_consumed=g1_consumed,
            prior_sql_consumed=prior_sql_consumed,
        ),
        refuse_reason=(
            "CoT/route/improve exhausted horizon without ontology-valid SQL: "
            + last_reason
            + " (no invent-green SQL; not COMPLETE)"
        ),
    )


async def measure_climb(
    cases: Sequence[Mapping[str, Any]] | None = None,
    *,
    corpus: str = "",
    ideas: Sequence[str] | None = None,
    complete: Any | None = None,
) -> dict[str, Any]:
    """Coverage vs frozen #180 baseline. Not a live :5000 CI claim."""
    rows: list[Mapping[str, Any]] = [c for c in (cases or []) if isinstance(c, Mapping)]
    if not rows and (corpus or "").strip() == LIKE_WITH_LIKE_CORPUS:
        rows = curated_intents()
    idea_list = _idea_lines(ideas)
    outcomes: list[dict[str, Any]] = []
    ranking_fn: Any = None
    for case in rows:
        ranking = case.get("ranking") or {}
        if not ranking:
            if ranking_fn is None:
                from CortexOS.crew import insights as insights_mod

                ranking_fn = insights_mod.retrieve_ontology
            ranking = ranking_fn(str(case.get("intent") or ""))
        out = await climb(
            str(case.get("intent") or ""),
            ranking,
            complete=case.get("complete") or complete,
            ideas=idea_list,
        )
        wrong = bool(out.get("values")) or out.get("status") == "CERTIFIED"
        gold = gold_sql_for(
            str(case.get("id") or ""),
            str(case.get("expected_sql") or ""),
        )
        got = str(out.get("extracted_sql") or out.get("sql") or "")
        exact = sql_exact(got, gold)
        outcomes.append(
            {
                "id": case.get("id") or "",
                "ok": bool(out.get("ok")),
                "valid": bool(out.get("valid")),
                "wrong": wrong,
                "exact": exact,
                "gold": bool(gold),
                "status": out.get("status"),
                "final": (out.get("climb") or {}).get("final"),
            }
        )
    return coverage_report(outcomes, corpus=corpus)


assert HORIZON in ALLOWED_HORIZONS
assert len(DMS_180_CURATED) == DMS_180_N
assert len(DMS_180_IDS) == DMS_180_N
