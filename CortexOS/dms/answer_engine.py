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

import os
import re
import statistics
import uuid
from dataclasses import dataclass
from typing import Any

import sqlglot

from CortexOS.dms import sql_plausibility
from CortexOS.dms.semantic_port import (
    SemanticCompileError,
    resolve_semantic_layer,
)
from CortexOS.dms.sql_guardrail import (
    MAX_LIMIT,
    AuditEntry,
    log_audit,
)
from CortexOS.dms.warehouse_db import (
    DEFAULT_DB,
    get_connection,
    load_semantic_layer,
)
from CortexOS.execution.manifest import (
    ManifestError,
    PathNotAllowed,
    VerifiedManifest,
    local_manifest,
    tables_read,
)
from CortexOS.execution.session_manifests import (
    SessionExpired,
    SessionUnbound,
    get_session_registry,
)

# Reused from the existing service (loaded lazily to avoid import cycle at module load).
ABSTAIN = "needs_clarification"

# Which authority a turn ran under. A signed session manifest is a stronger
# claim than a grant this process minted for itself, and the difference has to
# be legible on the envelope, not inferrable from a log line (R-0011).
SESSION_GRANT = "session"
LOCAL_GRANT = "local-self-issued"


@dataclass(slots=True)
class MetricPlan:
    metric_id: str
    slots: dict[str, Any]
    reason: str


# ── normalization + certified index ──────────────────────────────────────────
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _certified_index() -> dict[str, Any]:
    model = resolve_semantic_layer().load_model()
    # Exact normalized match on primary question + curated synonyms (VQ-01).
    # Never fuzzy — a scoped question must not collide with an unscoped asset.
    index: dict[str, Any] = {}
    for cq in model.certified:
        keys = [_normalize(cq.question), *(_normalize(s) for s in (cq.synonyms or []))]
        for key in keys:
            if not key:
                continue
            prior = index.get(key)
            if prior is not None and prior.id != cq.id:
                raise RuntimeError(
                    f"certified synonym collision on {key!r}: {prior.id} vs {cq.id}"
                )
            index[key] = cq
    return index


def match_certified(question: str):
    """L0 — EXACT normalized match on primary question or curated synonyms.

    High precision; never fuzzy. Synonyms live on the certified asset (pack
    YAML), not in product intent regex (F28 / VQ-01).
    """
    return _certified_index().get(_normalize(question))


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
        re.search(r"\b(\d{1,4})\s+(?:warehouses?|locations?|skus?|suppliers?|rows?|results?|items?)\b", q) or \
        re.search(r"\bwhich\s+(\d{1,4})\s+skus?\b", q) or \
        re.search(
            r"\b(\d{1,4})\s+(?:highest|lowest|best|worst|top|most|least|selling|"
            r"biggest|largest|smallest)\b",
            q,
        )
    if m:
        return int(m.group(1))
    mw = re.search(
        r"\b(?:top|first)\s+(one|two|three|four|five|six|seven|eight|nine|ten)\b",
        q,
    ) or re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:highest|best|top|lowest|worst|most|least|selling)\b",
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


#: Asking the warehouse what *will* happen. Five words was not the class.
#:
#: The old guard was ``\b(forecast|predict|projection|what if|hypothetical)\b``,
#: which is a list of ways to say it rather than the thing itself. Measured on
#: the corpus, 4 of 7 forecast paraphrases walked straight past it — "what will
#: demand be next quarter", "project SKU-00173 demand for next quarter", "how
#: much of SKU-00173 will we sell next quarter", "estimate next quarter demand"
#: — and L2 then answered each with historically-valid SQL over the past.
#: Confidently wrong about the future, badged as a real answer.
_PREDICTION_VERB = re.compile(
    r"\b(forecast(?:s|ed|ing)?|predict(?:s|ed|ion|ions|ing)?|projection(?:s)?|"
    r"extrapolat\w*|simulat\w*)\b",
    re.I,
)
_HYPOTHETICAL = re.compile(r"\b(what if|hypothetical|scenario|suppose|assuming)\b", re.I)
#: "next quarter", "coming month", "next 30 days" — a window that has not happened.
_FUTURE_WINDOW = re.compile(
    r"\b(next|coming|upcoming|following)\s+"
    r"(?:\d+\s+)?(quarter|month|week|year|fy|financial year|day)s?\b",
    re.I,
)
#: Dates the warehouse actually stores. ``shipments.expected_arrival`` is a real
#: future date, so "which deliveries are due in the next seven days" is a
#: question about recorded schedule, not a prediction — refusing it would be a
#: control rejecting legitimate work (R-0005), and the vocabulary layer already
#: routes that phrasing to a governed metric.
#: ``expir\w*`` belongs here for the same reason as arrivals: ``inventory``
#: stores ``expiry_date``, so "which SKUs expire next month" is a lookup against
#: a recorded date, not a prediction. It was missing, and the corpus could not
#: see the mistake because every expiry seed in it is past tense ("expired last
#: month") — the guard measured zero false refusals while refusing a real one.
_SCHEDULED_FUTURE = re.compile(
    r"\b(arriv\w*|deliver\w*|due|eta|incoming|inbound|expected|shipment|shipping|"
    r"scheduled|restock\w*|reorder\w*|expir\w*|renew\w*|lapse\w*)\b",
    re.I,
)


def _is_predictive(q: str) -> bool:
    """True when the question asks about the future rather than the record."""
    if _PREDICTION_VERB.search(q) or _HYPOTHETICAL.search(q):
        return True
    return bool(_FUTURE_WINDOW.search(q) and not _SCHEDULED_FUTURE.search(q))


def _wants_aggregate(q: str) -> bool:
    """Count/avg/how-many — must beat listing synonyms like bare 'expired'."""
    return bool(
        re.search(
            r"\b(how many|number of|count of|\bcount\b|average|avg|mean|total)\b",
            q,
        )
    )


#: Words that aggregate *values*. Deliberately narrower than `_wants_aggregate`,
#: which also carries the counting words: counting a ranking of five is five, a
#: question nobody is misled by, while *summing* one has no governed metric
#: behind it at all. Refusing more than that would be R-0005 in the other
#: direction.
_VALUE_AGGREGATE = re.compile(
    r"\b(sum|summed|summing|total|totals|totalled|average|averaged|avg|mean|"
    r"median|combined|altogether|added up)\b",
    re.I,
)

#: A ranking construct - "top 5", "the highest", "bottom three".
_RANKING = re.compile(
    r"\b(top|bottom|highest|lowest|largest|smallest|biggest|best|worst)\b",
    re.I,
)


def _aggregate_over_ranking(question: str) -> str | None:
    """Reason this question aggregates over a ranking, or None if it does not.

    Every metric in the pack either ranks or aggregates. None of them does both,
    so "the sum of the top 5 selling SKUs" has no plan - and the router answered
    it anyway, with whichever half it matched. Three live shapes, one class:

        sum of top 5 selling skus          -> the 5-row ranking (asked for one number)
        average of the top 5 skus          -> the 5-row ranking
        total revenue of the top 3 suppliers -> revenue_total, the whole warehouse

    The third is the dangerous one: a plausible scalar that is not the answer to
    the question, under a governed badge, which is the ebd049b class exactly.

    **Order decides it, not membership.** Both words appear in a legitimate
    ranked metric too - "top 5 categories by total revenue" ranks *on* an
    aggregate and is perfectly answerable. What distinguishes them is which one
    governs: an aggregate *before* the ranking applies to it, an aggregate
    *after* is the key the ranking sorts by. Testing membership alone would
    refuse the legitimate half (R-0005).
    """
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


#: A concrete warehouse identifier, e.g. ``SKU-00397`` or ``SKU-BETA``.
_SPECIFIC_SKU = re.compile(r"\bSKU-[A-Za-z0-9][\w-]*", re.I)


def _names_specific_sku(question: str) -> str | None:
    """The SKU code this question is about, if it names one.

    ``\\bskus?\\b`` matches inside ``SKU-00397`` — the hyphen is a word
    boundary — so "total revenue for SKU-00397" satisfied the population-level
    ``sku_count`` branch and answered "509", the count of every SKU in the
    warehouse, badged as a governed metric. Naming one entity is the strongest
    possible signal that a population aggregate is the wrong answer.
    """
    match = _SPECIFIC_SKU.search(question)
    return match.group(0).upper() if match else None


def _calendar_month(q: str) -> str | None:
    """Return 'last' | 'this' when the question names a calendar month window."""
    if re.search(r"\b(last|previous|prior)\s+month\b", q):
        return "last"
    if re.search(r"\bthis\s+month\b", q):
        return "this"
    return None


def _pct(q: str, default: int = 90) -> int:
    m = re.search(r"(?:above|over|more than|exceed(?:s|ing)?|>)\s*(\d{1,3})\s*(?:percent|%)", q)
    return int(m.group(1)) if m else default


def _location(question: str) -> str | None:
    vd = resolve_semantic_layer()

    res = vd.resolve_value(question, "location_code")
    return res.value if res.ok else None


_EXCLUSION_STOP = re.compile(
    r"\b(?:from|in|into|within|among|amongst|what|show|list|give|find|get|"
    r"top|bottom|best|worst|highest|lowest|"
    r"ranked?|numbers?|ranks?|selling|sold|revenue|sales|value|quantity|volume|"
    r"then|please|"
    # Malay verbs that end the entity clause. Without these, "kecuali BETA,
    # tunjukkan top 5 …" captured TUNJUKKAN as a SKU, failed to resolve it, and
    # abstained on a question the English form answers.
    r"tunjukkan|tunjuk|senaraikan|senarai|bagi|papar|paparkan|beri|berikan)\b",
    re.I,
)
# Words that can follow an exclusion verb without naming anything. A token that
# slips through here becomes a filter matching nothing while the envelope stamps
# success — the exact failure CLAUDE.md §8 calls out. Prefer dropping a real SKU
# from this list over admitting a filler word.
_EXCLUSION_SKIP = frozenset(
    {
        "THE", "A", "AN", "SKU", "SKUS", "AND", "OR", "FROM", "BY", "OF", "ALL",
        "ANY", "THAT", "THIS", "THOSE", "THESE", "IT", "ITS", "OUT", "IN", "ON",
        "FOR", "TO", "ME", "US", "WITH", "WHICH", "ARE", "IS", "WAS", "WERE",
        "ITEM", "ITEMS", "ROW", "ROWS", "ONE", "ONES", "PRODUCT", "PRODUCTS",
        "RESULT", "RESULTS", "ENTRY", "ENTRIES", "RECORD", "RECORDS",
        # Malay fillers — "keluarkan BETA dari top 5" must not exclude "DARI".
        "DARI", "DALAM", "UNTUK", "DAN", "ATAU", "YANG", "ITU", "INI", "KE",
        "PADA", "SAHAJA", "JUGA",
    }
)


_EXCLUSION_VERB_RE = re.compile(
    r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|remov(?:e|ing)|drop(?:ping)?|"
    r"leav(?:e|ing)\s+out|skip(?:ping)?|omit(?:ting)?|"
    r"without|except|besides|apart\s+from|other\s+than|minus|"
    r"kecuali|tak\s+nak|buang|selain|keluarkan)\s+(?:the\s+)?(.+)",
    flags=re.I,
)


def _strip_trailing_filler(clause: str) -> str:
    """Drop filler words the clause ends on, so the resolver sees only entities.

    ANS-01. Two lists decided this and disagreed. ``_EXCLUSION_STOP`` says where
    the entity clause ends and omitted ``and`` / ``dan``; ``_EXCLUSION_SKIP``,
    used by the token path, contains them. So ``_excluded_skus`` read
    "SKU-BETA and" correctly while this path handed the fuzzy resolver the
    literal string — which cannot match exactly, so the engine asked the user to
    confirm an exclusion they had already stated precisely. A refusal of
    legitimate work (R-0005), on the documented promise that an exact encoding
    applies immediately.

    Fixed here rather than by adding the two words to the stop list, because
    that leaves the class alive: any future divergence between the lists
    reopens it. One list now decides (R-0004).

    Only *trailing* tokens go. In "ignore BETA and GAMMA" the ``and`` joins two
    entities and dropping it would silently lose GAMMA — turning a visible
    refusal into a wrong answer, which is the worse trade.
    """
    tokens = clause.split()
    while tokens and tokens[-1].strip("'\".,").upper() in _EXCLUSION_SKIP:
        tokens.pop()
    return " ".join(tokens)


#: Conjunctions that may join two entities inside one exclusion clause.
_EXCLUSION_JOINER_RE = re.compile(r"[,/]+|\b(?:and|or|dan|atau)\b", re.I)
#: Shaped like a SKU identifier even if the warehouse does not encode it. A
#: token the customer clearly meant as an entity must reach the resolver so it
#: can be reported as unmatched, rather than truncating the span and silently
#: discarding the real SKUs that follow it.
_SKU_SHAPED_RE = re.compile(r"^SKU[-_]?[A-Za-z0-9]+$", re.I)


def _resolves_as_entity(text: str) -> bool:
    """True when *text* names a SKU the warehouse actually encodes.

    Fail-safe: if the semantic layer is not registered the resolver raises, and
    an unresolvable token is simply not an entity. The caller then falls back to
    the stop-list path, so an unregistered pack behaves exactly as before.
    """
    token = text.strip().strip("'\".,")
    if not token:
        return False
    try:
        res = resolve_semantic_layer().resolve_value(token, "sku")
    except Exception:
        return False
    return bool(res.exact and res.value)


def _resolves_at_all(text: str) -> bool:
    """True when the resolver gets *any* hit on *text*, exact or fuzzy.

    A fuzzy hit is meaningful on its own: "remove beta trial" must reach
    ``_resolve_exclusions`` intact so it produces a confirm chip. Narrowing it
    to the exact "beta" inside it would silently filter something the customer
    only approximately named, which is the failure the clarify path exists to
    prevent.
    """
    token = text.strip().strip("'\".,")
    if not token:
        return False
    try:
        res = resolve_semantic_layer().resolve_value(token, "sku")
    except Exception:
        return False
    return bool(res.ok and res.value)


def _looks_named(part: str) -> bool:
    """True when *part* is something the customer clearly meant as an entity.

    Three signals, none of them a filler list (R-0004): it resolves; it is
    shaped like a SKU code; or it is written in caps, which is how people write
    an identifier they believe in. "GAMMA" qualifies and "also" does not, and
    that distinction is what lets the span keep a named-but-unknown entity -
    so the resolver can report it unmatched - while still dropping an adverb.
    """
    bare = part.strip().strip("'\".,")
    if not bare:
        return False
    # A *fuzzy* hit counts: "beta trial" is something the customer named, and
    # the clarify path is what decides whether to apply or confirm it. The six
    # adverbs that broke ANS-01 - also, just, kindly, maybe, simply, perhaps -
    # get no hit at all, which is what separates them from an entity.
    if _resolves_at_all(bare) or _SKU_SHAPED_RE.match(bare):
        return True
    return bare.isupper() and bare.upper() not in _EXCLUSION_SKIP


def _entity_span(clause: str) -> str | None:
    """Longest leading span of *clause* that is entities joined by conjunctions.

    ANS-01, second pass. The first fix ended the clause *negatively* - it popped
    trailing tokens that appeared in ``_EXCLUSION_SKIP``. That still let two
    lists disagree, just later: any word neither list knows ("also", "just",
    "kindly", "maybe", "simply") stayed in the clause, the fuzzy resolver could
    not match it exactly, and the engine asked the customer to confirm an
    exclusion they had already stated precisely. Six live phrasings abstained,
    two of them carrying no punctuation at all.

    This ends the clause *positively* instead: keep the longest leading span
    that actually resolves to entities, and drop whatever follows. An unknown
    adverb cannot break it, because the span is established by what resolves
    rather than by what a list happens to name (R-0004 - the defect class is
    "a list can be incomplete", so no list decides).

    "BETA and GAMMA" survives whole - both sides resolve, and dropping the
    conjunction would silently lose GAMMA, turning a visible refusal into a
    wrong answer. Returns ``None`` when nothing in the clause resolves, leaving
    the existing clarify/abstain path to report it.
    """
    whole = clause.strip().strip("'\".,")
    if not whole:
        return None
    # Every part of the clause already looks named, so the clause means what it
    # says. Narrowing it would turn "remove beta trial" into "beta" and
    # silently filter a SKU the customer only approximately named, which is the
    # failure the clarify path exists to prevent. Leave it, and let the
    # stop-list strip take the trailing conjunction as it always did.
    #
    # Testing the *whole* clause for a resolver hit would not do: the fuzzy
    # resolver hits "SKU-BETA and also" too, and returning non-exact on it is
    # precisely the ANS-01 defect. Only a part-wise test separates a phrase the
    # customer named from a phrase an adverb wandered into.
    if _entity_parts(whole) is not None:
        return None

    tokens = whole.split()
    for end in range(len(tokens) - 1, 0, -1):
        cand = " ".join(tokens[:end])
        named = _entity_parts(cand)
        if named is not None:
            return named
    return None


def _entity_parts(cand: str) -> str | None:
    """*cand* rebuilt from its entities, or ``None`` if any part is not one.

    Returns the cleaned join rather than the raw candidate, so a trailing
    conjunction does not survive into the clause - "SKU-BETA and" cannot
    resolve and would abstain exactly as the unfixed engine did.

    Every part must look named, so an unmatched-but-clearly-named SKU stays in
    the span and reaches the resolver, which reports it. Truncating there
    instead would drop the real SKUs after it - "exclude SKU-BETA and
    SKU-GAMMA and SKU-00397" would apply only SKU-BETA and still answer green,
    and a wrong answer under a success badge is worse than an abstain
    (CLAUDE.md section 8).
    """
    parts = [p.strip().strip("'\".,") for p in _EXCLUSION_JOINER_RE.split(cand)]
    parts = [p for p in parts if p]
    if not parts or not all(_looks_named(p) for p in parts):
        return None
    return " and ".join(parts)


def _exclusion_clauses(q: str) -> list[str]:
    """Raw exclusion phrases (before token split), for sku_name fuzzy resolve."""
    out: list[str] = []
    for m in _EXCLUSION_VERB_RE.finditer(q):
        clause = m.group(1)
        stop = _EXCLUSION_STOP.search(clause)
        if stop:
            clause = clause[: stop.start()]
        clause = clause.strip().strip("'\".,")
        # Entity-anchored first (R-0004); the stop-list strip is only the
        # fallback for when the semantic layer cannot resolve anything.
        anchored = _entity_span(clause)
        clause = anchored if anchored is not None else _strip_trailing_filler(clause)
        if clause and clause.lower() not in out:
            out.append(clause)
    return out


def _excluded_skus(q: str) -> list[str]:
    """Named SKUs to drop from a ranking (raw tokens — prefer resolve_exclusions)."""
    out: list[str] = []
    for clause in _exclusion_clauses(q):
        for token in re.split(r"[\s,/]+|\band\b|\bor\b", clause, flags=re.I):
            t = (token or "").strip().strip("'\".")
            tm = re.match(r"^([A-Za-z0-9][\w-]*)$", t)
            if not tm:
                continue
            t = tm.group(1).upper()
            if t in _EXCLUSION_SKIP or len(t) < 2:
                continue
            if t not in out:
                out.append(t)
    return out


def _resolve_exclusions(q_raw: str) -> tuple[list[str], dict[str, Any] | None]:
    """Resolve exclusion phrases to warehouse SKUs.

    Returns ``(exact_skus, clarify_or_none)``.
    - All exact/encoding hits → apply immediately.
    - One fuzzy unique hit → clarify confirm chip (do not silent-filter).
    - Unresolvable with an exclusion verb → clarify payload with error (abstain).
    """
    valuedict = resolve_semantic_layer()

    clauses = _exclusion_clauses(q_raw)
    if not clauses:
        return [], None

    exact: list[str] = []
    fuzzy: list[tuple[str, str, float]] = []  # (phrase, sku, conf)
    failed: list[str] = []

    for clause in clauses:
        # Prefer whole-phrase resolve (sku_name / multi-token), then per-token.
        res = valuedict.resolve_value(clause, "sku")
        if res.exact and res.value:
            if res.value not in exact:
                exact.append(res.value)
            continue
        if res.ok and res.value and not res.exact:
            fuzzy.append((clause, res.value, float(res.confidence)))
            continue
        # Fall back to token split when the whole clause is ambiguous.
        tokens = _excluded_skus(f"exclude {clause} from the list")
        if not tokens:
            failed.append(clause)
            continue
        for tok in tokens:
            tres = valuedict.resolve_value(tok, "sku")
            if tres.exact and tres.value:
                if tres.value not in exact:
                    exact.append(tres.value)
            elif tres.ok and tres.value and not tres.exact:
                fuzzy.append((tok, tres.value, float(tres.confidence)))
            else:
                failed.append(tok)

    if failed and not exact and not fuzzy:
        return [], {
            "kind": "exclusion_unresolved",
            "phrases": failed,
            "candidates": [],
        }
    if failed and exact and not fuzzy:
        # Partial resolve is not safe — ask rather than silent-drop a leftover token.
        return [], {
            "kind": "exclusion_unresolved",
            "phrases": failed,
            "candidates": list(exact)[:5],
        }
    if fuzzy:
        # One unique fuzzy SKU across phrases → confirm; else abstain with options.
        skus = list(dict.fromkeys(s for _, s, _ in fuzzy))
        if len(skus) == 1 and not failed:
            phrase, sku, conf = fuzzy[0]
            return exact, {
                "kind": "exclusion_confirm",
                "phrase": phrase,
                "sku": sku,
                "confidence": conf,
                "also_exact": list(exact),
            }
        return exact, {
            "kind": "exclusion_ambiguous",
            "phrases": [p for p, _, _ in fuzzy] + failed,
            "candidates": skus[:5],
        }
    return exact, None


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
    exact, clarify = _resolve_exclusions(q_raw)
    slots: dict[str, Any] = {"direction": _direction(q_raw), "offset_clause": 0}
    if window:
        start, end = window
        slots["offset_clause"] = start - 1
        slots["limit"] = end - start + 1
    else:
        slots["limit"] = _explicit_limit(q_raw) or _extract_limit(q_raw, 5)
    if exact and clarify is None:
        slots["exclude_skus"] = exact
    if clarify is not None:
        slots["_exclusion_clarify"] = clarify
    return slots


def _clarify_exclusion(
    question: str,
    audit_id: str,
    *,
    clarify: dict[str, Any],
    limit: int = 5,
) -> dict[str, Any]:
    """Customer-visible confirm: did you mean exclude this SKU? (R-0001 / R-0011)."""
    kind = clarify.get("kind")
    if kind == "exclusion_confirm":
        sku = str(clarify["sku"])
        phrase = str(clarify.get("phrase") or "")
        yes = f"Yes — exclude {sku} from the top {limit} sales"
        no = f"No — show top {limit} sales without excluding"
        text = (
            f'Do you mean exclude **{sku}**'
            + (f' (matched “{phrase}”)' if phrase else "")
            + f" from the top {limit}? "
            "Click Yes within 5 seconds to confirm, or No to keep the unfiltered ranking."
        )
        suggestions = [yes, no]
        reason = f"exclusion_confirm:{sku}"
    elif kind == "exclusion_ambiguous":
        cands = clarify.get("candidates") or []
        suggestions = [f"Exclude {c} from the top {limit} sales" for c in cands[:3]]
        suggestions.append(f"Show top {limit} sales without excluding")
        text = (
            "I found more than one SKU that could match that exclusion. "
            "Pick one below, or continue without excluding."
        )
        reason = "exclusion_ambiguous"
    else:
        phrases = clarify.get("phrases") or []
        suggestions = [
            f"Top {limit} selling SKUs by revenue",
            f"Show top {limit} sales without excluding",
        ]
        text = (
            "I couldn't match "
            + (", ".join(repr(p) for p in phrases[:3]) or "that exclusion")
            + " to a SKU in the warehouse. "
            "Rephrase with the full SKU code, or ask for the ranking without excluding."
        )
        reason = "exclusion_unresolved"

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
        "suggestions": suggestions,
        "clarify": clarify,
    }


def _wants_sales_rank(q: str, q_raw: str) -> bool:
    if re.search(r"\b(top|best|highest|most)\b", q) and re.search(
        r"\b(sell|sold|revenue|sales|sku|skus|revnue)\b", q
    ):
        return True
    if re.search(r"\btop\s+\d+\b", q):
        return True
    if _rank_window(q_raw) and re.search(r"\b(sku|skus|revenue|sales|sell)\b", q):
        return True
    return False


# ── L1 metric router (ordered; specific rules before generic) ────────────────
def route_to_metric(question: str) -> MetricPlan | None:
    """Pick a governed metric, and refuse one that ignores a SKU the user named.

    Every metric in the pack is population-level: there is no "revenue of one
    SKU". So when a question names a concrete SKU, a plan whose slots do not
    carry that SKU is answering a *different question* than the one asked —
    and the badge says governed_metric while it does it.

    That was live. "total revenue for SKU-00397" returned ``sku_count = 509``
    (every SKU in the warehouse) because ``\\bskus?\\b`` matches inside the
    identifier. Excluding that one branch just moved the wrong answer along to
    ``revenue_total = 80,375,993.99`` — the whole warehouse's revenue, reported
    as one SKU's — because the next branch's guard tests the *normalized* text,
    where the identifier no longer looks like "sku". Two branches, one defect,
    and patching them one at a time was never going to converge.

    So the check lives here, once, on the way out: name a SKU, and the plan has
    to be about it. Exclusion asks pass untouched — "ignore SKU-BETA and show the
    top 5" resolves the SKU into ``exclude_skus``, so it *is* in the slots.
    Anything else falls through to L2 generation, or abstains.
    """
    plan = _route_to_metric(question)
    if plan is None:
        return None
    named = _names_specific_sku(question)
    if named and named not in str(plan.slots).upper():
        return None
    # Same shape as the SKU check above, same reason: the plan has to be about
    # the question that was asked. An aggregate over a ranking has no metric, so
    # whatever matched is answering an adjacent question (ANS-02). Enforced here
    # rather than at the one answer site so that every caller is safe by
    # construction - the abstain *message* is rendered there, but the refusal
    # cannot be forgotten by a caller that does not know to ask.
    if _aggregate_over_ranking(question) is not None:
        return None
    return plan


def _route_to_metric(question: str) -> MetricPlan | None:
    """Pick a governed metric + its slots.

    Two views of the question, deliberately kept apart:
      ``q``     — normalized into router vocabulary; decides WHICH metric.
      ``q_raw`` — the untouched question; supplies every SLOT (limits,
                  thresholds, directions, day windows, percentages, locations).

    Slots must never come from the normalized text: normalization exists to
    widen recall over wording, and it must not be able to move a number, a
    threshold or a direction. See packs/dms/semantic/vocabulary.py.
    """

    q_raw = question.lower()
    q = resolve_semantic_layer().normalize_for_routing(question)

    # scalars first — "how many X" must not fall through to a listing
    # Predictive / out-of-schema asks must abstain before sales-rank keywords fire
    # ("forecast … top SKU" contains "top"+"sku" and would otherwise invent a ranking).
    if _is_predictive(q):
        return None

    # _wants_aggregate, not a narrow keyword list: "Total cold storage locations"
    # is a count, and returning the listing for it is a shape error the caller
    # cannot see.
    if _wants_aggregate(q) and "cold storage" in q:
        return MetricPlan("cold_storage_count", {}, "count of cold-storage locations")

    # Row count vs SKU count is the whole point of the grain_fanout category:
    # "how many rows in inventory" and "how many SKUs" are different questions
    # with different answers, and this branch must beat the SKU branch below.
    # Unqualified only. "count inventory rows with no expiry date" is a filtered
    # count, and answering it with the table total is a confidently wrong number
    # that looks plausible — the exact shape this branch was added to prevent.
    if (
        _wants_aggregate(q)
        and re.search(r"\b(rows?|records?|lines?|entries|entr(?:y|ies))\b", q)
        and "inventory" in q
        and not re.search(
            r"\b(expiry|expire[ds]?|expiring|null|missing|blank|without|no\s+\w+|"
            r"hazard\w*|reorder|below|above|under|over|category|categories|"
            r"supplier\w*|location\w*|warehouse\w*|cold storage|per|each|"
            r"group(?:ed)?\s+by)\b",
            q,
        )
    ):
        return MetricPlan("inventory_row_count", {}, "row count of the inventory table")

    # Distinct suppliers sourced from — COUNT(DISTINCT supplier_id) on inventory,
    # not a count of the supplier table and not a row count after a join.
    if _wants_aggregate(q) and re.search(r"\bsuppliers?\b", q) and re.search(
        r"\b(distinct|different|unique|separate)\b|\binventory\b|"
        r"\bbuy\w*\s+from\b|\bpurchas\w*\s+from\b|\bsourc\w*\s+from\b|\bsupply us\b",
        q,
    ) and not re.search(r"\b(country|category|risk|audit|per|each)\b", q):
        return MetricPlan("supplier_count", {}, "distinct suppliers in inventory")

    # Missing expiry — must beat both the row-count branch above and the
    # "expired" listing below. A NULL expiry is unknown, not overdue.
    _no_expiry = re.search(
        r"\b(?:no|without|missing|blank|null|not have|dont have|don'?t have|"
        r"lacking|absent)\b[^.?]{0,24}\bexpir\w*", q
    ) or re.search(r"\bexpir\w*\b[^.?]{0,16}\b(?:missing|blank|null|not recorded)\b", q)
    if _no_expiry and _wants_aggregate(q):
        if re.search(r"\b(average|avg|mean)\b", q):
            return MetricPlan("avg_qty_null_expiry", {}, "average quantity where expiry is unknown")
        return MetricPlan("null_expiry_count", {}, "count of items with no expiry date")

    # _wants_aggregate, not bare "how many": "count of unique SKUs" and "our
    # distinct SKU count" are the same question and were abstaining.
    #
    # Excluded when the question names one SKU. "total revenue for SKU-00397"
    # hit every condition here — "total" is an aggregate, and `\bskus?\b` matches
    # inside the identifier — and answered "sku_count = 509" with a green
    # governed-metric badge and a drillthrough token. There is no per-SKU revenue
    # metric, so the honest outcome is to fall through and abstain.
    # "sum" and "top N" are not counting words. "i mean the sum of top 5 selling
    # skus" satisfied every condition here — `_wants_aggregate` fires on "sum",
    # `\bskus?\b` matches — and answered "sku_count = 509", the count of every
    # SKU in the warehouse, badged L1_GOVERNED_METRIC. Adding up five revenue
    # figures and counting all the products are not the same question, and the
    # customer has no way to see which one they were given.
    #
    # There is no governed metric that sums a ranking, so the honest outcome is
    # to fall through and abstain rather than answer the adjacent question.
    _counts_not_sums = not re.search(
        r"\b(sum|summed|combined|altogether|top|highest|largest|biggest|best|"
        r"lowest|smallest|bottom|rank(?:ed|ing)?)\b",
        q,
    )
    if (
        _wants_aggregate(q)
        and _counts_not_sums
        and re.search(r"\bskus?\b", q)
        and not re.search(r"\b(category|per|by)\b", q)
    ):
        return MetricPlan("sku_count", {}, "distinct SKU count")
    # delayed COUNT before status listing — "how many delayed" must not return 1000 rows
    if _wants_aggregate(q) and "delayed" in q and re.search(r"\bshipments?\b", q) \
            and not re.search(r"\b(carrier|per|by|each|warehouse|destination)\b", q):
        return MetricPlan("delayed_count", {}, "count of delayed shipments")

    # per-warehouse / per-carrier breakdowns of shipments (before the status listing)
    if "delayed" in q and re.search(r"\bcarrier", q):
        return MetricPlan("count_by_carrier", {"status": "DELAYED"}, "delayed shipments grouped by carrier")
    if re.search(r"\b(per|by|each)\b", q) and re.search(r"\b(warehouse|destination|location)\b", q) \
            and ("delayed" in q or "incoming" in q or "shipment" in q):
        status = "DELAYED" if "delayed" in q else "IN_TRANSIT"
        return MetricPlan("count_by_destination", {"status": status}, f"{status} shipments grouped by destination")

    # revenue — calendar month before rolling-day window; bare total before ranked "top sales"
    if re.search(r"\b(revenue|sales|sold)\b", q) and _calendar_month(q) == "last":
        return MetricPlan("revenue_last_month", {}, "revenue in the previous calendar month")
    if re.search(r"\b(revenue|sales|sold)\b", q) and re.search(r"\b(last|past|within|previous)\b.*\bday", q):
        return MetricPlan("revenue_windowed", {"days": _days(q_raw, 30)}, "revenue over a rolling window")
    # G6 — bare total revenue (no month/window); must not fall through to abstain
    if re.search(r"\btotal\b", q) and re.search(r"\b(revenue|sales)\b", q):
        return MetricPlan("revenue_total", {}, "total outbound revenue")
    if re.search(r"\b(revenue|sales)\b", q) and not re.search(
        r"\b(top|best|highest|most|sku|skus|rank|per|by|each)\b", q
    ):
        return MetricPlan("revenue_total", {}, "total outbound revenue")

    # spend / stock value aggregates (before supplier ranking / listings)
    if re.search(r"\bspend\b", q) and re.search(r"\bcountry\b", q):
        return MetricPlan("spend_by_country", {}, "inventory spend grouped by supplier country")
    if re.search(r"\bstock value\b", q) and re.search(r"\bcategor", q):
        return MetricPlan("stock_value_by_category", {}, "stock value by category")
    if re.search(r"\bsku count\b", q) and re.search(r"\bcategor", q):
        return MetricPlan("sku_count_by_category", {}, "SKU count by category")

    # audit overdue — "who hasn't been audited" / "audit overdue"
    if "audit" in q and re.search(r"\b(overdue|not been|hasn't|havent|have not)\b", q):
        return MetricPlan(
            "audit_overdue",
            {"days": _days(q_raw, 90)},
            "suppliers with overdue audit",
        )

    # supplier risk threshold
    if re.search(r"\brisk\b", q) and re.search(r"\b(above|over|below|under|greater|less|more than|exceed|>|<)\b", q) \
            and not re.search(r"\b(pending|shipment)\b", q):
        return MetricPlan("suppliers_by_risk",
                          {"threshold": _threshold(q_raw), "op": _threshold_op(q_raw)},
                          "suppliers filtered by risk-score threshold")

    # high-risk suppliers with pending shipments
    if re.search(r"\bhigh[- ]?risk\b", q) and re.search(r"\b(pending|shipment)\b", q):
        return MetricPlan(
            "high_risk_pending",
            {"threshold": _threshold(q_raw, 0.7)},
            "high-risk suppliers with pending shipments",
        )

    # average lead time by country
    if re.search(r"\baverage\b|\bmean\b|\bavg\b", q) and "lead time" in q:
        return MetricPlan("avg_lead_time_by_country", {}, "average lead time grouped by country")

    # free capacity ranking
    if re.search(r"\bfree\b|\bspare\b|\bavailable\b", q) and "capacit" in q:
        return MetricPlan("free_capacity",
                          {"limit": _explicit_limit(q_raw) or 1, "direction": _direction(q_raw)},
                          "warehouses ranked by free capacity")

    # capacity above a percentage (incl. almost/nearly full via vocabulary)
    if "capacit" in q and re.search(r"\b(above|over|more than|exceed(?:s|ing)?)\b.*\d", q):
        return MetricPlan("capacity_above", {"pct": _pct(q_raw)}, "locations above a capacity threshold")
    if re.search(r"\b(almost|nearly)\s+full\b", q) or (
        "capacit" in q and re.search(r"\b(almost|nearly)\s+full\b", q)
    ):
        return MetricPlan("capacity_above", {"pct": _pct(q_raw, 90)}, "locations above a capacity threshold")
    # utilis\w* / utiliz\w*, not utilis\b — the trailing \b made the word
    # "utilisation" itself fail to match, so this branch was only ever reachable
    # by the stem alone. The golden question hits L0 certified, which is why the
    # dead branch went unnoticed.
    if "capacit" in q and re.search(r"\b(utilis\w*|utiliz\w*|how full|usage)\b", q):
        return MetricPlan("capacity_utilisation", {}, "capacity utilisation per location")

    # arriving window
    if "arriving" in q or ("incoming" in q and re.search(r"\bweek|\bdays?\b", q)):
        return MetricPlan("arriving_window", {"days": _days(q_raw, 7)}, "in-transit shipments arriving within a window")

    # shipment status listing (after delayed_count scalar)
    for status in ("delayed", "in transit", "in_transit", "pending", "delivered", "cancelled"):
        if status in q and re.search(r"\bshipments?\b", q):
            norm = "IN_TRANSIT" if status.startswith("in ") or status == "in_transit" else status.upper()
            return MetricPlan("shipments_by_status", {"status": norm}, f"shipments with status {norm}")

    # cold storage listing
    if "cold storage" in q:
        return MetricPlan("cold_storage_list", {}, "cold-storage locations")

    # low stock (optionally warehouse-scoped)
    if re.search(r"\b(below reorder|low stock|understocked|reorder level)\b", q):
        loc = _location(question)
        return MetricPlan("low_stock", {"wh": loc} if loc else {},
                          f"items below reorder level{' at ' + loc if loc else ''}")

    # not restocked window
    if re.search(r"\b(not restocked|stale)\b", q) or ("restock" in q and "not" in q):
        return MetricPlan("stale_restock", {"days": _days(q_raw, 30)}, "items not restocked within a window")

    # expired — aggregate / calendar month BEFORE bare listing
    if "expired" in q or "past expiry" in q or "out of date" in q:
        month = _calendar_month(q)
        if month == "last" or (_wants_aggregate(q) and month == "last"):
            return MetricPlan("expired_last_month", {}, "count of items that expired last month")
        if _wants_aggregate(q):
            return MetricPlan("expired_count", {}, "count of currently expired inventory")
        return MetricPlan("expired_items", {}, "expired inventory listing")

    # active alerts
    if "alert" in q and re.search(r"\b(active|open|unresolved|current)\b", q):
        return MetricPlan("active_alerts", {}, "unresolved alerts")

    # sales ranking (after month/window scalars so "last month sales" never ranks)
    if _wants_sales_rank(q, q_raw):
        slots = _sales_rank_slots(q_raw)
        clarify = slots.pop("_exclusion_clarify", None)
        if clarify is not None:
            # Signal to answer() — do not compile/filter yet.
            return MetricPlan(
                "_exclusion_clarify",
                {"clarify": clarify, "limit": int(slots.get("limit") or 5)},
                "exclusion needs confirm",
            )
        if re.search(r"\b(quantity|volume|kg|kilograms?|weight|units?)\b", q):
            return MetricPlan(
                "sales_by_volume",
                slots,
                "SKUs ranked by quantity sold",
            )
        return MetricPlan("sales_by_value", slots, "SKUs ranked by sales value")
    # unranked "last month sales" catch-all if earlier branch missed phrasing
    if re.search(r"\b(sales|revenue)\b", q) and _calendar_month(q) == "last":
        return MetricPlan("revenue_last_month", {}, "revenue in the previous calendar month")

    return None


# ── truncation-honest total ──────────────────────────────────────────────────
def _true_count(sql: str, *, verified: VerifiedManifest) -> int | None:
    """COUNT(*) over the query with LIMIT/ORDER stripped — the honest total
    behind a possibly-capped listing. Returns None if it can't be computed.

    Always runs through the C4 submit executor so the manifest applies. The
    count is a second read of the same data, so a count that ignored predicates
    would disclose the size of a set the session may not see (SEC-01).
    """
    from CortexOS.execution.submit import execute_count

    return execute_count(verified, sql)


# ── suggestions for abstain ──────────────────────────────────────────────────
def _suggestions(question: str, limit: int = 3) -> list[str]:
    """Nearest answerable questions (token overlap over certified + metric synonyms)."""
    sem = resolve_semantic_layer()
    model = sem.load_model()
    qtokens = set(_normalize(question).split())
    scored: list[tuple[float, str]] = []
    for cq in model.certified:
        overlap = len(qtokens & set(_normalize(cq.question).split()))
        if overlap:
            scored.append((overlap, cq.question))
    for metric in model.metrics.values():
        for syn in metric.synonyms[:2]:
            overlap = len(qtokens & set(_normalize(syn).split()))
            if overlap:
                scored.append((overlap * 0.9, syn))
    scored.sort(key=lambda t: -t[0])
    seen: list[str] = []
    for _, qtext in scored:
        if qtext not in seen:
            seen.append(qtext)
        if len(seen) >= limit:
            break
    if not seen:  # cold: certified titles + metric labels from the pack
        for cq in model.certified:
            seen.append(cq.question)
            if len(seen) >= limit:
                break
        if len(seen) < limit:
            for metric in model.metrics.values():
                label = sem.metric_label(metric)
                if label not in seen:
                    seen.append(label)
                if len(seen) >= limit:
                    break
    return seen[:limit]


def _catalog_response(question: str, audit_id: str) -> dict[str, Any]:
    """META-01 — browse what the semantic layer can answer (no SQL)."""
    sem = resolve_semantic_layer()

    payload = sem.build_catalog_answer()
    model = sem.load_model()
    suggestions = [cq.question for cq in model.certified[:5]]
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
        "query_plan": _honest_plan(question, None, layer=payload["layer"], assumptions="catalog browse"),
    }


def _plausibility_runner(verified: VerifiedManifest) -> sql_plausibility.Runner:
    """A bounded probe executor, always under the manifest.

    Probes route through ``execute_sql`` so the manifest applies: a value this
    session may not read probes as absent, and the abstain that follows says
    "no such value" without disclosing that one exists. The bare-connection
    runner that used to serve unbound callers is gone — a probe is a read, and
    an unenforced read is the same disclosure whether or not it feeds an answer
    (SEC-01).
    """

    def _run(probe_sql: str) -> list[dict[str, Any]]:
        from CortexOS.execution.submit import execute_sql

        rows, _, _ = execute_sql(verified, probe_sql)
        return rows

    return _run


def _abstain_impossible_filter(
    question: str,
    audit_id: str,
    *,
    result: sql_plausibility.PlausibilityResult,
) -> dict[str, Any]:
    """Refuse a query whose filter cannot match, and name the near-misses.

    Distinct from a plain abstain because the customer can act on it: the value
    they asked for does not exist under that spelling, and the answer says which
    spellings do. Returning "0" here would be the confidently-wrong failure the
    whole eval floor is built to keep at zero.
    """
    first = result.impossible[0]
    pred = first.predicate
    suggestions: list[str] = []
    if first.candidates:
        suggestions = [
            re.sub(re.escape(pred.value), c, question, count=1, flags=re.I)
            if re.search(re.escape(pred.value), question, flags=re.I)
            else f"{question} (using {c})"
            for c in first.candidates[:3]
        ]
    suggestions.extend(s for s in _suggestions(question) if s not in suggestions)

    if first.candidates:
        shown = ", ".join(f"**{c}**" for c in first.candidates[:3])
        text = (
            f"The warehouse has no `{pred.column}` equal to `{pred.value}`, so that filter "
            f"would match nothing and any total I gave you would read as a real zero. "
            f"Closest values stored: {shown}."
        )
    else:
        text = (
            f"The warehouse has no `{pred.column}` equal to `{pred.value}`, so that filter "
            f"would match nothing. I'd rather say that than hand you a zero that looks real."
        )

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
        "assumptions": f"impossible_filter: {result.reason()}",
        "total_count": 0,
        "suggestions": suggestions[:4],
    }


def _bound_sources_sentence(verified: VerifiedManifest | None) -> str:
    """One sentence naming what this session can be answered over.

    Shared by every abstain so the two cannot describe the same grant
    differently. P0-DEMO-02's clause is that the system says which sources it
    *can* answer over; an abstain that only says no is a dead end the buyer
    reads as "it does not work".
    """
    granted = sorted(
        {str(name).lower() for name in (verified.manifest.row_predicates if verified else {})}
    )
    if not granted:
        return "This session has no data sources bound."
    return "This session is bound to: " + ", ".join(granted) + "."


def _abstain(
    question: str,
    audit_id: str,
    *,
    reason: str,
    verified: VerifiedManifest | None = None,
) -> dict[str, Any]:
    # The suggestions come from the semantic layer, which describes the whole
    # pack - so on a session bound to an uploaded workbook they used to offer
    # demo-warehouse questions this very session would refuse. Naming the bound
    # sources first keeps the offer honest (P0-DEMO-02).
    suggestions = _suggestions(question)
    hint = " Try: " + " / ".join(f'"{s}"' for s in suggestions)
    return {
        "answer": (f"I can't answer that from the DMS semantic layer with confidence ({reason}). "
                   + _bound_sources_sentence(verified)
                   + hint),
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
        "suggestions": suggestions,
    }


def _local_grant_tables() -> frozenset[str]:
    """Tables the local warehouse grant may cover — the semantic layer's own.

    Read from the pack rather than hardcoded, so a pack that stops declaring a
    table stops being able to answer over it. Kept as a seam so a test can
    narrow the grant and prove the enforcement below actually refuses.
    """
    semantic = load_semantic_layer()
    return frozenset((semantic.get("tables") or {}).keys())


def _local_verified(session_id: str | None) -> VerifiedManifest:
    """Self-issued grant for a caller that never bound a session manifest (SEC-01)."""
    return local_manifest(
        session_id=(session_id or "local").strip() or "local",
        tables=_local_grant_tables(),
        allowed_paths=[str(DEFAULT_DB)],
    )


def resolve_grant(
    session_id: str | None, verified: VerifiedManifest | None
) -> tuple[VerifiedManifest, str, str]:
    """The one place that decides which grant governs a turn.

    SEC-01 and P0-DEMO-02 were one defect because two pieces of code answered
    "what may this session read?" and were free to disagree. ``/v1/contract/ask``
    resolved :func:`get_session_registry`; ``/dms/query`` and ``/mcp/call`` did
    not, so a session that bound a narrow grant still ran under the wide
    self-issued mint on the product path - and P0-DEMO-02's grounding gate
    intersected against a grant that already contained every table the pack
    declares. The gate was vacuous exactly where the customer stands.

    Teaching those two call sites to look up the registry would have made
    today's lists agree and left the next entry point free to disagree
    tomorrow. So resolution happens here, once, for every caller: an explicit
    manifest wins, then the session registry, and only a caller with no binding
    at all falls back to the local mint.

    Returns the grant, its kind, and why it degraded (empty when it did not).
    The reason is returned rather than logged because an expired binding
    widening back to the full local grant is the most dangerous of the three
    outcomes, and the caller has to be able to see which one they got.
    """
    if verified is not None:
        return verified, SESSION_GRANT, ""
    try:
        return get_session_registry().resolve(session_id), SESSION_GRANT, ""
    except SessionExpired:
        return _local_verified(session_id), LOCAL_GRANT, "session manifest expired"
    except SessionUnbound:
        return _local_verified(session_id), LOCAL_GRANT, "no session manifest bound"


def stamp_grant(
    result: dict[str, Any], *, verified: VerifiedManifest, kind: str, degraded: str
) -> dict[str, Any]:
    """Disclose the governing grant on the envelope, on every return path.

    :func:`answer` has more than twenty returns. Asking each to remember the
    disclosure is the same shape as the defect above - a set of places that can
    disagree - so there is exactly one writer, at the exit, and no path can
    ship an envelope that hides the degradation (R-0011).
    """
    sources = sorted({str(name).lower() for name in verified.manifest.row_predicates})
    result["grant_kind"] = kind
    result["granted_sources"] = sources
    if degraded:
        note = f"grant={kind} ({degraded})"
        prior = str(result.get("assumptions") or "")
        result["assumptions"] = f"{prior}; {note}" if prior else note
    plan = result.get("query_plan")
    if isinstance(plan, dict):
        plan["grant_kind"] = kind
        plan["granted_sources"] = sources
        if degraded:
            plan["assumptions"] = result["assumptions"]
    return result


def _abstain_refused(
    question: str, audit_id: str, *, exc: ManifestError
) -> dict[str, Any]:
    """A manifest refusal the caller receives as an envelope, not a stack trace.

    Same refusal *types* as /v1/contract/submit — the class name is reported so
    PathNotAllowed, StatementNotAllowed and SqlNotAnalyzable stay
    distinguishable — and the query is still refused: no rows, no SQL.

    The route is deliberately not the ordinary abstain code. ``answer_question``
    bridges ``needs_clarification`` into the legacy ranked-SQL path for delayed/
    late questions, and that path executes on its own connection — so reusing
    the abstain code here would let a manifest refusal fall straight through
    into the execution it was meant to prevent.
    """
    kind = type(exc).__name__
    reason = f"{kind}: {exc}"
    return {
        "answer": (
            "That question can't be answered inside this session's data grant "
            f"({kind}). Nothing was read."
        ),
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [kind],
        "route": "refused",
        "rows": [],
        "source_table": None,
        "layer": "refused",
        "badge": "refused",
        "assumptions": reason,
        "total_count": 0,
        "suggestions": [],
        "query_plan": _honest_plan(question, None, layer="refused", assumptions=reason),
    }


def ungrounded_tables(
    sql: str, *, verified: VerifiedManifest | None
) -> frozenset[str]:
    """Tables this SQL reads that the session never bound (P0-DEMO-02).

    The grant is the manifest's ``row_predicates`` keys — the same set
    ``enforce_manifest`` decides by — so the two can never disagree about what
    was allowed.

    With no manifest there is nothing to intersect against, and this returns
    empty rather than inventing a grant. That path is the ``/dms/query`` bypass
    tracked as SEC-01; closing it here would be guessing at a boundary instead
    of enforcing one.

    Unparseable SQL yields no table set. It cannot be *cleared* either, so it is
    reported as ungrounded under a sentinel — fail closed, since every caller
    treats a non-empty result as a refusal.
    """
    if verified is None:
        return frozenset()
    try:
        read = tables_read(sql)
    except ManifestError:
        return frozenset({"<unanalysable-sql>"})
    granted = {str(name).lower() for name in verified.manifest.row_predicates}
    return frozenset(sorted(read - granted))


def _abstain_ungrounded(
    question: str,
    audit_id: str,
    *,
    ungrounded: frozenset[str],
    verified: VerifiedManifest | None,
) -> dict[str, Any]:
    """Refuse a plan that reads outside the session's bound sources, and say so.

    Names the sources that *are* answerable. An abstain that only says no is a
    dead end the buyer reads as "it doesn't work"; the granted table names are
    already in their hands, because they are what the session bound.
    """
    granted = sorted({str(name).lower() for name in (verified.manifest.row_predicates if verified else {})})
    refused = ", ".join(sorted(ungrounded))
    can_answer = _bound_sources_sentence(verified)
    reason = f"question resolves to ungranted source(s): {refused}"
    return {
        "answer": (
            "I can't answer that from the sources bound to this session - the closest "
            f"governed answer would read {refused}, which this session did not bind. "
            + can_answer
            + " Ask about those, or bind the source you meant and ask again."
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
        "suggestions": granted[:4],
        "query_plan": _honest_plan(question, None, layer="abstain", assumptions=reason),
    }


def _try_document_rag(
    question: str,
    audit_id: str,
    *,
    space_id: str | None,
) -> dict[str, Any] | None:
    """Space-scoped doc-RAG after SQL layers abstain (RAG-03).

  Only runs when ``space_id`` is set and the retrieval port returns hits for
  that Space — never as an early keyword shortcut ahead of L0/L1/L2.
    """
    if not space_id:
        return None
    from CortexOS.dms.document_retrieval_port import (
        DocumentRetrievalNotRegistered,
        resolve_document_retrieval,
    )
    from CortexOS.rag import lexical

    try:
        retriever = resolve_document_retrieval()
    except DocumentRetrievalNotRegistered:
        return None
    if not retriever.is_configured():
        return None
    hits = retriever.retrieve(question, space_id=space_id, top_k=8)
    if not hits:
        return None
    for hit in hits:
        if str(hit.get("space_id") or "") != str(space_id):
            return None
    corpus_hits = [
        {
            "id": h.get("id"),
            "text": h.get("content") or h.get("text") or "",
            "space_id": h.get("space_id"),
            "source_id": h.get("source_id"),
            "score": h.get("score"),
        }
        for h in hits
    ]
    rag = lexical.answer_from_hits(question, corpus_hits)
    sources = [
        f"{h.get('source_id')}:{h.get('chunk_index', h.get('id'))}"
        for h in hits
        if h.get("source_id")
    ]
    return {
        "answer": rag["answer"],
        "sql_used": None,
        "chart_spec": None,
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "rag",
        "sources": sources,
        "rows": [],
        "source_table": None,
        "layer": "rag",
        "badge": "document",
        "assumptions": f"document retrieval (space={space_id})",
        "total_count": 0,
        "query_plan": _honest_plan(question, None, layer="rag"),
    }


def _abstain_or_document_rag(
    question: str,
    audit_id: str,
    *,
    reason: str,
    space_id: str | None = None,
    verified: VerifiedManifest | None = None,
) -> dict[str, Any]:
    """Final abstain path - try space-scoped doc-RAG before giving up."""
    doc = _try_document_rag(question, audit_id, space_id=space_id)
    if doc is not None:
        return doc
    return _abstain(question, audit_id, reason=reason, verified=verified)


# ── session memory (follow-up anaphora) ───────────────────────────────────────
# Keyed by session_id + space_id so Space A never sees Space B's prior SQL (C6).
_SESSION: dict[str, dict[str, Any]] = {}


def _session_key(session_id: str | None, space_id: str | None = None) -> str:
    sid = (session_id or "demo").strip() or "demo"
    sp = (space_id or "").strip()
    return f"{sid}::space:{sp}" if sp else sid


def _is_derived_scalar(turn: dict[str, Any]) -> bool:
    """A single number computed from the turn before it (a sum, an average, a count)."""
    if turn.get("layer") != "session":
        return False
    rows = turn.get("rows") or []
    if len(rows) != 1:
        return False
    return all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in rows[0].values()
    )


def _granted_tables(
    prior: dict[str, Any] | None,
    verified: VerifiedManifest | None,
) -> frozenset[str] | None:
    """Tables the prior turn was allowed to read — follow-ups must stay inside this."""
    if prior:
        stored = prior.get("granted_tables")
        if stored:
            return frozenset(str(t).lower() for t in stored)
    if verified is not None:
        return frozenset(k.lower() for k in verified.manifest.row_predicates)
    return None


def _tables_referenced(sql: str) -> set[str]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    names: set[str] = set()
    for table in tree.find_all(sqlglot.exp.Table):
        name = (table.name or "").lower()
        if name:
            names.add(name)
    return names


def _check_followup_grant(
    sql: str,
    prior: dict[str, Any],
    verified: VerifiedManifest | None,
) -> None:
    """Refuse follow-ups that would touch tables outside the bound manifest (FOLLOWUP-03)."""
    granted = _granted_tables(prior, verified)
    if not granted:
        return
    extra = _tables_referenced(sql) - granted
    if extra:
        raise PathNotAllowed(
            f"follow-up would read {sorted(extra)!r} outside the prior grant "
            f"{sorted(granted)!r}"
        )


def _remember(
    session_id: str | None,
    turn: dict[str, Any],
    *,
    space_id: str | None = None,
    verified: VerifiedManifest | None = None,
) -> None:
    """Record this turn as what "them" refers to next — unless it is a scalar.

    "Top 5" then "sum of them" then "how many of them?" answered **1**: the sum
    had replaced the ranking as the anchor, so "them" was one number and
    counting it gave one. Literally consistent and not what anybody means.

    A derived scalar therefore does not become the new anchor; the listing it
    came from stays. That keeps a chain of follow-ups pointing at the thing on
    screen rather than at the last thing computed from it. A follow-up that
    produces a *listing* — re-slicing a ranking — does become the anchor, because
    then the screen really has changed.
    """
    key = _session_key(session_id, space_id)
    if _is_derived_scalar(turn) and key in _SESSION:
        return
    if verified is not None and "granted_tables" not in turn:
        turn = {
            **turn,
            "granted_tables": sorted(k.lower() for k in verified.manifest.row_predicates),
        }
    _SESSION[key] = turn


def clear_session(session_id: str | None = None, *, space_id: str | None = None) -> None:
    if session_id is None and space_id is None:
        _SESSION.clear()
    else:
        _SESSION.pop(_session_key(session_id, space_id), None)


#: "their"/"its" directly ahead of an aggregation word ("their mean", "its
#: total") is a follow-up. Unlike "them/those/these", which are rarely a fresh
#: question's own grammar, "their"/"its" is an ordinary possessive determiner —
#: "SKUs under their reorder point", "stock past its expiry date" — so it is
#: recognised only in this narrow construction, and only when the question
#: does not name its own subject (the same guard _is_window_followup already
#: uses for "them"-adjacent re-slicing).
_POSSESSIVE_FOLLOWUP = re.compile(
    r"\b(their|its)\s+(average|avg|mean|median|total|sum|count|min|minimum|max|maximum)\b",
    re.I,
)


def _is_anaphora(q: str) -> bool:
    """Follow-up pronouns / arithmetic over the prior result set.

    "give me their mean" abstained: "them/those/these" were recognised but not
    the possessive "their/its". ``_aggregate_prior`` already reads "mean" from
    the question text on its own — the gate here is only what decides whether
    to look at the prior turn at all, so the missing pronoun read as a fresh,
    unrouted question instead of a follow-up.

    A first attempt added "their"/"its" to the bare pronoun class and broke two
    corpus questions that were never follow-ups: "SKUs under their reorder
    point at WH-A" and "what stock has gone past its expiry date" both name
    their own subject in the same clause, and got answered over an unrelated
    prior turn's ranking instead. Scoping the match to "possessive directly
    before an aggregation word", and refusing it when the question names its
    own subject, fixes the reported case without that regression (R-0007
    caught both directions before this landed).
    """
    if _POSSESSIVE_FOLLOWUP.search(q) and not _NAMES_OWN_SUBJECT.search(q):
        return True
    return bool(
        re.search(
            r"\b(them|those|these)\b|"
            r"\b(average|avg|mean|median|total|sum|count|how many|min|minimum|max|maximum)\s+of\s+(them|those|these|it)\b|"
            r"\bwhat is the (average|median|min(?:imum)?|max(?:imum)?) of (them|those|these|it)\b|"
            r"\baverage of (them|those|these)\b|"
            r"\bmedian of (them|those|these)\b|"
            r"\b(?:min|minimum|max|maximum) of (them|those|these)\b|"
            r"\bdivid(?:e|ed|ing)\b.*?\bby\s+\d|"
            r"\bmultipl(?:y|ied|ying)\b.*?\bby\s+\d|"
            r"(?:/|÷|×|\*)\s*\d|"
            r"\badd(?:ing)?\s+\d|"
            r"\b(?:minus|subtract(?:ing)?)\s+\d|"
            r"\+\s*\d|"
            r"(?<!\w)-\s*\d|"
            r"\bone\s+fifth\b",
            q,
        )
    ) or _is_window_followup(q)


#: Domain nouns that make a question stand on its own. A follow-up borrows its
#: subject from the previous turn ("show me number 2-6"); a question that names
#: its own subject ("top 2 to 6 SKUs by revenue") is a fresh ask and must go to
#: the normal router, not get re-windowed over whatever happened to be on screen.
_NAMES_OWN_SUBJECT = re.compile(
    r"\b(skus?|revenue|sales|shipments?|suppliers?|inventory|alerts?|warehouses?|"
    r"spend|costs?|items?|products?|categor(?:y|ies)|carriers?|locations?)\b",
    re.I,
)


def _skip_first(q: str) -> int | None:
    """"ignoring the first one", "excluding the top 2", "after the first 3" -> N."""
    m = re.search(
        r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|without|skip(?:ping)?|omit(?:ting)?|"
        r"apart from|other than|besides|after|drop(?:ping)?)\s+"
        r"(?:the\s+)?(?:first|top|highest|best)\s+(one|1|two|2|three|3|\d{1,2})\b",
        q,
        re.I,
    )
    if not m:
        # "ignoring the first" / "without the top" with no count means one.
        if re.search(
            r"\b(?:ignor(?:e|ing)|exclud(?:e|ing)|without|skip(?:ping)?|omit(?:ting)?|"
            r"apart from|other than|besides|drop(?:ping)?)\s+(?:the\s+)?"
            r"(?:first|top|highest|best)\b(?!\s+\d)",
            q,
            re.I,
        ):
            return 1
        return None
    word = m.group(1).lower()
    return {"one": 1, "two": 2, "three": 3}.get(word, int(word) if word.isdigit() else 1)


def _is_window_followup(q: str) -> bool:
    """A re-slice of the previous ranking, phrased without naming a subject."""
    if _NAMES_OWN_SUBJECT.search(q):
        return False
    return _rank_window(q) is not None or _skip_first(q) is not None


def _window_over_prior(
    prior_sql: str, question: str, rows: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Re-run the previous ranking at a different offset / length.

    "show me number 2-6" and "ignoring the first one" are the two most natural
    things to say after a top-5, and both abstained — the ranked metrics have
    carried an ``offset_clause`` parameter the whole time, but the router only
    ever reached it when the question named its own subject, which a follow-up
    by definition does not.

    Rewrites LIMIT/OFFSET on the prior statement rather than recompiling the
    metric, because the prior turn is often a *certified* query with no
    ``metric_id`` to recompile — which is exactly the case in the reported
    session, where turn one was ``cq_sales_top5_value``.
    """
    window = _rank_window(question)
    skip = _skip_first(question)
    if window is None and skip is None:
        raise ValueError("not a window follow-up")

    tree = sqlglot.parse_one(prior_sql, read="duckdb")
    if window is not None:
        start, end = window
        offset, limit = start - 1, end - start + 1
    else:
        prior_limit = None
        limit_node = tree.args.get("limit")
        if limit_node is not None:
            try:
                prior_limit = int(limit_node.expression.this)
            except (AttributeError, TypeError, ValueError):
                prior_limit = None
        # Keep the window the same size as the one on screen, just moved along.
        offset, limit = int(skip or 1), prior_limit or max(len(rows), 5)

    if offset < 0 or limit < 1 or limit > MAX_LIMIT:
        raise ValueError("window out of range")

    tree.set("limit", sqlglot.exp.Limit(expression=sqlglot.exp.Literal.number(limit)))
    tree.set("offset", sqlglot.exp.Offset(expression=sqlglot.exp.Literal.number(offset)))
    return tree.sql(dialect="duckdb"), []  # rows filled by execute


def _scale_factor(q: str) -> tuple[str, float] | None:
    """Return ('div'|'mul'|'add'|'sub', factor) for session arithmetic follow-ups."""
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
    m = re.search(r"\badd(?:ing)?\s+(\d+(?:\.\d+)?)\b", q)
    if m:
        return "add", float(m.group(1))
    m = re.search(r"\b(?:minus|subtract(?:ing)?)\s+(\d+(?:\.\d+)?)", q)
    if m:
        return "sub", float(m.group(1))
    m = re.search(r"\+\s*(\d+(?:\.\d+)?)", q)
    if m:
        return "add", float(m.group(1))
    m = re.search(r"(?<!\w)-\s*(\d+(?:\.\d+)?)", q)
    if m:
        return "sub", float(m.group(1))
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


class FollowupUnsupported(Exception):
    """The follow-up named an aggregation this turn cannot compute.

    Distinct from "this is not a follow-up at all". The caller falls through to
    the other layers on a generic failure, which is right when the question was
    never a follow-up — but wrong here: the customer asked for the sum of the
    thing on screen, and letting the keyword router take a second guess at it is
    how "sum of top 5 selling skus" became "sku_count = 509". Carries a reason
    the abstain can show.
    """


def _aggregate_prior(
    prior_sql: str,
    question: str,
    rows: list[dict[str, Any]],
    *,
    total_count: int | None = None,
    total_exact: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """Follow-up aggregate / scale over the prior result.

    COUNT uses a guarded subquery wrap. SUM, AVG and divide/multiply are
    computed from the prior row snapshot (literal SELECT so the allowlist still
    passes).

    SUM was missing and there was no refusal behind it, so "sum of them" fell
    all the way through to the COUNT wrap and answered ``followup_count = 491``
    for a question about revenue. The scale branch below has been telling people
    to "ask to sum them first" against a sum that was never implemented.

    The rule now: an aggregation the customer named explicitly is either
    computed or refused. Falling back to a *different* aggregation and putting a
    confident number next to it is the worst outcome available (R-0011).
    """
    q = question.lower()
    wants_avg = bool(re.search(r"\b(average|avg|mean)\b", q))
    wants_median = bool(re.search(r"\bmedian\b", q))
    wants_min = bool(re.search(r"\b(min(?:imum)?)\b", q))
    wants_max = bool(re.search(r"\b(max(?:imum)?)\b", q))
    wants_sum = bool(
        re.search(r"\b(sum|summed|combined|altogether|added up|add(?:ed)? together)\b", q)
        or re.search(r"\btotal(?:led|led up)?\b(?!\s+(?:count|number))", q)
    )
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
        if op == "div":
            result = round(base / factor, 2)
        elif op == "mul":
            result = round(base * factor, 2)
        elif op == "add":
            result = round(base + factor, 2)
        else:
            result = round(base - factor, 2)
        col = f"{op}_{measure}"
        sql = f"SELECT CAST({result} AS DOUBLE) AS {col}"
        return sql, [{col: result}]

    if wants_sum:
        # Explicitly asked for a sum: compute it, or refuse. Never silently
        # answer with a count of the same rows.
        vals = []
        for row in rows:
            raw = row.get(measure) if measure else None
            if raw is None:
                continue
            try:
                vals.append(float(raw))
            except (TypeError, ValueError):
                continue
        if not vals:
            raise FollowupUnsupported(
                "the previous answer has no numeric column to add up"
            )
        total = round(sum(vals), 2)
        col = f"sum_{measure}"
        sql = f"SELECT CAST({total} AS DOUBLE) AS {col}"
        return sql, [{col: total}]

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

    if wants_avg:
        # Same rule as SUM: an average that cannot be computed is refused, not
        # quietly downgraded to a row count.
        raise FollowupUnsupported("the previous answer has no numeric column to average")

    if wants_median and measure and rows:
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
            median_val = round(statistics.median(vals), 2)
            col = f"median_{measure}"
            sql = f"SELECT CAST({median_val} AS DOUBLE) AS {col}"
            return sql, [{col: median_val}]

    if wants_median:
        # Same rule as SUM/AVG: refused, not silently downgraded to a count.
        raise FollowupUnsupported("the previous answer has no numeric column to take a median of")

    if wants_min and measure and rows:
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
            min_val = round(min(vals), 2)
            col = f"min_{measure}"
            sql = f"SELECT CAST({min_val} AS DOUBLE) AS {col}"
            return sql, [{col: min_val}]

    if wants_min:
        raise FollowupUnsupported("the previous answer has no numeric column to take a minimum of")

    if wants_max and measure and rows:
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
            max_val = round(max(vals), 2)
            col = f"max_{measure}"
            sql = f"SELECT CAST({max_val} AS DOUBLE) AS {col}"
            return sql, [{col: max_val}]

    if wants_max:
        raise FollowupUnsupported("the previous answer has no numeric column to take a maximum of")

    # "How many of them?" counts what is on screen. Stripping LIMIT unconditionally
    # counted the whole underlying result instead, so after a top-5 the answer was
    # 491 — every SKU with an outbound transaction. That is a real number and a
    # different question, and this is where the 491 in the reported session came
    # from.
    #
    # A LIMIT below the system cap is one the customer asked for ("top 5"), so it
    # is part of what "them" means and stays in the count. A LIMIT *at* the cap is
    # truncation the engine imposed, and there the honest answer is still the full
    # total — otherwise "how many" would report the page size.
    tree = sqlglot.parse_one(prior_sql, read="duckdb")
    requested_limit: int | None = None
    limit_node = tree.args.get("limit")
    if limit_node is not None:
        try:
            value = int(limit_node.expression.this)
            if value < MAX_LIMIT:
                requested_limit = value
        except (AttributeError, TypeError, ValueError):
            requested_limit = None

    if requested_limit is not None:
        # Keep the LIMIT so the count genuinely produces the number reported —
        # no hardcoded row standing in for SQL that would say something else.
        # ORDER BY still goes: it names a select alias, which the guardrail's
        # column allowlist cannot see inside a subquery, and which of the five
        # rows they are does not change how many there are.
        tree.set("order", None)
        inner = tree.sql(dialect="duckdb")
        return f"SELECT COUNT(*) AS followup_count FROM ({inner}) _prior", []

    tree.set("limit", None)
    tree.set("order", None)
    inner = tree.sql(dialect="duckdb")
    sql = f"SELECT COUNT(*) AS followup_count FROM ({inner}) _prior"
    if total_count is not None and total_exact and not wants_avg:
        # Prefer honest total when prior listing was truncated. Only when that
        # total is real: an inexact one is a lower bound, and replaying it here
        # would turn "at least 1000" into a confident "1000". Falling through
        # runs the COUNT for real instead.
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
    # ``grant_kind`` is stamped by stamp_grant on the way out, not here. Two
    # writers for one disclosure field is the same shape as the defect it
    # discloses.
    return base


# ── the engine ────────────────────────────────────────────────────────────────
def answer(
    question: str,
    *,
    session_id: str | None = None,
    space_id: str | None = None,
    verified: VerifiedManifest | None = None,
) -> dict[str, Any]:
    """Answer under exactly one grant, and say on the envelope which one.

    Kept deliberately thin. Grant resolution happens before any routing so no
    branch below can run without one, and the disclosure is stamped after every
    branch so no branch can omit it.
    """
    grant, kind, degraded = resolve_grant(session_id, verified)
    result = _answer(question, session_id=session_id, space_id=space_id, verified=grant)
    return stamp_grant(result, verified=grant, kind=kind, degraded=degraded)


def _answer(
    question: str,
    *,
    session_id: str | None,
    space_id: str | None,
    verified: VerifiedManifest,
) -> dict[str, Any]:
    from CortexOS.dms.query_service import (
        _infer_source_table,
        build_chart_spec,
        format_answer_with_insights,
        route_question,
        synthesize_answer,
    )
    skills = resolve_semantic_layer()

    audit_id = str(uuid.uuid4())
    route = route_question(question)

    # SEC-01 - there is no ungoverned execution path out of this function.
    # `verified` is resolved by the caller above and is never None here, so
    # every statement below reaches enforce_manifest. The parameter used to be
    # optional, and POST /dms/query - the demo and product path - passed
    # nothing, so the 34 fail-closed refusals and the hostile corpus guarded
    # only /v1/contract/*.

    if route == "blocked":
        return {
            "answer": "That operation is not permitted.", "sql_used": None, "chart_spec": None,
            "audit_id": audit_id, "violations_blocked": ["DDL_ATTEMPT"], "route": "blocked",
            "rows": [], "source_table": None, "layer": "blocked", "badge": "blocked",
            "assumptions": "destructive operation refused", "total_count": 0,
            "query_plan": _honest_plan(question, None, layer="blocked", assumptions="destructive"),
        }

    layer = badge = ""
    sql: str | None = None
    assumptions = ""
    metric_id: str | None = None
    metric_slots: dict[str, Any] = {}
    skill_score: float | None = None

    q_low = question.lower()
    prior = _SESSION.get(_session_key(session_id, space_id))

    # Session anaphora — "average of them" / "which of those are low stock"
    session_rows: list[dict[str, Any]] | None = None
    if _is_anaphora(q_low):
        if not (prior and prior.get("sql")):
            # Without a prior turn, fall-through to L0/L1 invents a different ask
            # from surface tokens ("average" → sales average). Abstain instead.
            return _abstain(
                question,
                audit_id,
                reason="follow-up needs a prior answer in this session",
                verified=verified,
            )
        try:
            if re.search(r"\b(low stock|below reorder|below\s+reorder)\b", q_low):
                sql, session_rows = _low_stock_over_prior(prior.get("rows") or [])
            elif _is_window_followup(q_low):
                sql, session_rows = _window_over_prior(
                    prior["sql"], question, prior.get("rows") or []
                )
            else:
                sql, session_rows = _aggregate_prior(
                    prior["sql"],
                    question,
                    prior.get("rows") or [],
                    total_count=prior.get("total_count"),
                    total_exact=bool(prior.get("total_count_exact", True)),
                )
            _check_followup_grant(sql, prior, verified)
            layer, badge = "session", "session"
            assumptions = f"follow-up over prior turn ({prior.get('metric_id') or prior.get('layer')})"
            metric_id = prior.get("metric_id")
        except FollowupUnsupported as exc:
            # The customer named an aggregation over the answer in front of them.
            # Falling through would hand the question to the keyword router,
            # which matches on surface tokens and would answer a different one.
            return _abstain(
                question, audit_id, reason=f"follow-up not supported: {exc}", verified=verified
            )
        except ManifestError as exc:
            return _abstain(question, audit_id, reason=str(exc), verified=verified)
        except Exception as exc:  # noqa: BLE001
            # Never re-route a recognised follow-up — that widens past the prior grant.
            return _abstain(
                question, audit_id, reason=f"follow-up could not be computed: {exc}",
                verified=verified,
            )

    # L0 certified → L1 metric → L-skill → L3 abstain
    # Skills run after governed routes so golden/certified paths stay authoritative.
    if sql is None:
        cq = match_certified(question)
        if cq is not None:
            sql, layer, badge = cq.sql, "certified", "certified"
            assumptions = f"certified query {cq.id}"
            metric_id = cq.id
        else:
            # Say *why*, before falling through to a generic abstain. The
            # refusal itself lives in route_to_metric so no caller can miss it;
            # this is the half the customer reads, and it names the two-step
            # path that does work rather than just declining (ANS-02, R-0005).
            over_ranking = _aggregate_over_ranking(question)
            if over_ranking is not None:
                return _abstain(question, audit_id, reason=over_ranking, verified=verified)
            plan = route_to_metric(question)
            if plan is not None:
                if plan.metric_id == "_exclusion_clarify":
                    clarify = dict(plan.slots.get("clarify") or {})
                    limit = int(plan.slots.get("limit") or 5)
                    return _clarify_exclusion(
                        question, audit_id, clarify=clarify, limit=limit
                    )

                try:
                    sql = resolve_semantic_layer().compile_metric(
                        plan.metric_id, plan.slots
                    )
                    layer, badge = "governed_metric", "governed_metric"
                    assumptions = plan.reason
                    metric_id = plan.metric_id
                    metric_slots = dict(plan.slots)
                except SemanticCompileError as exc:
                    # Exclusion resolve failures must not fall through to query-skill.
                    if _exclusion_clauses(question):
                        exact, clarify = _resolve_exclusions(question)
                        if clarify is not None:
                            return _clarify_exclusion(
                                question,
                                audit_id,
                                clarify=clarify,
                                limit=int(_sales_rank_slots(question).get("limit") or 5),
                            )
                        return _abstain(
                            question,
                            audit_id,
                            reason=f"could not resolve inputs: {exc}",
                            verified=verified,
                        )
                    return _abstain(
                        question,
                        audit_id,
                        reason=f"could not resolve inputs: {exc}",
                        verified=verified,
                    )

    # Predictive / hypothetical asks must not be answered by a similar query skill
    # (e.g. "forecast … top SKU" matching a captured sales_by_value skill).
    if sql is None and _is_predictive(q_low):
        return _abstain(
            question,
            audit_id,
            reason="predictive / out-of-scope — no verified forecast path",
            verified=verified,
        )

    # META-01 — metadata / browse intent before query-skill replay or L2 generation.
    if sql is None:

        if resolve_semantic_layer().is_catalog_intent(question):
            return _catalog_response(question, audit_id)

    if sql is None:
        # Exclusion verb present but unresolved → abstain (never query-skill replay).
        if _exclusion_clauses(question) and _wants_sales_rank(_normalize(question), question):
            exact, clarify = _resolve_exclusions(question)
            if clarify is not None:
                return _clarify_exclusion(
                    question,
                    audit_id,
                    clarify=clarify,
                    limit=int(_sales_rank_slots(question).get("limit") or 5),
                )
            if not exact:
                return _abstain(
                    question,
                    audit_id,
                    reason="exclusion could not be resolved to a warehouse SKU",
                    verified=verified,
                )
        hit = skills.find_skill(question)
        # A capture is matched by similarity, so a question naming one SKU sits
        # right next to a stored population-level skill — "total revenue for
        # SKU-00397" is a near neighbour of "total revenue". Replaying that would
        # report the whole warehouse's number while the customer reads it as that
        # SKU's. A capture that actually mentions the SKU is still fair game; L2
        # generation below can answer the rest properly, or the path abstains.
        if hit is not None and (_named_sku := _names_specific_sku(question)):
            captured = f"{hit.get('sql_template') or ''} {hit.get('params') or ''}".upper()
            if _named_sku not in captured:
                hit = None
        if hit is not None:
            skill_score = float(hit["score"])
            if hit.get("metric_id"):

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
                    clarify = params.pop("_exclusion_clarify", None)
                    if clarify is not None:
                        return _clarify_exclusion(
                            question,
                            audit_id,
                            clarify=clarify,
                            limit=int(params.get("limit") or 5),
                        )
                else:
                    params = {k: v for k, v in stored.items() if k not in contextual}
                try:
                    sql = resolve_semantic_layer().compile_metric(
                        hit["metric_id"], params
                    )
                    layer, badge = "query_skill", "query_skill"
                    assumptions = f"query skill match score={skill_score:.3f} → {hit['metric_id']}"
                    metric_id = hit["metric_id"]
                    metric_slots = dict(params)
                except SemanticCompileError:
                    sql = None
            elif hit.get("sql_template"):
                sql = hit["sql_template"]
                layer, badge = "query_skill", "query_skill"
                assumptions = f"query skill match score={skill_score:.3f} (stored sql)"

    if sql is None:
        # C7-full L2: schema retrieval → FreeRoute generate → validate gate.
        # Never fall back to the L1 keyword cascade or a smaller model.
        if os.environ.get("DMS_L2_ENABLED", "").lower() in ("1", "true", "yes"):
            # The provider comes from the active pack through the engine-owned port
            # (CortexOS/dms/sql_generation_port.py). The engine keeps the gate: a
            # pack proposes SQL, only sql_validate_gate decides whether it may run.
            from CortexOS.dms.sql_generation_port import (
                SqlGenerationNotRegistered,
                resolve_sql_generation,
            )
            from CortexOS.dms.sql_validate_gate import SqlGateAbstain, gate_with_retry

            try:
                l2 = resolve_sql_generation()
            except SqlGenerationNotRegistered:
                return _abstain_or_document_rag(
                    question,
                    audit_id,
                    reason="no verified answer path (L2 provider absent)",
                    space_id=space_id,
                    verified=verified,
                )

            if not l2.is_configured():
                return _abstain_or_document_rag(
                    question,
                    audit_id,
                    reason="no verified answer path (L2 not wired)",
                    space_id=space_id,
                    verified=verified,
                )

            semantic_early = load_semantic_layer()
            reduced = l2.retrieve_schema(question)
            prior_box: dict[str, list[str]] = {"v": []}

            def _gen(prior: list[str]) -> list[str]:
                # Every candidate, not just the best one: the gate tries them all
                # before spending another generation round.
                prior_box["v"] = list(prior)
                return l2.generate_candidates(
                    question,
                    reduced,
                    prior_violations=prior,
                )

            # EXPLAIN is the only stage that can catch a column the model invented
            # but that the parser accepts, so generated SQL gets it on *both*
            # paths. It used to be opened only when ``verified is None``, which
            # left the live contract path — the customer path — dry-running
            # nothing until submit.execute_sql, far too late for the retry loop
            # to feed the error back into the next prompt.
            #
            # Read-only unconditionally, matching submit.execute_sql: every
            # statement here has already passed the read-only guardrail, and a
            # writer holding the file must not turn into an abstain.
            con_explain = None
            explain_note = ""
            try:
                try:
                    con_explain = get_connection(DEFAULT_DB, read_only=True)
                except Exception as exc:  # noqa: BLE001
                    # Not silent: the degradation rides out on the answer's
                    # assumptions. execute_sql still EXPLAINs post-enforce and
                    # fails closed, so the query stays gated — what is lost is
                    # the ability to retry on the error, not the check itself.
                    explain_note = f"; EXPLAIN deferred to execute ({str(exc)[:80]})"
                gate = gate_with_retry(
                    _gen,
                    question,
                    semantic_early,
                    con=con_explain,
                    max_retries=2,
                    require_explain=con_explain is not None,
                )
            except SqlGateAbstain as exc:
                return _abstain_or_document_rag(
                    question,
                    audit_id,
                    reason=f"L2 generation failed validation gate: {exc}",
                    space_id=space_id,
                    verified=verified,
                )
            finally:
                if con_explain is not None:
                    con_explain.close()

            if not gate.passed or not gate.safe_sql:
                return _abstain_or_document_rag(
                    question,
                    audit_id,
                    reason="L2 generation failed validation gate",
                    space_id=space_id,
                    verified=verified,
                )

            sql = gate.safe_sql
            layer, badge = "generated", "L2_VALIDATED"
            assumptions = (
                f"L2 FreeRoute SQL over reduced schema "
                f"tables={list((reduced.get('tables') or {}).keys())}"
                f"; attempts={gate.attempts}"
                f"; explain={'ran' if gate.explain_ran else 'skipped'}"
                f"{explain_note}"
            )
            l2.record_validated(question, sql)  # provider swallows its own failures
        else:
            return _abstain_or_document_rag(
                question,
                audit_id,
                reason="no governed metric or certified query matched",
                space_id=space_id,
                verified=verified,
            )

    if sql is None:
        return _abstain_or_document_rag(
            question,
            audit_id,
            reason="no governed metric or certified query matched",
            space_id=space_id,
            verified=verified,
        )

    # P0-DEMO-02 — the plan has to name the tables it reads, and they have to be
    # sources this session actually bound. Until here the router has only ever
    # asked "which metric do these words look like?", so "the total amount in
    # the Q3 sales export" compiles happily against `transactions` — Netie's own
    # demo warehouse — and ships a confident number about data the customer has
    # never seen, badged governed_metric.
    #
    # enforce_manifest already refuses this, but it refuses by raising from
    # inside execution, which the ask route turns into an HTTP error. A refusal
    # the customer reads as a crash teaches them the product is broken; a
    # refusal that names what it *can* answer teaches them how to ask. The gate
    # below is the legible one; the enforcer stays the boundary that holds.
    ungrounded = ungrounded_tables(sql, verified=verified)
    if ungrounded:
        return _abstain_ungrounded(question, audit_id, ungrounded=ungrounded, verified=verified)

    semantic = load_semantic_layer()
    # One execution path, always through the C4 submit executor, which calls
    # enforce_manifest. The bare-connection branch that used to live here (taken
    # whenever no manifest was bound) is deleted rather than left unreachable —
    # an ungoverned executor kept "just in case" is the bypass, whether or not
    # anything currently reaches it.
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
            return _abstain(
                question,
                audit_id,
                reason=f"SQL validation gate: {exc}",
                verified=verified,
            )
        except ManifestError as exc:
            # The enforcer refused. Report it as the refusal it is, with the
            # type preserved, instead of letting it leave as an exception the
            # product path turns into a 500.
            entry.passed = False
            entry.violations = [type(exc).__name__]
            log_audit(entry)
            return _abstain_refused(question, audit_id, exc=exc)
        total_count = _true_count(gate.safe_sql, verified=verified)
        entry.row_count = len(rows)
        log_audit(entry)

    if not guard_result.passed:
        return _abstain(
            question,
            audit_id,
            reason=f"internal SQL failed guardrail {guard_result.violations}",
            verified=verified,
        )

    # Plausibility — the stage after EXPLAIN in the C7 pipeline (CLAUDE.md §8).
    # Parsing and planning both succeed on `WHERE sku = 'BETA'` against a
    # warehouse that stores SKU-BETA; what comes back is an empty result the
    # customer reads as a real zero. Probe the filters instead of trusting them.
    #
    # Generated SQL is probed every turn because nothing else verifies it. A
    # query skill is a replay of a capture that passed once, so it is probed only
    # when the result is empty — the way a stale value presents.
    if layer in sql_plausibility.PROBED_LAYERS and (layer == "generated" or not rows):
        plausible = sql_plausibility.check(
            guard_result.safe_sql or sql,
            _plausibility_runner(verified),
            layer=layer,
        )
        if not plausible.ok:
            return _abstain_impossible_filter(question, audit_id, result=plausible)
        if plausible.skipped_reason:
            assumptions = f"{assumptions}; plausibility not checked ({plausible.skipped_reason})"
        elif plausible.probed:
            assumptions = f"{assumptions}; {plausible.probed} filter value(s) verified present"

    # A capped listing whose COUNT probe failed used to report total_count =
    # len(rows) = exactly 1000, with truncated=False because total_count was
    # None — so nothing disclosed the cap and the customer read a fabricated
    # exact total. The real number could be 50,000, and a follow-up "how many of
    # them?" replayed the same invented 1000.
    #
    # len(rows) is only the true total when the cap was NOT reached. At the cap
    # with no count, all that is known is "at least this many", and the answer
    # says so rather than picking a number.
    count_exact = total_count is not None or len(rows) < MAX_LIMIT
    capped = len(rows) >= MAX_LIMIT
    truncated = capped and (total_count is None or total_count > len(rows))

    answer_text = synthesize_answer(rows, question)
    if truncated and count_exact:
        answer_text = f"{total_count} rows match; showing the first {len(rows)}.\n" + answer_text
    elif truncated:
        answer_text = (
            f"More than {len(rows)} rows match; showing the first {len(rows)}. "
            "The exact total could not be computed for this query.\n"
        ) + answer_text
    # INS-01: secondary pass — bullets cite only values already in ``rows``.
    answer_text = format_answer_with_insights(answer_text, rows)

    # Remember last successful turn for follow-ups (scoped to Space)
    _remember(
        session_id,
        {
            "question": question,
            "sql": guard_result.safe_sql,
            "metric_id": metric_id,
            "layer": layer,
            "rows": rows[:50],
            "total_count": total_count if total_count is not None else len(rows),
            # Whether that total is the real one. A follow-up count must not
            # replay a lower bound as though it were exact.
            "total_count_exact": count_exact,
            "source_table": _infer_source_table(sql),
            "space_id": (space_id or "").strip() or None,
        },
        space_id=space_id,
        verified=verified,
    )

    # Graduate successful non-session answers into the skill store
    if layer in ("certified", "governed_metric", "query_skill"):
        skills.capture_skill(
            question,
            metric_id=metric_id if layer != "certified" else None,
            params=metric_slots,
            sql=guard_result.safe_sql,
            layer=layer,
        )

    return {
        "answer": answer_text,
        "sql_used": guard_result.safe_sql,
        "chart_spec": build_chart_spec(rows, question),
        "audit_id": audit_id,
        "violations_blocked": [],
        "route": "sql",
        "row_count": len(rows),
        "rows": rows,
        "total_count": total_count if total_count is not None else len(rows),
        # False means total_count is a lower bound, not the answer. The UI must
        # render it as "1000+" rather than "1000".
        "total_count_exact": count_exact,
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
    }
