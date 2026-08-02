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
from CortexOS.execution.manifest import ManifestError, PathNotAllowed, VerifiedManifest

# Reused from the existing service (loaded lazily to avoid import cycle at module load).
ABSTAIN = "needs_clarification"


@dataclass(slots=True)
class MetricPlan:
    metric_id: str
    slots: dict[str, Any]
    reason: str


# ── normalization + certified index ──────────────────────────────────────────
def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _certified_index() -> dict[str, Any]:
    from packs.dms.semantic.loader import load_all

    model = load_all()
    return {_normalize(cq.question): cq for cq in model.certified}


def match_certified(question: str):
    """L0 — EXACT normalized match only (high precision; never fuzzy, so a
    scoped question can't collide with an unscoped certified query)."""
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
    from packs.dms.semantic import values as vd

    res = vd.resolve(question, "location_code")
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


def _exclusion_clauses(q: str) -> list[str]:
    """Raw exclusion phrases (before token split), for sku_name fuzzy resolve."""
    out: list[str] = []
    for m in _EXCLUSION_VERB_RE.finditer(q):
        clause = m.group(1)
        stop = _EXCLUSION_STOP.search(clause)
        if stop:
            clause = clause[: stop.start()]
        clause = clause.strip().strip("'\".,")
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
    from packs.dms.semantic import values as valuedict

    clauses = _exclusion_clauses(q_raw)
    if not clauses:
        return [], None

    exact: list[str] = []
    fuzzy: list[tuple[str, str, float]] = []  # (phrase, sku, conf)
    failed: list[str] = []

    for clause in clauses:
        # Prefer whole-phrase resolve (sku_name / multi-token), then per-token.
        res = valuedict.resolve(clause, "sku")
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
            tres = valuedict.resolve(tok, "sku")
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
    from packs.dms.semantic.vocabulary import normalize_for_routing

    q_raw = question.lower()
    q = normalize_for_routing(question)

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
    if not seen:  # cold: offer a stable default trio
        seen = [
            "Top 5 selling SKUs by revenue",
            "Which SKUs are below reorder level in warehouse A?",
            "Show warehouse capacity utilisation",
        ][:limit]
    return seen


def _plausibility_runner(verified: VerifiedManifest | None) -> sql_plausibility.Runner:
    """A bounded probe executor for whichever execution path this turn is on.

    The contract path routes probes through ``execute_sql`` so the manifest
    applies: a value this session may not read should probe as absent, and the
    abstain that follows says "no such value" without disclosing that one exists.
    """
    if verified is not None:

        def _contract(probe_sql: str) -> list[dict[str, Any]]:
            from CortexOS.execution.submit import execute_sql

            rows, _, _ = execute_sql(verified, probe_sql)
            return rows

        return _contract

    def _legacy(probe_sql: str) -> list[dict[str, Any]]:
        con = get_connection(DEFAULT_DB, read_only=True)
        try:
            rel = con.execute(probe_sql)
            columns = [d[0] for d in rel.description] if rel.description else []
            return [dict(zip(columns, row, strict=False)) for row in rel.fetchall()]
        finally:
            con.close()

    return _legacy


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


def _abstain(question: str, audit_id: str, *, reason: str) -> dict[str, Any]:
    suggestions = _suggestions(question)
    hint = " Try: " + " · ".join(f'"{s}"' for s in suggestions)
    return {
        "answer": (f"I can't answer that from the DMS semantic layer with confidence ({reason})."
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
    r"\b(their|its)\s+(average|avg|mean|median|total|sum|count)\b", re.I
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
            r"\b(average|avg|mean|median|total|sum|count|how many)\s+of\s+(them|those|these|it)\b|"
            r"\bwhat is the (average|median) of (them|those|these|it)\b|"
            r"\baverage of (them|those|these)\b|"
            r"\bmedian of (them|those|these)\b|"
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
    return base


# ── the engine ────────────────────────────────────────────────────────────────
def answer(
    question: str,
    *,
    session_id: str | None = None,
    space_id: str | None = None,
    verified: VerifiedManifest | None = None,
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
    route = route_question(question)

    if route == "blocked":
        return {
            "answer": "That operation is not permitted.", "sql_used": None, "chart_spec": None,
            "audit_id": audit_id, "violations_blocked": ["DDL_ATTEMPT"], "route": "blocked",
            "rows": [], "source_table": None, "layer": "blocked", "badge": "blocked",
            "assumptions": "destructive operation refused", "total_count": 0,
            "query_plan": _honest_plan(question, None, layer="blocked", assumptions="destructive"),
        }
    if route == "rag":
        ans, sources = rag_answer(question)
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

    q_low = question.lower()
    prior = _SESSION.get(_session_key(session_id, space_id))

    # Session anaphora — "average of them" / "which of those are low stock"
    session_rows: list[dict[str, Any]] | None = None
    if prior and prior.get("sql") and _is_anaphora(q_low):
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
            return _abstain(question, audit_id, reason=f"follow-up not supported: {exc}")
        except ManifestError as exc:
            return _abstain(question, audit_id, reason=str(exc))
        except Exception as exc:  # noqa: BLE001
            # Never re-route a recognised follow-up — that widens past the prior grant.
            return _abstain(
                question, audit_id, reason=f"follow-up could not be computed: {exc}"
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
            plan = route_to_metric(question)
            if plan is not None:
                if plan.metric_id == "_exclusion_clarify":
                    clarify = dict(plan.slots.get("clarify") or {})
                    limit = int(plan.slots.get("limit") or 5)
                    return _clarify_exclusion(
                        question, audit_id, clarify=clarify, limit=limit
                    )
                from packs.dms.semantic.loader import SemanticError, compile_metric, load_all

                try:
                    sql = compile_metric(load_all(), plan.metric_id, plan.slots)
                    layer, badge = "governed_metric", "governed_metric"
                    assumptions = plan.reason
                    metric_id = plan.metric_id
                    metric_slots = dict(plan.slots)
                except SemanticError as exc:
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
                        )
                    return _abstain(question, audit_id, reason=f"could not resolve inputs: {exc}")

    # Predictive / hypothetical asks must not be answered by a similar query skill
    # (e.g. "forecast … top SKU" matching a captured sales_by_value skill).
    if sql is None and _is_predictive(q_low):
        return _abstain(
            question,
            audit_id,
            reason="predictive / out-of-scope — no verified forecast path",
        )

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
                )
        hit = query_skills.find(question)
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
                    sql = compile_metric(load_all(), hit["metric_id"], params)
                    layer, badge = "query_skill", "query_skill"
                    assumptions = f"query skill match score={skill_score:.3f} → {hit['metric_id']}"
                    metric_id = hit["metric_id"]
                    metric_slots = dict(params)
                except SemanticError:
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
                return _abstain(
                    question, audit_id, reason="no verified answer path (L2 provider absent)"
                )

            if not l2.is_configured():
                return _abstain(question, audit_id, reason="no verified answer path (L2 not wired)")

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
                return _abstain(
                    question,
                    audit_id,
                    reason=f"L2 generation failed validation gate: {exc}",
                )
            finally:
                if con_explain is not None:
                    con_explain.close()

            if not gate.passed or not gate.safe_sql:
                return _abstain(question, audit_id, reason="L2 generation failed validation gate")

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
            return _abstain(question, audit_id, reason="no governed metric or certified query matched")

    if sql is None:
        return _abstain(question, audit_id, reason="no governed metric or certified query matched")

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
                return _abstain(
                    question,
                    audit_id,
                    reason=f"SQL validation gate: {exc}",
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
        return _abstain(question, audit_id,
                        reason=f"internal SQL failed guardrail {guard_result.violations}")

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
        query_skills.capture(
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
