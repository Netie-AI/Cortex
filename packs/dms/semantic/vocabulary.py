"""Q1 — enterprise vocabulary normalization in front of the L1 metric router.

The L1 router (`answer_engine.route_to_metric`) matches hand-written regex
against the raw question, so it only recognises the words its author happened to
type. Measured on `bench.paraphrase`, that is the dominant cause of abstention:
an analyst who asks "what is running low in warehouse A" instead of "which SKUs
are below reorder level in warehouse A" gets no answer at all.

This module maps how people actually talk to the vocabulary the router knows.
It is the same idea as the `synonyms:` field already declared on every metric in
`metrics.yaml` (and, until now, never read by anything) and as Snowflake Cortex
Analyst's semantic-model synonyms.

Three rules keep this safe, and each is enforced by a test:

  1. **Routing only.** The normalized string is used *solely* to pick a metric.
     Slot extraction (limits, thresholds, directions, locations, day windows),
     session anaphora, provenance and the audit record all keep the original
     question. Normalization can therefore never change a NUMBER or an ENTITY.

  2. **Additive, never contradictory.** A rule may only introduce vocabulary the
     router understands. No rule may rewrite a comparison ("above"/"below"),
     a direction ("most"/"least") or a time window, because those decide which
     way an answer points.

  3. **Precision is not allowed to fall.** Widening recall must not convert
     abstentions into confidently wrong answers. `bench.paraphrase` gates this:
     answered precision must stay at 100%.
"""
from __future__ import annotations

import re

# (pattern, replacement) applied in order to a lowercased copy of the question.
# Each entry maps *business* phrasing onto *router* phrasing. Keep every rule
# unambiguous inside the warehouse domain — a rule that could mean two things
# belongs in the certified-query repo, not here.
_RULES: tuple[tuple[str, str], ...] = (
    # ── shipments ──────────────────────────────────────────────────────────
    (r"\b(?:running late|behind schedule|late deliveries|late shipments|"
     r"late inbound|overdue deliveries)\b", "delayed shipments"),
    (r"\blate\s+(?:inbound\s+)?(?:loads|arrivals|consignments|orders)\b", "delayed shipments"),
    (r"\bconsignments?\b", "shipments"),
    # noun -> the adjective the router matches; "delays"/"delay" only, never
    # "delayed" (already router vocabulary).
    (r"\bdelays\b", "delayed shipments"),
    (r"\bdelay\b(?!ed)", "delayed"),
    (r"\bfreight\s+compan(?:y|ies)\b", "carrier"),
    (r"\blogistics\s+providers?\b", "carrier"),
    (r"\bfreight spend\b", "shipment cost"),
    (r"\bshipping cost\b", "shipment cost"),
    (r"\b(?:on the road|currently moving|in motion)\b", "in transit shipments"),
    (r"\b(?:lands|arrives|arrive)\s+this\s+week\b", "arriving this week"),
    (r"\bdue\s+in\s+the\s+next\s+seven\s+days\b", "arriving in 7 days"),
    (r"\bdrop-?\s?off\s+points?\b", "destination"),
    (r"\binbound\s+loads?\b", "incoming shipments"),

    # ── suppliers ──────────────────────────────────────────────────────────
    (r"\bvendors?\b", "suppliers"),
    (r"\bprocurement\s+spend\b", "total spend"),
    (r"\b(?:still\s+)?owe us\b", "pending"),

    # ── inventory ──────────────────────────────────────────────────────────
    (r"\b(?:running low|needs? replenishment|need(?:s|ing)? reordering|"
     r"needs? reorder|under (?:their |the )?reorder point|"
     r"stock(?:s)? (?:running )?thin|getting low on stock)\b", "below reorder level"),
    (r"\bwhat(?:'s| is) low in\b", "below reorder level in"),
    (r"\bsku(?:s)? (?:that are )?scarce\b", "below reorder level"),
    (r"\breorder\s+point\b", "reorder level"),
    (r"\b(?:past (?:its |their )?expiry(?: date)?|past shelf life|out of date|"
     r"gone past (?:its |their )?expiry)\b", "expired"),
    (r"\b(?:topped up|top(?:ped)? up)\b", "restocked"),
    (r"\bgone stale\b", "not restocked"),
    (r"\bchemical products?\b", "chemicals"),

    # ── capacity / sites ───────────────────────────────────────────────────
    (r"\b(?:refrigerated|chilled|chiller)\b", "cold storage"),
    (r"\b(?:spare space|room left|free space|spare capacity)\b", "free capacity"),
    (r"\bhow full\b", "capacity utilisation"),
    (r"\b(?:space usage|site by site space)\b", "capacity utilisation"),
    # The router's branch needs BOTH "capacit" and the utilisation word. The
    # lookbehind keeps this idempotent — "capacity utilisation" is left alone.
    (r"(?<!capacity )\butili[sz]ation\b", "capacity utilisation"),
    (r"\bnearly full\b", "capacity above 90 percent"),
    # Singular on purpose: the router matches \b(warehouse|destination|location)\b,
    # so "locations" would slip past the per-site breakdown rule and fall through
    # to a flat status listing.
    (r"\bsites?\b", "location"),

    # ── alerts / assets ────────────────────────────────────────────────────
    (r"\b(?:open warnings|warnings|currently flagged|flagged)\b", "active alerts"),
    (r"\bcamera feed\b", "cctv"),

    # ── measures ───────────────────────────────────────────────────────────
    # Volume rank looks for \b(quantity|volume|kg|units?)\b. "kilograms" and
    # "by weight" are the same ask; without this they compile as sales_by_value
    # (confidently wrong).
    (r"\bkilograms?\b", "kg"),
    (r"\bby weight\b", "by kg"),
    (r"\b(?:typical|mean)\b", "average"),
    (r"\btrailing month\b", "last 30 days"),
    (r"\b(?:past|last)\s+thirty\s+days\b", "last 30 days"),
    (r"\bworth\b", "stock value"),
    (r"\bturn(?:ed)?\s+over\b", "turnover"),
)

_COMPILED: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.I), replacement) for pattern, replacement in _RULES
)

# Words whose meaning must survive normalization untouched. Asserted by test:
# no rule may add, remove or flip any of these.
INVARIANT_TOKENS: frozenset[str] = frozenset({
    "above", "below", "over", "under", "more", "less", "greater", "fewer",
    "most", "least", "highest", "lowest", "top", "bottom", "best", "worst",
    "not", "no", "never", "average", "total", "count",
})


def normalize_for_routing(question: str) -> str:
    """Return the question rewritten into router vocabulary.

    The result is for METRIC SELECTION ONLY. Never feed it to slot extraction,
    to the audit record, or back to the user — the original question is the
    record of what was asked.
    """
    text = (question or "").lower()
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip()


def explain(question: str) -> list[str]:
    """Which rules fired — for the assumptions line and for debugging."""
    text = (question or "").lower()
    fired: list[str] = []
    for pattern, replacement in _COMPILED:
        if pattern.search(text):
            fired.append(f"{pattern.pattern} -> {replacement}")
            text = pattern.sub(replacement, text)
    return fired
