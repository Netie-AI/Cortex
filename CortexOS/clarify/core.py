"""CLARIFY (#307): an ambiguous ask gets one stamped question with 2-5 options.

Detection is deterministic and catalog-driven. No model is called here. The
answer plane is reached only through the injected :data:`Router`, so this
module never opens a database, never imports a pack, and never answers.

An ask is ambiguous when the engine would otherwise have to guess:

* ``time_window``: the router picked a metric, the ask says "recently" / "lately"
  / "soon" and names no window, so a windowed metric would run on a default.
* ``metric``: the router found no metric, and a term in the ask names 2-5
  metrics that no declared phrase in the ask narrows to one.
* ``term_column``: an ontology term maps to several columns and the ask names
  none of their tables or columns.

Every option is mapped to a concrete interpretation. Metric and time-window
options are kept only when the router sends their ``resolved_question`` to
exactly that interpretation, so choosing one cannot ask the same thing again.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cached_property
from typing import Any

from cortex_contract.answer import AmbiguityType, Clarify, ClarifyOption

SERVED_BY = "cortex:clarify"
MAX_OPTIONS = 5
WINDOW_DAYS = (7, 30, 90)

# Shape, time and function words: they say how to ask, not what is asked about.
_GENERIC = frozenset(
    """
    a an the and or of by per in on at to for with from into is are was were be
    do does did we our my me you it its what which who how many much count
    number list show give get find top bottom most least more less total overall
    all any each last next this that these those previous past month week day
    days year time over above below under high higher highest low lower lowest
    need needing
    """.split()
)

_VAGUE_WINDOW = re.compile(
    r"\b(recently|recent|lately|of late|nowadays|these days|soon|past few|last few)\b",
    re.I,
)
_EXPLICIT_WINDOW = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|a|an)\s*"
    r"(day|week|month|quarter|year)s?\b"
    r"|\b(today|yesterday|tonight|ytd|year to date|all[- ]time|ever)\b"
    r"|\b(this|last|previous|prior|next|past)\s+(week|month|quarter|year)\b",
    re.I,
)


class ClarifyInvalid(ValueError):
    """A clarify question without 2-5 options or a named ambiguity type."""


@dataclass(frozen=True)
class MetricEntry:
    id: str
    synonyms: tuple[str, ...]
    windowed: bool = False


@dataclass(frozen=True)
class TermColumn:
    term: str
    table: str
    column: str


@dataclass(frozen=True)
class Catalog:
    """What the ontology says the asker could mean. Built once, read-only."""

    metrics: tuple[MetricEntry, ...]
    # Names of things (tables, mapped columns, by/per dimensions). Never a
    # metric head: "list suppliers" names a table, not a choice of metrics.
    entity_terms: frozenset[str] = frozenset()
    term_columns: tuple[TermColumn, ...] = ()

    def metric(self, metric_id: str) -> MetricEntry | None:
        return next((m for m in self.metrics if m.id == metric_id), None)

    @cached_property
    def heads(self) -> dict[str, tuple[str, ...]]:
        """Term -> metric ids, for terms that 2..MAX_OPTIONS metrics share."""
        found: dict[str, list[str]] = {}
        for metric in self.metrics:
            for token in sorted(_subject_tokens(metric, self.entity_terms)):
                found.setdefault(token, []).append(metric.id)
        return {t: tuple(ids) for t, ids in found.items() if 2 <= len(ids) <= MAX_OPTIONS}

    @cached_property
    def phrases(self) -> tuple[str, ...]:
        return tuple(p for m in self.metrics for p in (_normalize(s) for s in m.synonyms) if p)

    @cached_property
    def term_groups(self) -> dict[str, tuple[TermColumn, ...]]:
        groups: dict[str, dict[tuple[str, str], TermColumn]] = {}
        for tc in self.term_columns:
            groups.setdefault(_normalize(tc.term), {})[(tc.table, tc.column)] = tc
        return {t: tuple(cols.values()) for t, cols in groups.items() if t and len(cols) >= 2}

    @cached_property
    def digest(self) -> str:
        body = {
            "metrics": [[m.id, list(m.synonyms), m.windowed] for m in self.metrics],
            "entity_terms": sorted(self.entity_terms),
            "term_columns": [[t.term, t.table, t.column] for t in self.term_columns],
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "sha256:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Routed:
    """What the deterministic router would do with an ask.

    ``settled`` means the engine never guesses on it (certified replay, a
    refusal, an unknown subject); a settled ask is never clarified.
    """

    metric_id: str | None = None
    slots: Mapping[str, Any] = field(default_factory=dict)
    settled: bool = False


Router = Callable[[str], Routed]


@dataclass(frozen=True)
class Turn:
    """One ask after the chosen options are applied.

    ``clarify`` set: serve it instead of an answer. Otherwise answer
    ``question``; ``applied`` lists the option ids that produced it.
    """

    question: str
    applied: tuple[str, ...] = ()
    clarify: Clarify | None = None


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _forms(word: str) -> set[str]:
    return {word, word + "s", word[:-1] if word.endswith("s") else word}


def _subject_tokens(metric: MetricEntry, entities: frozenset[str]) -> set[str]:
    tokens = {t for s in metric.synonyms for t in _normalize(s).split()}
    return {t for t in tokens if t not in _GENERIC and t not in entities and not t.isdigit()}


def _option_id(kind: AmbiguityType, term: str, interpretation: Mapping[str, str]) -> str:
    raw = json.dumps([kind.value, term, dict(interpretation)], sort_keys=True)
    return "opt_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _option(
    kind: AmbiguityType, term: str, interpretation: Mapping[str, str], *, label: str, resolved: str
) -> ClarifyOption:
    return ClarifyOption(
        id=_option_id(kind, term, interpretation),
        label=label,
        resolved_question=resolved,
        interpretation=dict(interpretation),
    )


def ensure_valid(clarify: Clarify) -> Clarify:
    """Refuse a clarify that would leave the asker nothing concrete to pick."""
    if not isinstance(clarify.ambiguity_type, AmbiguityType):
        raise ClarifyInvalid("clarify has no ambiguity_type")
    options = list(clarify.options or [])
    if not 2 <= len(options) <= MAX_OPTIONS:
        raise ClarifyInvalid(f"clarify carries {len(options)} options; it needs 2-{MAX_OPTIONS}")
    if len({o.id for o in options}) != len(options):
        raise ClarifyInvalid("clarify option ids are not unique")
    if any(not o.resolved_question or not o.interpretation for o in options):
        raise ClarifyInvalid("every clarify option maps to a resolvable interpretation")
    return clarify


def _clarify(
    kind: AmbiguityType,
    *,
    term: str,
    question: str,
    options: list[ClarifyOption],
    reason: str,
    catalog: Catalog,
) -> Clarify | None:
    if len(options) < 2:
        return None
    clarify = Clarify.model_construct(
        question=question,
        ambiguity_type=kind,
        term=term,
        options=options,
        served_by=SERVED_BY,
        served_at=datetime.now(timezone.utc).isoformat(),
        served_reason=reason,
        served_catalog=catalog.digest,
    )
    return Clarify.model_validate(ensure_valid(clarify).model_dump())


def _settled(routed: Routed) -> bool:
    return routed.settled


def _anchored(q_norm: str, routed: Routed, catalog: Catalog) -> bool:
    """The router or a declared metric phrase already picks one metric."""
    if routed.metric_id is not None:
        return True
    padded = f" {q_norm} "
    return any(f" {phrase} " in padded for phrase in catalog.phrases)


def _windowed_target(metric_id: str, catalog: Catalog) -> MetricEntry | None:
    metric = catalog.metric(metric_id)
    if metric is None:
        return None
    if metric.windowed:
        return metric
    mine = _subject_tokens(metric, catalog.entity_terms)
    siblings = [
        m
        for m in catalog.metrics
        if m.windowed and mine & _subject_tokens(m, catalog.entity_terms)
    ]
    return siblings[0] if len(siblings) == 1 else None


def _without(question: str, match: re.Match[str]) -> str:
    text = question[: match.start()] + question[match.end() :]
    return " ".join(text.split()).rstrip(" ?.!")


def _time_window(question: str, routed: Routed, catalog: Catalog, route: Router) -> Clarify | None:
    if routed.metric_id is None:
        return None
    vague = _VAGUE_WINDOW.search(question)
    if vague is None or _EXPLICIT_WINDOW.search(question):
        return None
    target = _windowed_target(routed.metric_id, catalog)
    if target is None:
        return None
    base = _without(question, vague)
    term = vague.group(0).lower()
    options: list[ClarifyOption] = []
    for days in WINDOW_DAYS:
        resolved = f"{base} within {days} days" if base else f"within {days} days"
        hit = route(resolved)
        if hit.settled or hit.metric_id != target.id or str(hit.slots.get("days")) != str(days):
            continue
        options.append(
            _option(
                AmbiguityType.TIME_WINDOW,
                term,
                {"metric_id": target.id, "days": str(days)},
                label=f"Within {days} days",
                resolved=resolved,
            )
        )
    return _clarify(
        AmbiguityType.TIME_WINDOW,
        term=term,
        question=f"Which time window do you mean by '{term}'?",
        options=options,
        reason=f"'{term}' names no window; {target.id} would run on its default window",
        catalog=catalog,
    )


def _verified_phrase(metric: MetricEntry, route: Router) -> str | None:
    for phrase in metric.synonyms:
        hit = route(phrase)
        if not hit.settled and hit.metric_id == metric.id:
            return phrase
    return None


def _metric(q_norm: str, catalog: Catalog, route: Router) -> Clarify | None:
    hits = [t for t in dict.fromkeys(q_norm.split()) if t in catalog.heads]
    if not hits:
        return None
    candidates = set.intersection(*(set(catalog.heads[t]) for t in hits))
    ordered = [m for m in catalog.metrics if m.id in candidates]
    if not 2 <= len(ordered) <= MAX_OPTIONS:
        return None
    term = " ".join(hits)
    options: list[ClarifyOption] = []
    for metric in ordered:
        phrase = _verified_phrase(metric, route)
        if phrase is None:
            continue
        options.append(
            _option(
                AmbiguityType.METRIC,
                term,
                {"metric_id": metric.id},
                label=phrase,
                resolved=phrase,
            )
        )
    return _clarify(
        AmbiguityType.METRIC,
        term=term,
        question=f"'{term}' can mean several metrics. Which one do you mean?",
        options=options,
        reason=(
            f"'{term}' names {len(ordered)} metrics "
            f"({', '.join(m.id for m in ordered)}) and no declared phrase picks one"
        ),
        catalog=catalog,
    )


def _term_column(question: str, q_norm: str, catalog: Catalog) -> Clarify | None:
    padded = f" {q_norm} "
    low = question.lower()
    for term, columns in catalog.term_groups.items():
        if f" {term} " not in padded:
            continue
        if any(f" {c.table} " in padded or c.column.lower() in low for c in columns):
            continue
        if len(columns) > MAX_OPTIONS:
            continue
        options = [
            _option(
                AmbiguityType.TERM_COLUMN,
                term,
                {"table": c.table, "column": c.column},
                label=f"{c.column} in {c.table}",
                resolved=f"{question.rstrip(' ?.!')} ({c.column} in {c.table})",
            )
            for c in columns
        ]
        return _clarify(
            AmbiguityType.TERM_COLUMN,
            term=term,
            question=f"'{term}' maps to several columns. Which one do you mean?",
            options=options,
            reason=(
                f"ontology term '{term}' maps to "
                f"{', '.join(f'{c.table}.{c.column}' for c in columns)}"
            ),
            catalog=catalog,
        )
    return None


def detect(question: str, catalog: Catalog, route: Router) -> Clarify | None:
    """One clarify question for an ambiguous ask, or None when it is not."""
    routed = route(question)
    if _settled(routed):
        return None
    found = _time_window(question, routed, catalog, route)
    if found is not None:
        return found
    q_norm = _normalize(question)
    if _anchored(q_norm, routed, catalog):
        return None
    return _metric(q_norm, catalog, route) or _term_column(question, q_norm, catalog)


def resolve(
    question: str, option_ids: Iterable[str], catalog: Catalog, route: Router
) -> Turn:
    """Apply the chosen options in order; clarify again only if still ambiguous."""
    current = question
    applied: list[str] = []
    for option_id in option_ids:
        pending = detect(current, catalog, route)
        if pending is None:
            break
        chosen = next((o for o in pending.options if o.id == option_id), None)
        if chosen is None:
            reason = f"{pending.served_reason}; option {option_id!r} was not offered"
            return Turn(current, tuple(applied), pending.model_copy(update={"served_reason": reason}))
        current = chosen.resolved_question
        applied.append(option_id)
    return Turn(current, tuple(applied), detect(current, catalog, route))


def catalog_from_semantic(
    metrics_doc: Mapping[str, Any], semantic_doc: Mapping[str, Any]
) -> Catalog:
    """Build the catalog from a metrics document and a semantic layer document."""
    metrics: list[MetricEntry] = []
    entities: set[str] = set()
    for raw in metrics_doc.get("metrics") or []:
        params = raw.get("params") or {}
        days = params.get("days") if isinstance(params, Mapping) else None
        windowed = isinstance(days, Mapping) and days.get("kind") == "int"
        synonyms = tuple(str(s) for s in raw.get("synonyms") or [] if str(s).strip())
        metrics.append(MetricEntry(str(raw["id"]), synonyms, windowed))
        for phrase in synonyms:
            words = _normalize(phrase).split()
            for i, word in enumerate(words[:-1]):
                if word in {"by", "per"}:
                    entities |= _forms(words[i + 1])
    term_columns: list[TermColumn] = []
    for join in semantic_doc.get("joins") or []:
        for key in ("from_table", "to_table"):
            if join.get(key):
                entities |= _forms(str(join[key]).lower())
    for entry in semantic_doc.get("glossary") or []:
        term, column, table = entry.get("term"), entry.get("maps_to"), entry.get("table")
        if not (term and column and table):
            continue
        entities |= _forms(str(table).lower())
        words = _normalize(str(term)).split()
        if len(words) == 1:
            entities |= _forms(words[0])
        term_columns.append(TermColumn(str(term), str(table), str(column)))
    return Catalog(tuple(metrics), frozenset(entities), tuple(term_columns))


def render(clarify: Clarify) -> str:
    """The prose a 1.4 consumer shows: the question and its numbered options."""
    choices = "; ".join(f"({i}) {o.label}" for i, o in enumerate(clarify.options, 1))
    return f"{clarify.question} Options: {choices}."
