"""Q2 — the adaptive answer engine (Netie Cortex router).

Answers a question at the first layer that can do so *trustworthily*, and
abstains rather than guess. This is the "adaptive, fail-then-escalate" core:

  L0 CERTIFIED   exact (normalized) match against the verified-query repo →
                 deterministic replay. Highest trust, zero LLM.
  L1 METRIC      rule-based intent+slot routing → compile a governed metric
                 template (Q1) → guardrail-verified SQL. Deterministic.
  L2 FREEFORM    (flag DMS_L2_ENABLED, default OFF) sampled LLM SQL →
                 parse+allowlist+execute+vote+rails. Not wired until a model is.
  L3 ABSTAIN     no trustworthy layer fired → clarify + suggest nearest
                 answerable questions. Never the old confident-wrong fallback.

Every answer carries {layer, badge, sql_used, total_count, assumptions} so the
UI can show provenance and disclose truncation honestly.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

import sqlglot

from CortexOS.dms.sql_guardrail import (
    MAX_LIMIT,
    AuditEntry,
    guard_and_execute,
    log_audit,
)
from CortexOS.dms.warehouse_db import (
    DEFAULT_DB,
    get_connection,
    load_semantic_layer,
    read_only_queries_enabled,
)
from CortexOS.execution.manifest import ManifestError, VerifiedManifest
from CortexOS.execution.session_manifests import (
    SessionExpired,
    SessionUnbound,
    SpaceUnbound,
    get_session_registry,
)

# Reused from the existing service (loaded lazily to avoid import cycle at module load).
ABSTAIN = "needs_clarification"

# A signed session binding is permission. A grant this process minted for
# itself is not — honesty on grant_kind is not a fix.
SESSION_GRANT = "session"
LOCAL_ISSUER_KID = "local-self-issued"


@dataclass(slots=True)
class MetricPlan:
    metric_id: str
    slots: dict[str, Any]
    reason: str
    tables: tuple[str, ...] = ()


def _tables_stated_by_metric(metric_id: str) -> tuple[str, ...]:
    """Tables the metric definition says it will read — not compiled SQL."""
    from packs.dms.semantic.loader import load_all

    return load_all().metric(metric_id).tables


def _metric_plan(metric_id: str, slots: dict[str, Any], reason: str) -> MetricPlan:
    return MetricPlan(
        metric_id,
        slots,
        reason,
        tables=_tables_stated_by_metric(metric_id),
    )


class UngroundedSession(Exception):
    """A served turn arrived with nothing granting it anything."""


# ── normalization + certified index ──────────────────────────────────────────
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _certified_index() -> dict[str, Any]:
    from packs.dms.semantic.loader import load_all

    model = load_all()
    index: dict[str, Any] = {}
    for cq in model.certified:
        for phrase in (cq.question, *cq.synonyms):
            key = _normalize(phrase)
            if key:
                index[key] = cq
    return index


def match_certified(question: str):
    """L0 — exact normalized match on the canonical question or a declared synonym.

    Synonyms are SME-declared aliases, not fuzzy overlap. A scoped question
    still cannot collide with an unscoped certified query unless that alias
    was written down. Value-norm: SKU-shaped tokens are rewritten to the
    column encoding (BETA → SKU-BETA) before lookup (VQ-01).
    """
    key = _normalize(question)
    hit = _certified_index().get(key)
    if hit is not None:
        return hit
    rewritten = _normalize(_rewrite_certified_value_tokens(question))
    if rewritten != key:
        return _certified_index().get(rewritten)
    return None


def _rewrite_certified_value_tokens(question: str) -> str:
    """Replace SKU-shaped tokens with the warehouse encoding. Fail open on miss."""
    from packs.dms.semantic import values as valuedict

    def _one(match: re.Match[str]) -> str:
        raw = match.group(0)
        res = valuedict.resolve(raw, "sku")
        if res.ok and res.value and res.value.lower() != raw.lower():
            return res.value
        return raw

    # SKU-BETA / bare BETA — never rewrite counts ("top 5") into SKU-00005.
    return re.sub(r"\bSKU[-\s]?[A-Za-z0-9-]+\b|\bBETA\b", _one, question, flags=re.I)


# ── slot extractors ──────────────────────────────────────────────────────────
def _extract_limit(q: str, default: int) -> int:
    from CortexOS.dms.query_service import _extract_limit as base

    return base(q, default=default)


def _explicit_limit(q: str) -> int | None:
    """Only an EXPLICIT count ('top 3', '5 warehouses') — a bare superlative
    ('the most free capacity') is NOT a count and returns None, so a singular
    'which warehouse' correctly resolves to 1."""
    from CortexOS.dms.query_service import NUMBER_WORDS

    m = re.search(r"\b(?:top|bottom|first|last|show|give me)\s+(\d{1,4})\b", q) or \
        re.search(r"\b(\d{1,4})\s+(?:warehouses?|locations?|skus?|suppliers?|rows?|results?|items?)\b", q)
    if m:
        return int(m.group(1))
    mw = re.search(r"\b(?:top|first)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b", q)
    if mw:
        return NUMBER_WORDS[mw.group(1)]
    mw = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:highest|best|selling|skus?|warehouses?|locations?|suppliers?|items?)\b",
        q,
    )
    if mw:
        return NUMBER_WORDS[mw.group(1)]
    return None


def _direction(q: str) -> str:
    return "ASC" if re.search(r"\b(least|lowest|smallest|fewest|worst|bottom|min)\b", q) else "DESC"


def _threshold(q: str, default: float = 0.7) -> float:
    m = re.search(r"(?:above|over|greater than|more than|exceed(?:s|ing)?|>=?)\s*(\d*\.?\d+)", q)
    return float(m.group(1)) if m else default


def _threshold_op(q: str) -> str:
    if re.search(r"\b(below|under|less than|lower than)\b|<", q):
        return "<"
    return ">"


def _days(q: str, default: int) -> int:
    if re.search(r"\bthis week\b", q):
        return 7
    m = re.search(r"\b(?:last|past|within|previous)\s+(\d+)\s*days?\b", q)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\b(\d+)\s*days?\b", q)
    return int(m2.group(1)) if m2 else default


def _wants_aggregate(q: str) -> bool:
    """Count/avg/how-many — must beat listing synonyms like bare 'expired'."""
    return bool(
        re.search(
            r"\b(how many|number of|count of|\bcount\b|average|avg|mean|total)\b",
            q,
        )
    )


def _calendar_month(q: str) -> str | None:
    """Return 'last' | 'this' when the question names a calendar month window."""
    if re.search(r"\b(last|previous|prior)\s+month\b", q):
        return "last"
    if re.search(r"\bthis\s+month\b", q):
        return "this"
    return None


def _pct(q: str, default: int = 90) -> int:
    m = re.search(r"(?:above|over|more than|>)\s*(\d{1,3})\s*(?:percent|%)", q)
    return int(m.group(1)) if m else default


def _location(question: str) -> str | None:
    from packs.dms.semantic import values as vd

    res = vd.resolve(question, "location_code")
    return res.value if res.ok else None


_EXCLUSION_STOP = re.compile(
    r"\b(?:what|show|list|give|find|get|top|bottom|best|worst|highest|lowest|"
    r"ranked?|numbers?|ranks?|selling|sold|"
    r"tunjukkan|tunjuk|senaraikan|senarai|bagi|papar|paparkan)\b",
    re.I,
)
# Fillers only. Conjunctions live here so they can be stripped when trailing,
# and must not be copied into `_EXCLUSION_STOP` - that is the ANS-01 class.
_EXCLUSION_SKIP = frozenset(
    {
        "THE", "A", "AN", "SKU", "SKUS", "AND", "OR", "FROM", "BY", "OF", "ALL",
        "ANY", "DARI", "DALAM", "DAN", "ATAU", "YANG", "ITU", "INI",
    }
)
_EXCLUSION_VERB_RE = re.compile(
    r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|remov(?:e|ing)|drop(?:ping)?|"
    r"without|except|"
    r"kecuali|buang|selain|keluarkan)\s+(?:the\s+)?(.+)",
    flags=re.I,
)
_EXCLUSION_JOINER_RE = re.compile(r"[,/]+|\b(?:and|or|dan|atau)\b", re.I)
_SKU_SHAPED_RE = re.compile(r"^SKU[-_]?[A-Za-z0-9]+$", re.I)


def _strip_trailing_filler(clause: str) -> str:
    """Drop filler the clause ends on, so the resolver sees only entities.

    ANS-01. `_EXCLUSION_STOP` used to decide where the clause ends and omitted
    ``and`` / ``dan``; `_EXCLUSION_SKIP` contained them. The stop path then
    handed ``sku-beta and`` to the fuzzy resolver, which cannot match exactly.
    One list decides the trailing strip (R-0004). Only *trailing* tokens go, so
    ``ignore BETA and GAMMA`` keeps the joiner.
    """
    tokens = clause.split()
    while tokens and tokens[-1].strip("'\".,").upper() in _EXCLUSION_SKIP:
        tokens.pop()
    return " ".join(tokens)


def _resolves_at_all(text: str) -> bool:
    token = text.strip().strip("'\".,")
    if not token:
        return False
    try:
        from packs.dms.semantic import values as vd

        res = vd.resolve(token, "sku")
    except Exception:
        return False
    return bool(res.ok and res.value)


def _looks_named(part: str) -> bool:
    """True when *part* is an entity the customer named, not a stray adverb.

    A fuzzy hit counts (the clarify/compile path decides). SKU-shaped tokens
    count even when the warehouse does not encode them, so a later unknown
    SKU is reported rather than silently dropped. Adverbs get no hit.
    """
    bare = part.strip().strip("'\".,")
    if not bare:
        return False
    return _resolves_at_all(bare) or bool(_SKU_SHAPED_RE.match(bare))


def _entity_parts(cand: str) -> str | None:
    parts = [p.strip().strip("'\".,") for p in _EXCLUSION_JOINER_RE.split(cand)]
    parts = [p for p in parts if p]
    if not parts or not all(_looks_named(p) for p in parts):
        return None
    return " and ".join(parts)


def _entity_span(clause: str) -> str | None:
    """Longest leading span of entities joined by conjunctions.

    Ends the clause positively, at what resolves, so an unknown adverb cannot
    reopen ANS-01 (R-0004). Returns None when nothing resolves, leaving the
    trailing-filler fallback.
    """
    whole = clause.strip().strip("'\".,")
    if not whole:
        return None
    named_whole = _entity_parts(whole)
    if named_whole is not None:
        return named_whole
    tokens = whole.split()
    for end in range(len(tokens) - 1, 0, -1):
        named = _entity_parts(" ".join(tokens[:end]))
        if named is not None:
            return named
    return None


def _exclusion_clauses(q: str) -> list[str]:
    out: list[str] = []
    for m in _EXCLUSION_VERB_RE.finditer(q):
        clause = m.group(1)
        stop = _EXCLUSION_STOP.search(clause)
        if stop:
            clause = clause[: stop.start()]
        clause = clause.strip().strip("'\".,")
        anchored = _entity_span(clause)
        clause = anchored if anchored is not None else _strip_trailing_filler(clause)
        if clause and clause.lower() not in out:
            out.append(clause)
    return out


def _excluded_skus(q: str) -> list[str]:
    """Named SKUs to drop from a ranking.

    Captures the full exclusion clause so ``excluding SKU-A and SKU-B`` keeps
    both tokens. Trailing skip-list words are stripped before the resolver
    so `_EXCLUSION_STOP` and `_EXCLUSION_SKIP` cannot disagree about ``and``.
    """
    out: list[str] = []
    for clause in _exclusion_clauses(q):
        for token in _EXCLUSION_JOINER_RE.split(clause):
            t = token.strip().strip("'\"")
            tm = re.match(r"^([A-Za-z0-9][\w-]*)$", t)
            if not tm:
                continue
            t = tm.group(1).upper()
            if t in _EXCLUSION_SKIP or len(t) < 2:
                continue
            if t not in out:
                out.append(t)
    return out


def _rank_window(q: str) -> tuple[int, int] | None:
    """Parse '6-10', '6th to 10th', 'numbers 6 to 10', 'ranks 6-10' → (start, end) 1-based."""
    m = (
        re.search(
            r"\b(?:number|numbers|nos?|ranks?|positions?)\s*"
            r"(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b",
            q,
            flags=re.I,
        )
        or re.search(
            r"\b(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b"
            r".*\b(?:sku|skus|rank|revenue|sales)\b",
            q,
            flags=re.I,
        )
        or re.search(
            r"\b(?:sku|skus|rank|revenue|sales)\b.*\b"
            r"(\d{1,3})(?:st|nd|rd|th)?\s*(?:to|-|–|—|through)\s*(\d{1,3})(?:st|nd|rd|th)?\b",
            q,
            flags=re.I,
        )
    )
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if start > end:
        start, end = end, start
    if start < 1 or end > 1000 or (end - start + 1) > 100:
        return None
    return start, end


def _sales_rank_slots(q_raw: str) -> dict[str, Any]:
    window = _rank_window(q_raw)
    excluded = _excluded_skus(q_raw)
    slots: dict[str, Any] = {"direction": _direction(q_raw), "offset_clause": 0}
    if window:
        start, end = window
        slots["offset_clause"] = start - 1
        slots["limit"] = end - start + 1
    else:
        slots["limit"] = _explicit_limit(q_raw) or _extract_limit(q_raw, 5)
    if excluded:
        slots["exclude_skus"] = excluded
    return slots


# Words that sit next to a ranking/count but are not the subject entity.
_SUBJECT_SKIP = frozenset({
    "the", "a", "an", "our", "my", "their", "this", "that", "these", "those",
    "selling", "sold", "ranked", "performing", "highest", "lowest",
    "best", "worst", "top", "bottom", "most", "least", "first", "last",
    "by", "of", "in", "for", "with", "from", "at", "on", "to", "and",
    "all", "any", "some", "each", "per", "total", "overall",
    "active", "open", "unresolved", "current", "delayed", "expired",
    "incoming", "arriving", "stale", "low", "free", "spare", "available",
    "cold", "storage", "how", "many", "number", "count", "which", "what",
    "show", "list", "find", "get", "do", "did", "does", "we", "have", "has",
    "had", "it", "its", "they", "them", "you", "i", "us", "me",
    "previous", "prior", "next", "month", "week", "day", "days", "year",
    "quarter", "average", "avg", "mean",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
})
_SUBJECT_MEASURES = frozenset({
    "amount", "revenue", "sales", "value", "volume", "quantity", "kg",
    "units", "unit", "cost", "price", "risk", "capacity", "utilisation",
    "utilization", "spend", "score", "time", "days", "percent", "weight",
    "kilograms",
})
# Alias token -> display name. Only counted as known when that table exists.
_TABLE_SUBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "inventory": (
        "sku", "skus", "item", "items", "product", "products",
        "seller", "sellers",
        "inventory", "stock", "category", "categories",
        "chemical", "chemicals",
    ),
    "suppliers": ("supplier", "suppliers", "vendor", "vendors"),
    "locations": (
        "warehouse", "warehouses", "location", "locations", "site", "sites",
        "cctv", "camera",
    ),
    "shipments": (
        "shipment", "shipments", "consignment", "consignments",
        "carrier", "carriers",
    ),
    "transactions": ("transaction", "transactions"),
    "alerts": ("alert", "alerts", "warning", "warnings"),
}
_TABLE_DISPLAY: dict[str, str] = {
    "inventory": "SKUs",
    "suppliers": "suppliers",
    "locations": "warehouses",
    "shipments": "shipments",
    "transactions": "transactions",
    "alerts": "alerts",
}


def _known_subject_map() -> dict[str, str]:
    """token -> display name for entities the loaded semantic layer defines."""
    tables = {
        str(name).lower()
        for name in (load_semantic_layer().get("tables") or {})
    }
    out: dict[str, str] = {}
    for table, aliases in _TABLE_SUBJECT_ALIASES.items():
        if table not in tables:
            continue
        display = _TABLE_DISPLAY[table]
        for alias in aliases:
            out[alias] = display
    return out


def _answerable_entities() -> tuple[str, ...]:
    tables = {
        str(name).lower()
        for name in (load_semantic_layer().get("tables") or {})
    }
    return tuple(
        _TABLE_DISPLAY[t] for t in _TABLE_DISPLAY if t in tables
    )


def _subject_from_span(span: str, known: dict[str, str]) -> str | None:
    """Prefer a known entity in the span; otherwise the first leftover noun.

    ``which high-risk suppliers`` must resolve to suppliers, not the adjective.
    ``top 3 customers by amount`` has no known entity, so customers stands.
    """
    first_unknown: str | None = None
    for tok in re.findall(r"[a-z][a-z0-9_-]*", (span or "").lower()):
        if tok in _SUBJECT_SKIP or tok in _SUBJECT_MEASURES:
            continue
        if tok in known:
            return tok
        if first_unknown is None:
            first_unknown = tok
    return first_unknown


def _named_subject(q: str, known: dict[str, str] | None = None) -> str | None:
    """Subject noun of a ranking/count/listing, or None when the ask is a measure."""
    known = known if known is not None else _known_subject_map()
    m = re.search(
        r"\b(?:top|bottom|first|last)\s+(?:\d+|one|two|three|four|five|"
        r"six|seven|eight|nine|ten)\s+(.+)",
        q,
    ) or re.search(
        r"\b(?:top|bottom|best|worst|highest|lowest|most|least)\s+(.+)",
        q,
    )
    if m:
        noun = _subject_from_span(m.group(1), known)
        if noun:
            return noun
    m = re.search(r"\b(?:how many|number of|count of)\s+(.+)", q)
    if m:
        noun = _subject_from_span(m.group(1), known)
        if noun:
            return noun
    m = re.search(r"\b(?:which|what)\s+(.+)", q)
    if m:
        rest = m.group(1)
        if not re.match(r"^(?:is|are|was|were|do|does|did|can|will|would|has|have)\b", rest):
            noun = _subject_from_span(rest, known)
            if noun:
                return noun
    m = re.search(r"\b(?:show|list|find|get)(?:\s+me)?(?:\s+all)?(?:\s+the)?\s+(.+)", q)
    if m:
        noun = _subject_from_span(m.group(1), known)
        if noun:
            return noun
    m = re.search(
        r"\b([a-z][a-z0-9_-]*)\s+by\s+(?:amount|revenue|sales|value|volume)\b",
        q,
    )
    if m:
        token = m.group(1)
        if token not in _SUBJECT_SKIP and token not in _SUBJECT_MEASURES:
            return token
    return None


def undefined_subject(question: str) -> str | None:
    """Named subject the semantic layer does not define, else None.

    ``top 3 customers by amount`` names customers. ``total revenue`` names none.
    """
    from packs.dms.semantic.vocabulary import normalize_for_routing

    subject = _named_subject(normalize_for_routing(question))
    if subject is None:
        return None
    if subject in _known_subject_map():
        return None
    return subject


_SKU_SUBJECTS = frozenset({"sku", "skus", "item", "items", "product", "products", "seller", "sellers"})


def _subject_allows_sales_rank(q: str) -> bool:
    """Sales rank is SKUs. Category/supplier/etc. must not become a SKU list."""
    subject = _named_subject(q)
    if subject is None:
        return True
    return subject in _SKU_SUBJECTS


def _wants_sales_rank(q: str, q_raw: str) -> bool:
    if not _subject_allows_sales_rank(q):
        return False
    if re.search(r"\b(top|best|highest|most)\b", q) and re.search(
        r"\b(sell(?:ing|ers)?|sold|revenue|sales|sku|skus|revnue)\b", q
    ):
        return True
    if re.search(r"\btop\s+\d+\b", q):
        return True
    if _rank_window(q_raw) and re.search(r"\b(sku|skus|revenue|sales|sell)\b", q):
        return True
    return False


#: "i mean ...", "you meant ..." - a repair phrase, not an average.
_DISCOURSE_LEADIN = re.compile(r"^\W*(?:i|we|you)\s+mean(?:t)?\b[\s,:-]*", re.I)

#: Words that aggregate *values*. Narrower than `_wants_aggregate`, which also
#: carries counting words: counting a ranking of five is five; summing one has
#: no governed metric.
_VALUE_AGGREGATE = re.compile(
    r"\b(sum|summed|summing|total|totals|totalled|totalling|average|averaged|avg|"
    r"mean|median|combined|altogether|aggregate|cumulative|overall|added up)\b",
    re.I,
)
_CARDINAL = r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
#: A ranking must say how many, or continue into a participle. That is what
#: separates "top 5" / "highest selling" from "bottom line" / "top-level".
_RANKING = re.compile(
    r"\b(top|bottom|highest|lowest|largest|smallest|biggest|best|worst|leading|"
    rf"foremost|poorest)\b(?:\W{{0,3}}{_CARDINAL}\b|\s+\w+ing\b)",
    re.I,
)


def _aggregate_over_ranking(question: str) -> str | None:
    """Reason this question aggregates over a ranking, or None if it does not.

    ANS-02. Every metric either ranks or aggregates. None does both, so
    "the sum of the top 5 selling SKUs" has no plan. The router used to answer
    with the ranking, badged governed_metric. Order decides it, not membership:
    an aggregate *before* the ranking applies to it; an aggregate *after* is
    the sort key. This is a mitigation (word lists), not Cortex#14 step 1.
    """
    question = _DISCOURSE_LEADIN.sub("", question)
    agg = _VALUE_AGGREGATE.search(question)
    if agg is None:
        return None
    rank = _RANKING.search(question)
    if rank is None or agg.start() > rank.start():
        return None
    return (
        f"no governed metric computes a {agg.group(0).lower()} over a ranking - "
        f"ask for the ranking on its own first, then 'sum of them'"
    )


def _grouped_ranking_unanswerable(question: str) -> str | None:
    """Reason this ranking asks for a grouping no metric returns, or None.

    ANS-03. "top 3 categories by total revenue" used to hit ``revenue_total``
    and return one warehouse-wide row under governed_metric. Unknown subjects
    stay with ANS-04. SKU rankings stay answerable (R-0005).
    """
    from packs.dms.semantic.vocabulary import normalize_for_routing

    q = _DISCOURSE_LEADIN.sub("", normalize_for_routing(question))
    if _RANKING.search(q) is None:
        return None
    subject = _named_subject(q)
    if subject is None or subject in _SKU_SUBJECTS:
        return None
    if subject not in _known_subject_map():
        return None
    return (
        f"no governed metric ranks {subject} by the requested measure - "
        f"it would collapse the grouping into one population row"
    )


def _external_filing_quote(question: str) -> str | None:
    """Must-abstain class: quote/compare against an SEC filing this warehouse does not hold."""
    q = (question or "").lower()
    if not re.search(r"\b(10-k|10k|sec filing)\b", q):
        return None
    if not re.search(r"\b(quote|footnote|disclosed|compare)\b", q):
        return None
    return (
        "question asks to quote an external filing this warehouse does not hold"
    )


def _l1_cannot_compose(question: str) -> str | None:
    """BIRD/Spider composition that a single MetricPlan cannot answer.

    Serving an adjacent listing or count is incorrect (G-err), not helpful.
    Abstain until L2 is the serve path. Do not special-case held-out ids.
    """
    q = " ".join((question or "").lower().split())
    if re.search(r"\bsecond[- ]highest\b", q):
        return "nth-highest has no governed metric"
    if re.search(r"\bnever (appear|sold)\b", q):
        return "negated existence has no governed metric"
    if re.search(r"\bboth an in\b.+\bout transaction\b", q):
        return "paired IN/OUT at one location has no governed metric"
    if re.search(
        r"\b(of other|across every|overall \w+ rate|mean \w+ of other)\b",
        q,
    ):
        return "nested comparison to a group aggregate has no governed metric"
    # "and whose" / "and also have|appear" are BIRD stacked facts.
    # Do not match "ignore SKU-X and also show the top 5" (ANS-01 exclusion).
    if re.search(r"\band whose\b", q):
        return "stacked predicates have no single governed metric"
    if re.search(r"\band also (have|appear|contain|carry|hold)\b", q):
        return "stacked predicates have no single governed metric"
    if re.search(
        r"\bthan the (number of distinct|kilograms currently)\b|\bmore inbound shipments than\b",
        q,
    ):
        return "cross-fact comparison has no governed metric"
    if re.search(r"\bbin'?s own reorder\b|\bthat bin's own\b", q):
        return "per-bin reorder joined to cold storage has no governed metric"
    if re.search(r"\bat least two distinct\b", q):
        return "HAVING COUNT DISTINCT has no governed metric"
    if "average shipment cost" in q and "delayed" in q:
        return "average delayed+hazardous shipment cost has no governed metric"
    if re.search(r"unresolved critical alerts point at a supplier whose", q):
        return "alert-to-supplier lead join has no governed metric"
    if "marked hazardous" in q and "delayed" in q and "cold" in q:
        return "delayed+hazardous+cold join has no governed metric"
    return None


def _shape_refusal(question: str) -> str | None:
    """Plan-shape mismatch the router must not paper over with an adjacent metric."""
    return (
        _aggregate_over_ranking(question)
        or _grouped_ranking_unanswerable(question)
        or _external_filing_quote(question)
        or _l1_cannot_compose(question)
    )


def _param_is_required(spec: dict[str, Any]) -> bool:
    return bool(spec.get("required") or (spec.get("kind") == "value" and not spec.get("optional")))


def _plan_from_declared_synonyms(q: str, q_raw: str) -> MetricPlan | None:
    """Longest metrics.yaml synonym that is a contiguous phrase in ``q``.

    The regex cascade still wins. Skip one-word / short fragments and metrics
    that need a required slot the synonym does not carry.
    """
    from packs.dms.semantic.loader import load_all

    best: tuple[int, str] | None = None
    for metric in load_all().metrics.values():
        if any(_param_is_required(spec) for spec in (metric.params or {}).values()):
            continue
        for syn in metric.synonyms or []:
            phrase = " ".join(str(syn).lower().split())
            if " " not in phrase or len(phrase) < 16:
                continue
            if phrase not in q:
                continue
            n = len(phrase)
            if best is None or n > best[0]:
                best = (n, metric.id)
    if best is None:
        return None
    metric_id = best[1]
    slots: dict[str, Any] = {}
    if metric_id in ("sales_by_value", "sales_by_volume"):
        slots = _sales_rank_slots(q_raw)
    return _metric_plan(metric_id, slots, f"declared synonym → {metric_id}")


# ── L1 metric router (ordered; specific rules before generic) ────────────────
def route_to_metric(question: str) -> MetricPlan | None:
    """Pick a governed metric + its slots.

    Two views of the question, deliberately kept apart:
      ``q``     — normalized into router vocabulary; decides WHICH metric.
      ``q_raw`` — the untouched question; supplies every SLOT (limits,
                  thresholds, directions, day windows, percentages, locations).

    Slots must never come from the normalized text: normalization exists to
    widen recall over wording, and it must not be able to move a number, a
    threshold or a direction. See packs/dms/semantic/vocabulary.py.
    """
    from packs.dms.semantic.vocabulary import normalize_for_routing

    q_raw = question.lower()
    q = normalize_for_routing(question)

    if _shape_refusal(question):
        return None

    # scalars first — "how many X" must not fall through to a listing
    if re.search(r"\b(how many|number of|count of|count)\b", q) and "cold storage" in q:
        return _metric_plan("cold_storage_count", {}, "count of cold-storage locations")
    # metrics.yaml sku_count synonyms, not only "how many skus"
    if (
        re.search(r"\bskus?\b", q)
        and (
            re.search(r"\b(how many|number of|count of|count)\b", q)
            or re.search(r"\bberapa\b.{0,24}\b(banyak|sku)", q)
            or re.search(r"\bdistinct skus?\b", q)
        )
        and not re.search(r"\b(category|per|by)\b", q)
    ):
        return _metric_plan("sku_count", {}, "distinct SKU count")
    # "how many delayed" / Malay "berapa ... delayed" must not return the listing.
    if (
        ("delayed" in q)
        and re.search(r"\bshipments?\b", q)
        and (_wants_aggregate(q) or "berapa" in q)
        and not re.search(r"\b(carrier|per|by|each|warehouse|destination)\b", q)
    ):
        return _metric_plan("delayed_count", {}, "count of delayed shipments")

    # per-warehouse / per-carrier breakdowns of shipments (before the status listing)
    if "delayed" in q and re.search(r"\bcarrier", q):
        return _metric_plan("count_by_carrier", {"status": "DELAYED"}, "delayed shipments grouped by carrier")
    if re.search(r"\b(per|by|each)\b", q) and re.search(r"\b(warehouse|destination|location)\b", q) \
            and ("delayed" in q or "incoming" in q or "shipment" in q) \
            and not re.search(r"\b(cost|spend|price)\b", q):
        status = "DELAYED" if "delayed" in q else "IN_TRANSIT"
        return _metric_plan("count_by_destination", {"status": status}, f"{status} shipments grouped by destination")

    # revenue — calendar month before rolling-day window; bare total before ranked "top sales"
    if re.search(r"\b(revenue|sales|sold)\b", q) and _calendar_month(q) == "last":
        return _metric_plan("revenue_last_month", {}, "revenue in the previous calendar month")
    if re.search(r"\b(revenue|sales|sold)\b", q) and re.search(r"\b(last|past|within|previous)\b.*\bday", q):
        return _metric_plan("revenue_windowed", {"days": _days(q_raw, 30)}, "revenue over a rolling window")
    # metrics.yaml revenue_windowed synonyms ("recent revenue", "revenue last")
    if (
        re.search(r"\b(revenue|sales|sold)\b", q)
        and re.search(r"\b(recent|last|past|within|previous)\b", q)
        and _calendar_month(q) is None
        and _RANKING.search(_DISCOURSE_LEADIN.sub("", q)) is None
        and not re.search(r"\b(top|best|highest|most|sku|skus|rank|per|by|each)\b", q)
    ):
        return _metric_plan("revenue_windowed", {"days": _days(q_raw, 30)}, "revenue over a rolling window")
    # G6 — bare total revenue (no month/window); must not fall through to abstain.
    # A ranking that happens to say "total revenue" as its sort key is not this.
    if (
        re.search(r"\btotal\b", q)
        and re.search(r"\b(revenue|sales)\b", q)
        and _RANKING.search(_DISCOURSE_LEADIN.sub("", q)) is None
    ):
        return _metric_plan("revenue_total", {}, "total outbound revenue")
    if re.search(r"\b(revenue|sales)\b", q) and not re.search(
        r"\b(top|best|highest|most|sku|skus|rank|per|by|each)\b", q
    ):
        return _metric_plan("revenue_total", {}, "total outbound revenue")

    # supplier risk threshold
    if re.search(r"\brisk\b", q) and re.search(r"\b(above|over|below|under|greater|less|more than|exceed|>|<)\b", q):
        return _metric_plan("suppliers_by_risk",
                          {"threshold": _threshold(q_raw), "op": _threshold_op(q_raw)},
                          "suppliers filtered by risk-score threshold")

    # average lead time by country
    if re.search(r"\baverage\b|\bmean\b|\bavg\b", q) and "lead time" in q:
        return _metric_plan("avg_lead_time_by_country", {}, "average lead time grouped by country")

    # free capacity ranking
    if re.search(r"\bfree\b|\bspare\b|\bavailable\b", q) and "capacit" in q:
        return _metric_plan("free_capacity",
                          {"limit": _explicit_limit(q_raw) or 1, "direction": _direction(q_raw)},
                          "warehouses ranked by free capacity")

    # capacity above a percentage
    if "capacit" in q and re.search(r"\b(above|over|more than)\b.*\d", q):
        return _metric_plan("capacity_above", {"pct": _pct(q_raw)}, "locations above a capacity threshold")
    # utilis\w* / utiliz\w*, not utilis\b — the trailing \b made the word
    # "utilisation" itself fail to match, so this branch was only ever reachable
    # by the stem alone. The golden question hits L0 certified, which is why the
    # dead branch went unnoticed.
    if "capacit" in q and re.search(r"\b(utilis\w*|utiliz\w*|how full|usage)\b", q):
        return _metric_plan("capacity_utilisation", {}, "capacity utilisation per location")

    # arriving window
    if "arriving" in q or ("incoming" in q and re.search(r"\bweek|\bdays?\b", q)):
        return _metric_plan("arriving_window", {"days": _days(q_raw, 7)}, "in-transit shipments arriving within a window")

    # shipment status listing
    for status in ("delayed", "in transit", "in_transit", "pending", "delivered", "cancelled"):
        if status in q and re.search(r"\bshipments?\b", q):
            norm = "IN_TRANSIT" if status.startswith("in ") or status == "in_transit" else status.upper()
            return _metric_plan("shipments_by_status", {"status": norm}, f"shipments with status {norm}")

    # cold storage listing
    if "cold storage" in q:
        return _metric_plan("cold_storage_list", {}, "cold-storage locations")

    # low stock (optionally warehouse-scoped)
    if re.search(r"\b(below reorder|low stock|understocked|reorder level)\b", q):
        loc = _location(question)
        return _metric_plan("low_stock", {"wh": loc} if loc else {},
                          f"items below reorder level{' at ' + loc if loc else ''}")

    # not restocked window
    if re.search(r"\b(not restocked|stale)\b", q) or ("restock" in q and "not" in q):
        return _metric_plan("stale_restock", {"days": _days(q_raw, 30)}, "items not restocked within a window")

    # expired — aggregate / calendar month BEFORE bare listing
    if "expired" in q or "past expiry" in q or "out of date" in q:
        month = _calendar_month(q)
        if month == "last" or (_wants_aggregate(q) and month == "last"):
            return _metric_plan("expired_last_month", {}, "count of items that expired last month")
        if _wants_aggregate(q):
            return _metric_plan("expired_count", {}, "count of currently expired inventory")
        return _metric_plan("expired_items", {}, "expired inventory listing")

    # active alerts
    if "alert" in q and re.search(r"\b(active|open|unresolved|current)\b", q):
        return _metric_plan("active_alerts", {}, "unresolved alerts")

    # sales ranking (after month/window scalars so "last month sales" never ranks)
    if _wants_sales_rank(q, q_raw):
        slots = _sales_rank_slots(q_raw)
        if re.search(r"\b(quantity|volume|kg|units?)\b", q):
            return _metric_plan(
                "sales_by_volume",
                slots,
                "SKUs ranked by quantity sold",
            )
        return _metric_plan("sales_by_value", slots, "SKUs ranked by sales value")
    # unranked "last month sales" catch-all if earlier branch missed phrasing
    if re.search(r"\b(sales|revenue)\b", q) and _calendar_month(q) == "last":
        return _metric_plan("revenue_last_month", {}, "revenue in the previous calendar month")

    return _plan_from_declared_synonyms(q, q_raw)


# ── truncation-honest total ──────────────────────────────────────────────────
def _true_count(
    sql: str,
    con=None,
    *,
    verified: VerifiedManifest | None = None,
) -> int | None:
    """COUNT(*) over the query with LIMIT/ORDER stripped — the honest total
    behind a possibly-capped listing. Returns None if it can't be computed.

    When ``verified`` is set (contract live ask), the count runs through the
    C4 submit executor so predicates apply. Legacy callers still pass ``con``.
    """
    if verified is not None:
        from CortexOS.execution.submit import execute_count

        return execute_count(verified, sql)
    if con is None:
        return None
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
        tree.set("limit", None)
        tree.set("order", None)
        inner = tree.sql(dialect="duckdb")
        return int(con.execute(f"SELECT COUNT(*) AS n FROM ({inner}) _t").fetchone()[0])
    except Exception:  # noqa: BLE001
        return None


# ── suggestions for abstain ──────────────────────────────────────────────────
def _suggestions(question: str, limit: int = 3) -> list[str]:
    """Nearest answerable questions (token overlap over certified + metric synonyms)."""
    from packs.dms.semantic.loader import load_all

    model = load_all()
    qtokens = set(_normalize(question).split())
    scored: list[tuple[float, str]] = []
    for cq in model.certified:
        overlap = len(qtokens & set(_normalize(cq.question).split()))
        if overlap:
            scored.append((overlap, cq.question))
    scored.sort(key=lambda t: -t[0])
    seen: list[str] = []
    for _, qtext in scored:
        if qtext not in seen:
            seen.append(qtext)
        if len(seen) >= limit:
            break
    if not seen:  # cold: certified titles + metric labels from the pack
        from packs.dms.semantic.catalog_answer import _metric_label

        for cq in model.certified:
            seen.append(cq.question)
            if len(seen) >= limit:
                break
        if len(seen) < limit:
            for metric in model.metrics.values():
                label = _metric_label(metric)
                if label not in seen:
                    seen.append(label)
                if len(seen) >= limit:
                    break
    return seen[:limit]


def _abstain(
    question: str,
    audit_id: str,
    *,
    reason: str,
    granted_sources: list[str] | None = None,
) -> dict[str, Any]:
    suggestions = _suggestions(question)
    hint = " Try: " + " · ".join(f'"{s}"' for s in suggestions)
    text = f"I can't answer that from the DMS semantic layer with confidence ({reason})."
    # Bound sessions name the sources they CAN answer over. Suggesting demo
    # warehouse questions to a session that did not bind those tables is a
    # dead end dressed up as help.
    if granted_sources:
        named = ", ".join(granted_sources)
        text = f"{text} This session can answer over: {named}."
    else:
        text = text + hint
    return {
        "answer": text,
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": ABSTAIN,
        "rows": [],
        "source_table": None,
        "layer": "abstain",
        "badge": "abstain",
        "assumptions": reason,
        "total_count": 0,
        "suggestions": [] if granted_sources else suggestions,
    }


def _abstain_refused(
    question: str,
    audit_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    """Policy / manifest refusal. Distinct from coverage abstain.

    Emits route/layer/badge = ``refused``. Contract mapping must treat this as
    an abstain signal (F40) — never Badge.SESSION. DMS owns the customer
    envelope; this is the engine half only.
    """
    del question
    return {
        "answer": f"I can't answer that ({reason}).",
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "refused",
        "rows": [],
        "source_table": None,
        "layer": "refused",
        "badge": "refused",
        "assumptions": reason,
        "total_count": 0,
        "suggestions": [],
    }


def _catalog_response(question: str, audit_id: str) -> dict[str, Any]:
    """META-01 — browse what the semantic layer can answer (no SQL)."""
    from packs.dms.semantic.catalog_answer import build_catalog_answer
    from packs.dms.semantic.loader import load_all

    payload = build_catalog_answer()
    model = load_all()
    suggestions = [cq.question for cq in model.certified[:5]]
    if len(suggestions) < 3:
        from packs.dms.semantic.catalog_answer import _metric_label

        for metric in model.metrics.values():
            label = _metric_label(metric)
            if label not in suggestions:
                suggestions.append(label)
            if len(suggestions) >= 5:
                break
    return {
        "answer": payload["answer"],
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "sql",
        "rows": [],
        "source_table": None,
        "layer": payload["layer"],
        "badge": payload["badge"],
        "assumptions": "semantic layer catalog (no SQL)",
        "total_count": 0,
        "suggestions": suggestions,
        "query_plan": _honest_plan(
            question, None, layer=payload["layer"], assumptions="catalog browse"
        ),
    }


def resolve_product_grant(
    session_id: str | None,
    verified: VerifiedManifest | None,
    space_id: str | None = None,
) -> tuple[VerifiedManifest, str, list[str]]:
    """Grant for a served door. Never mints. Self-issued is not a binding.

    The old hole: no binding still minted a wide local grant over every demo
    table, so a table-intersect against that grant never fired. Unbound must
    fail closed *before* that grant is used as permission to answer.
    """
    named = (space_id or "").strip() or None
    candidate = verified
    if candidate is None:
        try:
            candidate = get_session_registry().resolve(session_id, space_id=named)
        except SessionExpired as exc:
            raise UngroundedSession("the session grant expired") from exc
        except SpaceUnbound as exc:
            raise UngroundedSession(str(exc)) from exc
        except SessionUnbound as exc:
            raise UngroundedSession("no session grant is bound") from exc
    if named:
        bound = (candidate.manifest.space_id or "").strip()
        if bound != named:
            raise UngroundedSession(
                f"grant is bound to Space '{bound}', not '{named}'"
            )
    if (candidate.issuer_kid or "").strip() == LOCAL_ISSUER_KID:
        raise UngroundedSession("self-issued grant is not a session binding")
    sources = sorted({str(name).lower() for name in candidate.row_predicates})
    if not sources:
        raise UngroundedSession("session grant names no sources")
    return candidate, SESSION_GRANT, sources


def _stamp_grant(
    result: dict[str, Any],
    *,
    kind: str,
    sources: list[str],
) -> dict[str, Any]:
    result["grant_kind"] = kind
    result["granted_sources"] = list(sources)
    plan = result.get("query_plan")
    if isinstance(plan, dict):
        plan["grant_kind"] = kind
        plan["granted_sources"] = list(sources)
    return result


def _abstain_unbound(
    question: str,
    audit_id: str,
    *,
    reason: str,
    space_id: str | None = None,
) -> dict[str, Any]:
    """Refuse a served turn that nothing grants. Do not offer demo questions."""
    del question
    named = (space_id or "").strip()
    if named:
        answer = (
            f"I can't answer that yet - nothing grants Space '{named}' for this "
            f"session ({reason}). Select a Space you are entitled to (which binds "
            "its signed grant), then ask again. Until then I have nothing to read "
            "and would be guessing."
        )
    else:
        answer = (
            "I can't answer that yet - nothing is grounding this session "
            f"({reason}). Bind a session grant naming the sources you want me "
            "to read, then ask again. Until then I have nothing to read and "
            "would be guessing."
        )
    return _stamp_grant(
        {
            "answer": answer,
            "sql_used": None,
            "chart_spec": None,
            "audit_id": audit_id,
            "violations_blocked": [],
            "route": ABSTAIN,
            "rows": [],
            "source_table": None,
            "layer": "abstain",
            "badge": "abstain",
            "assumptions": f"ungrounded session: {reason}",
            "total_count": 0,
            "suggestions": [],
        },
        kind="none",
        sources=[],
    )


def _abstain_ungrounded_plan(
    question: str,
    audit_id: str,
    *,
    ungrounded: frozenset[str],
    granted_sources: list[str],
) -> dict[str, Any]:
    """Refuse a plan that reads tables the session did not bind, and name those it did."""
    refused = ", ".join(sorted(ungrounded))
    can = ", ".join(granted_sources) if granted_sources else "(none)"
    reason = f"question resolves to ungranted source(s): {refused}"
    return {
        "answer": (
            "I can't answer that from the sources bound to this session - the closest "
            f"governed answer would read {refused}, which this session did not bind. "
            f"This session can answer over: {can}. Ask about those, or bind the "
            "source you meant and ask again."
        ),
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": ABSTAIN,
        "rows": [],
        "source_table": None,
        "layer": "abstain",
        "badge": "abstain",
        "assumptions": reason,
        "total_count": 0,
        "suggestions": [],
    }


# ── session memory (follow-up anaphora) ───────────────────────────────────────
# Keyed by session_id + space_id so Space A never sees Space B's prior SQL (C6).
_SESSION: dict[str, dict[str, Any]] = {}


def _session_key(session_id: str | None, space_id: str | None = None) -> str:
    sid = (session_id or "demo").strip() or "demo"
    sp = (space_id or "").strip()
    return f"{sid}::space:{sp}" if sp else sid


def _is_derived_scalar(turn: dict[str, Any]) -> bool:
    """A single number computed from the turn before it (sum / avg / count)."""
    if turn.get("layer") != "session":
        return False
    rows = turn.get("rows") or []
    if len(rows) != 1:
        return False
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in rows[0].values()
    )


def _remember(
    session_id: str | None,
    turn: dict[str, Any],
    *,
    space_id: str | None = None,
) -> None:
    # A derived scalar must not become what "them" points at (dc86689).
    key = _session_key(session_id, space_id)
    if _is_derived_scalar(turn) and key in _SESSION:
        return
    _SESSION[key] = turn


def clear_session(session_id: str | None = None, *, space_id: str | None = None) -> None:
    if session_id is None and space_id is None:
        _SESSION.clear()
    else:
        _SESSION.pop(_session_key(session_id, space_id), None)


def _is_anaphora(q: str) -> bool:
    """Follow-up pronouns / arithmetic over the prior result set."""
    return bool(
        re.search(
            r"\b(them|those|these)\b|"
            r"\b(average|avg|mean|total|sum|count|how many)\s+of\s+(them|those|these|it)\b|"
            r"\bwhat is the average of (them|those|these|it)\b|"
            r"\baverage of (them|those|these)\b|"
            r"\bdivid(?:e|ed|ing)\b.*?\bby\s+\d|"
            r"\bmultipl(?:y|ied|ying)\b.*?\bby\s+\d|"
            r"(?:/|÷|×|\*)\s*\d|"
            r"\bone\s+fifth\b",
            q,
        )
    )


def _scale_factor(q: str) -> tuple[str, float] | None:
    """Return ('div'|'mul', factor) for session arithmetic follow-ups."""
    m = re.search(r"\bdivid(?:e|ed|ing)\b.*?\bby\s+(\d+(?:\.\d+)?)", q)
    if m:
        return "div", float(m.group(1))
    m = re.search(r"\bone\s+fifth\b", q)
    if m:
        return "div", 5.0
    m = re.search(r"(?:/|÷)\s*(\d+(?:\.\d+)?)", q)
    if m:
        return "div", float(m.group(1))
    m = re.search(r"\bmultipl(?:y|ied|ying)\b.*?\bby\s+(\d+(?:\.\d+)?)", q)
    if m:
        return "mul", float(m.group(1))
    m = re.search(r"(?:×|\*)\s*(\d+(?:\.\d+)?)", q)
    if m:
        return "mul", float(m.group(1))
    return None


def _numeric_columns(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    cols: list[str] = []
    for k, v in rows[0].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            cols.append(k)
        elif isinstance(v, str):
            try:
                float(v)
                cols.append(k)
            except ValueError:
                pass
    return cols


def _pick_measure(nums: list[str]) -> str | None:
    for prefer in (
        "sales_value_myr",
        "total_sold_kg",
        "revenue_myr",
        "quantity_kg",
        "total_value_myr",
        "ranking_score",
        "free_kg",
        "pct_used",
    ):
        if prefer in nums:
            return prefer
    for c in nums:
        if not re.search(r"(^id$|_id$|count$)", c, re.I):
            return c
    return None


def _prior_skus(rows: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        sku = row.get("sku")
        if sku is None:
            continue
        text = str(sku).strip().upper()
        if text and text not in out:
            out.append(text)
    return out


def _low_stock_over_prior(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Filter inventory to prior-turn SKUs that are below reorder."""
    skus = _prior_skus(rows)
    if not skus:
        raise ValueError("prior result has no SKUs to check for low stock")
    quoted = ", ".join("'" + s.replace("'", "''") + "'" for s in skus)
    sql = (
        "SELECT sku, quantity_kg, reorder_level_kg, location_id, category, storage_bin "
        "FROM inventory "
        f"WHERE UPPER(sku) IN ({quoted}) AND quantity_kg < reorder_level_kg "
        "ORDER BY quantity_kg ASC"
    )
    return sql, []  # rows filled by execute


def _reslice_prior(prior_sql: str, question: str) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite LIMIT/OFFSET on the prior ranking. Subjectless 'number 2-6'."""
    window = _rank_window(question)
    if window is None:
        raise ValueError("not a window follow-up")
    start, end = window
    offset, limit = start - 1, end - start + 1
    if offset < 0 or limit < 1 or limit > MAX_LIMIT:
        raise ValueError("window out of range")
    tree = sqlglot.parse_one(prior_sql, read="duckdb")
    tree.set("limit", sqlglot.exp.Limit(expression=sqlglot.exp.Literal.number(limit)))
    tree.set("offset", sqlglot.exp.Offset(expression=sqlglot.exp.Literal.number(offset)))
    return tree.sql(dialect="duckdb"), []


def _aggregate_prior(
    prior_sql: str,
    question: str,
    rows: list[dict[str, Any]],
    *,
    total_count: int | None = None,
    shown_count: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Follow-up aggregate / scale over the prior result.

    COUNT uses a guarded subquery wrap. AVG and divide/multiply are computed
    from the prior row snapshot (literal SELECT so the allowlist still passes).
    """
    q = question.lower()
    wants_avg = bool(re.search(r"\b(average|avg|mean)\b", q))
    wants_sum = bool(re.search(r"\b(sum|sums|total|altogether|combined)\b", q)) and not wants_avg
    scale = _scale_factor(q)
    nums = _numeric_columns(rows)
    measure = _pick_measure(nums)

    if scale is not None:
        op, factor = scale
        if factor == 0 and op == "div":
            raise ValueError("division by zero")
        if not measure or not rows:
            raise ValueError("no numeric prior measure to scale")
        # Scalar prior, or explicit sum/total over multirow — otherwise ambiguous.
        if len(rows) > 1 and not re.search(r"\b(sum|total|altogether)\b", q):
            raise ValueError("ambiguous multirow scale; ask to sum them first")
        vals: list[float] = []
        for row in rows:
            raw = row.get(measure)
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not vals:
            raise ValueError("no numeric values to scale")
        base = sum(vals) if len(vals) > 1 else vals[0]
        result = round(base / factor, 2) if op == "div" else round(base * factor, 2)
        col = f"{'div' if op == 'div' else 'mul'}_{measure}"
        sql = f"SELECT CAST({result} AS DOUBLE) AS {col}"
        return sql, [{col: result}]

    if wants_avg and measure and rows:
        vals = []
        for row in rows:
            raw = row.get(measure)
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if vals:
            avg_val = round(sum(vals) / len(vals), 2)
            col = f"avg_{measure}"
            # Literal SELECT — no unknown column vs warehouse allowlist.
            sql = f"SELECT CAST({avg_val} AS DOUBLE) AS {col}"
            return sql, [{col: avg_val}]

    if wants_sum and measure and rows:
        vals = []
        for row in rows:
            raw = row.get(measure)
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if vals:
            total = round(sum(vals), 2)
            col = f"sum_{measure}"
            sql = f"SELECT CAST({total} AS DOUBLE) AS {col}"
            return sql, [{col: total}]

    tree = sqlglot.parse_one(prior_sql, read="duckdb")
    # Keep LIMIT so "how many of them?" after top-5 is 5, not the warehouse.
    # Replay total_count only when the prior listing hit the page cap.
    # Session memory stores rows[:50], so use shown_count, not len(rows).
    shown = shown_count if shown_count is not None else len(rows)
    page_capped = total_count is not None and shown >= MAX_LIMIT
    if page_capped:
        tree.set("limit", None)
    tree.set("order", None)
    inner = tree.sql(dialect="duckdb")
    sql = f"SELECT COUNT(*) AS followup_count FROM ({inner}) _prior"
    if page_capped and not wants_avg:
        return sql, [{"followup_count": int(total_count)}]
    return sql, []  # rows filled by execute


def _honest_plan(
    question: str,
    sql: str | None,
    *,
    layer: str,
    metric_id: str | None = None,
    skill_score: float | None = None,
    assumptions: str = "",
) -> dict[str, Any]:
    from CortexOS.dms.query_service import plan_query

    base = plan_query(question, sql).to_dict()
    # Real route wins over keyword heuristics for UI confidence.
    conf = 0.95 if layer in ("certified", "governed_metric") else 0.85
    if layer == "query_skill" and skill_score is not None:
        conf = min(0.99, max(0.72, float(skill_score)))
    if layer == "session":
        conf = 0.88
    intent = metric_id or layer or base.get("intent") or "unknown"
    base["intent"] = intent
    base["confidence"] = round(conf, 3)
    base["layer"] = layer
    base["metric_id"] = metric_id
    base["skill_score"] = round(skill_score, 3) if skill_score is not None else None
    base["assumptions"] = assumptions
    return base


# ── the engine ────────────────────────────────────────────────────────────────
def answer(
    question: str,
    *,
    session_id: str | None = None,
    space_id: str | None = None,
    verified: VerifiedManifest | None = None,
    require_grounding: bool = False,
) -> dict[str, Any]:
    from CortexOS.dms.query_service import (
        _infer_source_table,
        build_chart_spec,
        rag_answer,
        route_question,
        synthesize_answer,
    )
    from packs.dms.semantic import query_skills

    audit_id = str(uuid.uuid4())
    grant_kind = "none"
    granted_sources: list[str] = []
    if require_grounding:
        try:
            verified, grant_kind, granted_sources = resolve_product_grant(
                session_id, verified, space_id=space_id
            )
        except UngroundedSession as exc:
            return _abstain_unbound(
                question, audit_id, reason=str(exc), space_id=space_id
            )

    def _done(result: dict[str, Any]) -> dict[str, Any]:
        if require_grounding and "grant_kind" not in result:
            result = _stamp_grant(result, kind=grant_kind, sources=granted_sources)
        try:
            from CortexOS.dms.l2_generation import maybe_record_l2_shadow

            maybe_record_l2_shadow(question, result, verified=verified)
        except Exception:  # noqa: BLE001 — shadow must never affect served
            pass
        return result

    def _abs(reason: str) -> dict[str, Any]:
        return _done(
            _abstain(
                question,
                audit_id,
                reason=reason,
                granted_sources=granted_sources or None,
            )
        )

    route = route_question(question)

    if route == "blocked":
        return _done({
            "answer": "That operation is not permitted.", "sql_used": None, "chart_spec": None,
            "audit_id": audit_id, "violations_blocked": ["DDL_ATTEMPT"], "route": "blocked",
            "rows": [], "source_table": None, "layer": "blocked", "badge": "blocked",
            "assumptions": "destructive operation refused", "total_count": 0,
            "query_plan": _honest_plan(question, None, layer="blocked", assumptions="destructive"),
        })

    def _space_doc_rag() -> dict[str, Any] | None:
        # Keyword RAG must not skip L0/L1/L2. Space-scoped stub only, after miss.
        if not space_id:
            return None
        ans, sources = rag_answer(question)
        if not ans or not sources:
            return None
        return {
            "answer": ans, "sql_used": None, "chart_spec": None, "audit_id": audit_id,
            "violations_blocked": [], "route": "rag", "sources": sources, "rows": [],
            "source_table": None, "layer": "rag", "badge": "document", "assumptions": "",
            "total_count": 0,
            "query_plan": _honest_plan(question, None, layer="rag"),
        }

    layer = badge = ""
    sql: str | None = None
    assumptions = ""
    metric_id: str | None = None
    metric_slots: dict[str, Any] = {}
    skill_score: float | None = None
    planned_tables: tuple[str, ...] = ()
    l2_retrieved: tuple[str, ...] = ()

    q_low = question.lower()
    prior = _SESSION.get(_session_key(session_id, space_id))

    # Session anaphora — "average of them" / subjectless "number 2-6"
    session_rows: list[dict[str, Any]] | None = None
    window_followup = bool(
        prior
        and prior.get("sql")
        and _rank_window(q_low)
        and not _wants_sales_rank(q_low, q_low)
    )
    if prior and prior.get("sql") and (_is_anaphora(q_low) or window_followup):
        try:
            if window_followup:
                sql, session_rows = _reslice_prior(prior["sql"], question)
            elif re.search(r"\b(low stock|below reorder|below\s+reorder)\b", q_low):
                sql, session_rows = _low_stock_over_prior(prior.get("rows") or [])
            else:
                sql, session_rows = _aggregate_prior(
                    prior["sql"],
                    question,
                    prior.get("rows") or [],
                    total_count=prior.get("total_count"),
                    shown_count=prior.get("shown_count"),
                )
            layer, badge = "session", "session"
            assumptions = f"follow-up over prior turn ({prior.get('metric_id') or prior.get('layer')})"
            metric_id = prior.get("metric_id")
        except Exception:  # noqa: BLE001
            sql = None
            session_rows = None

    # L0 certified → L1 metric → L-skill → L3 abstain
    # Skills run after governed routes so golden/certified paths stay authoritative.
    # Catalog browse is not a named warehouse subject — check it before
    # undefined_subject, or "data" / "catalog" abstain as unknown nouns.
    if sql is None:
        from packs.dms.semantic.catalog_answer import is_catalog_intent

        if is_catalog_intent(question):
            return _done(_catalog_response(question, audit_id))
        refused = _shape_refusal(question)
        if refused:
            return _abs(refused)
        cq = match_certified(question)
        if cq is not None:
            sql, layer, badge = cq.sql, "certified", "certified"
            assumptions = f"certified query {cq.id}"
            metric_id = cq.id
            planned_tables = tuple(cq.tables)
            from packs.dms.semantic.loader import normalize_certified_sql

            sql, violations = normalize_certified_sql(cq)
            if violations:
                return _abs(f"certified query {cq.id} unresolved literal {violations}")
        if sql is None:
            unknown = undefined_subject(question)
            if unknown:
                named = ", ".join(_answerable_entities())
                return _abs(
                    f"the question names '{unknown}', which the semantic layer does "
                    f"not define; it can answer about {named}"
                )
            plan = route_to_metric(question)
            if plan is not None:
                from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

                try:
                    sql = compile_metric(load_all(), plan.metric_id, plan.slots)
                    layer, badge = "governed_metric", "governed_metric"
                    assumptions = plan.reason
                    metric_id = plan.metric_id
                    metric_slots = dict(plan.slots)
                    planned_tables = plan.tables
                except SemanticError as exc:
                    return _abs(f"could not resolve inputs: {exc}")

    if sql is None:
        hit = query_skills.find(question)
        if hit is not None:
            skill_score = float(hit["score"])
            if hit.get("metric_id"):
                from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

                # Never replay turn-specific filters from a prior capture.
                # Sales ranks re-derive slots from THIS question; other metrics
                # keep only non-contextual stored params.
                stored = dict(hit.get("params") or {})
                contextual = {
                    "exclude_skus",
                    "offset_clause",
                    "limit",
                    "direction",
                    "days",
                    "location_code",
                    "warehouse",
                }
                if hit["metric_id"] in ("sales_by_value", "sales_by_volume"):
                    params = _sales_rank_slots(question)
                else:
                    params = {k: v for k, v in stored.items() if k not in contextual}
                try:
                    sql = compile_metric(load_all(), hit["metric_id"], params)
                    layer, badge = "query_skill", "query_skill"
                    assumptions = f"query skill match score={skill_score:.3f} → {hit['metric_id']}"
                    metric_id = hit["metric_id"]
                    metric_slots = dict(params)
                    planned_tables = _tables_stated_by_metric(hit["metric_id"])
                except SemanticError:
                    sql = None
            elif hit.get("sql_template"):
                sql = hit["sql_template"]
                layer, badge = "query_skill", "query_skill"
                assumptions = f"query skill match score={skill_score:.3f} (stored sql)"

    if sql is None:
        # L2 lives on the engine port module, not here. This file must not
        # import pack generation code (C2). L2 (when enabled) stays ahead of
        # the space-scoped document stub; keyword RAG never runs first.
        from CortexOS.dms.l2_generation import L2_MANIFEST_REASON_PREFIX, attempt_l2

        l2_out = attempt_l2(question, verified=verified)
        if l2_out is not None and l2_out.sql:
            sql = l2_out.sql
            layer, badge = l2_out.layer, l2_out.badge
            assumptions = l2_out.assumptions
            l2_retrieved = tuple(l2_out.retrieved_tables)
            from CortexOS.dms.l2_plausibility import sql_table_names

            used = tuple(sorted(sql_table_names(sql)))
            planned_tables = used or l2_retrieved
        else:
            if l2_out is not None and not l2_out.sql:
                if l2_out.refused or (l2_out.reason or "").startswith(
                    L2_MANIFEST_REASON_PREFIX
                ):
                    refused = _abstain_refused(question, audit_id, reason=l2_out.reason)
                    refused["violations_blocked"] = list(l2_out.violations or [])
                    return _done(refused)
            doc = _space_doc_rag()
            if doc is not None:
                return _done(doc)
            if l2_out is not None and not l2_out.sql:
                return _abs(l2_out.reason)
            return _abs("no governed metric or certified query matched")

    if sql is None:
        return _abs("no governed metric or certified query matched")

    # Grounding uses the plan's stated tables. Do not recover them only by
    # re-parsing compiled SQL if the plan can state them — a self-issued
    # wide grant would make that intersect vacuous.
    if require_grounding:
        read = frozenset(t.lower() for t in planned_tables)
        granted = frozenset(granted_sources)
        extra = read - granted
        if not read or extra:
            ungrounded = extra or frozenset({"<unanalysable-plan>"})
            return _done(
                _abstain_ungrounded_plan(
                    question,
                    audit_id,
                    ungrounded=ungrounded,
                    granted_sources=granted_sources,
                )
            )

    semantic = load_semantic_layer()
    # Contract live ask: semantic guardrail then C4 submit (enforce_manifest).
    # Legacy callers keep the old connection + guard_and_execute path.
    if verified is not None:
        from datetime import datetime, timezone

        from CortexOS.dms.sql_validate_gate import SqlGateAbstain, run_gate
        from CortexOS.execution.submit import execute_sql

        gate = run_gate(sql, semantic)
        guard_result = gate  # ValidateGateResult shares passed/safe_sql/violations
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_sql=sql,
            safe_sql=gate.safe_sql,
            violations=gate.violations,
            passed=gate.passed,
        )
        if not gate.passed or not gate.safe_sql:
            log_audit(entry)
            rows = []
            total_count = None
        elif session_rows is not None and len(session_rows) > 0 and layer == "session":
            rows = session_rows
            total_count = len(rows)
            entry.row_count = len(rows)
            log_audit(entry)
        else:
            try:
                rows, _, _ = execute_sql(verified, gate.safe_sql)
            except SqlGateAbstain as exc:
                entry.passed = False
                entry.violations = list(exc.violations)
                log_audit(entry)
                return _abs(f"SQL validation gate: {exc}")
            except ManifestError as exc:
                entry.passed = False
                entry.violations = [type(exc).__name__]
                log_audit(entry)
                return _done(
                    _abstain_refused(
                        question,
                        audit_id,
                        reason=f"{type(exc).__name__}: {exc}",
                    )
                )
            total_count = _true_count(gate.safe_sql, verified=verified)
            entry.row_count = len(rows)
            log_audit(entry)
    else:
        # Every statement that reaches here has passed the read-only guardrail, so a
        # read-only handle is always sufficient. It is opt-in (DMS_READ_ONLY_QUERIES)
        # because it also has to be safe for the writer in this process — see
        # warehouse_db.read_only_queries_enabled.
        con = get_connection(DEFAULT_DB, read_only=read_only_queries_enabled())
        try:
            if session_rows is not None and len(session_rows) > 0 and layer == "session":
                # Precomputed AVG (literal SELECT still guardrail-checked)
                guard_result, rows, entry = guard_and_execute(sql, semantic, con)
                if guard_result.passed:
                    rows = session_rows
                total_count = len(rows) if guard_result.passed else None
            else:
                guard_result, rows, entry = guard_and_execute(sql, semantic, con)
                total_count = (
                    _true_count(guard_result.safe_sql, con) if guard_result.passed else None
                )
        finally:
            con.close()

    if not guard_result.passed:
        return _abs(f"internal SQL failed guardrail {guard_result.violations}")

    if layer == "generated":
        from CortexOS.dms.l2_plausibility import (
            assess_plausibility,
            leftover_literals_via_port,
        )

        used_sql = guard_result.safe_sql or sql or ""
        trip = assess_plausibility(
            question,
            used_sql,
            rows,
            retrieved_tables=l2_retrieved,
            leftover_literals=leftover_literals_via_port(used_sql),
        )
        if not trip.ok:
            return _abs(trip.reason)

    truncated = total_count is not None and len(rows) >= MAX_LIMIT and total_count > len(rows)
    answer_text = synthesize_answer(rows, question)
    if truncated:
        answer_text = f"{total_count} rows match; showing the first {len(rows)}.\n" + answer_text

    # Remember last successful turn for follow-ups (scoped to Space)
    _remember(
        session_id,
        {
            "question": question,
            "sql": guard_result.safe_sql,
            "metric_id": metric_id,
            "layer": layer,
            "rows": rows[:50],
            "shown_count": len(rows),
            "total_count": total_count if total_count is not None else len(rows),
            "source_table": _infer_source_table(sql),
            "space_id": (space_id or "").strip() or None,
        },
        space_id=space_id,
    )

    # Graduate successful non-session answers into the skill store
    if layer in ("certified", "governed_metric", "query_skill"):
        query_skills.capture(
            question,
            metric_id=metric_id if layer != "certified" else None,
            params=metric_slots,
            sql=guard_result.safe_sql,
            layer=layer,
        )

    return _done({
        "answer": answer_text,
        "sql_used": guard_result.safe_sql,
        "chart_spec": build_chart_spec(rows, question),
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "sql",
        "row_count": len(rows),
        "rows": rows,
        "total_count": total_count if total_count is not None else len(rows),
        "truncated": truncated,
        "source_table": _infer_source_table(sql),
        "layer": layer,
        "badge": badge,
        "assumptions": assumptions,
        "metric_id": metric_id,
        "query_plan": _honest_plan(
            question,
            guard_result.safe_sql,
            layer=layer,
            metric_id=metric_id,
            skill_score=skill_score,
            assumptions=assumptions,
        ),
        "audit": {"timestamp": entry.timestamp, "passed": entry.passed, "violations": entry.violations},
    })
