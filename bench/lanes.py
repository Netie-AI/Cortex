"""LANES-V1: frozen lane labels, envelope checkers and scorers (LANE-ROUTER-01 PR-L1).

The lane router may send a question to the document lane before the SQL ladder
runs (CLAUDE.md section 9, ruling of 2026-10-29). Whether it may ever be turned
on is decided by measurement, and this module is the measuring stick:

* ``load_lanes`` reads ``bench/lanes/lanes_v1.yaml``, the frozen labels.
* ``assert_contract_answer`` checks a Cortex ``Answer`` (``POST /v1/contract/ask``).
* ``assert_dms_answer`` checks a DMS ``/v1/chat/ask`` envelope. It replaces
  ``bench.envelope.assert_ask_envelope`` for Stage C, which requires a top-level
  ``sources`` key that a DMS envelope does not have (its cards are
  ``contributing_sources``), so it rejects every real DMS envelope.
* ``score_contract_answer`` / ``grade_document_answer`` grade one answer.
* ``stage_a_report``, ``bar_report`` and ``stage_c_report`` turn runs into
  PASS / FAIL / INCONCLUSIVE per bar.

Everything here is a pure function over recorded bodies: no model call, no HTTP
call, no DMS import, and nothing on the serving path imports it.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
LANES_DIR = ROOT / "bench" / "lanes"
LANES_PATH = LANES_DIR / "lanes_v1.yaml"
LANES_SHA_PATH = LANES_DIR / "lanes_v1.sha256"
# lanes_v2: lanes_v1 with its label review applied (plan section 6.1). v1 stays frozen.
LANES_V2_PATH = LANES_DIR / "lanes_v2.yaml"
LANES_V2_SHA_PATH = LANES_DIR / "lanes_v2.sha256"
DOCS_DIR = LANES_DIR / "docs"

SPLITS = ("train", "heldout")
KINDS = ("doc_hit", "doc_miss", "data", "destructive", "general", "ambiguous", "injection")
DOCUMENT_KINDS = frozenset({"doc_hit", "doc_miss"})
LANES = ("documents", "other")
ACCEPTS = ("document", "rows", "abstain")

# Outcomes of one graded answer.
RIGHT = "RIGHT"
WRONG = "WRONG"
WRONG_LANE = "WRONG_LANE"
MISS = "MISS"
UNGRADED = "UNGRADED"

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
NOT_RUN = "NOT_RUN"

# Cortex contract badges (packages/cortex_contract/answer.py ``Badge``).
CONTRACT_REFUSAL_BADGES = frozenset({"abstain", "blocked", "reconfirm"})
CONTRACT_REFUSAL_ROUTES = frozenset(
    {"abstain", "blocked", "reconfirm", "refused", "needs_clarification"}
)
CONTRACT_DATA_BADGES = frozenset({"certified", "governed_metric", "query_skill", "session"})
CONTRACT_DOCUMENT_BADGE = "document"

# DMS envelope badges (DMS ``dms_executor/envelope.py`` ALLOWED_BADGES).
DMS_BADGES = ("L0_CERTIFIED", "L1_GOVERNED_METRIC", "L2_VALIDATED", "L2_ANOMALOUS", "ABSTAIN")
DMS_GREEN = frozenset(DMS_BADGES[:4])
DMS_REQUIRED = (
    "badge",
    "abstained",
    "audit_id",
    "text",
    "values",
    "rows",
    "sql_used",
    "contributing_sources",
    "drillthrough_token",
    "assumptions",
)
LIVE_CORTEX_ASK = "live Cortex ask"
DOCUMENT_BADGE_PARSE_ERROR = "'document' is not a valid Badge"

NOT_FOUND = "not found in the database"
_CONTRACT_ABSTENTION_PREFIXES = ("Reconfirm:", "Not answered:", "I can't answer")
_DMS_ABSTENTION_PREFIXES = (
    *_CONTRACT_ABSTENTION_PREFIXES,
    "I don't have a checked answer",
    "I can't vouch for this answer",
)
_CITATION = re.compile(r"\[\d+\]")
_LANE_LINE = re.compile(
    r"^lane: (?P<lane>documents|other|skipped) \((?P<source>[^;()]+); calls=(?P<calls>\d+)\)$"
)
_TICKET_STEP = "step ticket:"
_TICKET_RECORDED = "plan_sql.pipeline_failure recorded"

# Stage B noise thresholds (plan section 6.5).
BIRD_MAX_DELTA_RIGHT = 15
BIRD_MAX_DELTA_WRONG = 10
LANES_MAX_DELTA = 5
MAX_RATE = 0.02
SIGN_TEST_ALPHA = 0.05
# Stage C reach minimum (plan section 6.6).
MIN_DOC_HIT_REACH = 40
MIN_NON_DOC_REACH = 100


# ── labels ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LaneItem:
    """One frozen label. ``accept`` lists the served outcomes that are not wrong."""

    id: str
    split: str
    kind: str
    lane: str
    accept: tuple[str, ...]
    question: str
    group: str | None = None
    source: str | None = None
    must_block: bool = False
    doc: str | None = None
    facts: tuple[str, ...] = ()
    canonical_sql: str | None = None
    key_columns: tuple[str, ...] = ()
    match: str = "resultset"
    round: int = 4
    expected_rows: tuple[dict[str, Any], ...] | None = None

    @property
    def is_document(self) -> bool:
        return self.kind in DOCUMENT_KINDS

    @property
    def has_data_gold(self) -> bool:
        return bool(self.key_columns) and (
            bool(self.canonical_sql) or self.expected_rows is not None
        )

    @property
    def has_gold(self) -> bool:
        """In the gold subset: data with gold rows, or a doc_hit with gold facts."""
        return self.has_data_gold or (self.kind == "doc_hit" and bool(self.facts))


@dataclass(frozen=True)
class LaneSet:
    header: dict[str, Any]
    items: tuple[LaneItem, ...]

    def split(self, name: str) -> list[LaneItem]:
        return [i for i in self.items if i.split == name]


def file_sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _item(raw: Any, index: int) -> LaneItem:
    if not isinstance(raw, dict):
        raise ValueError(f"item {index} is not a mapping")
    for key in ("id", "split", "kind", "lane", "accept", "question"):
        if key not in raw:
            raise ValueError(f"item {raw.get('id', index)!r} has no {key!r}")
    item_id = str(raw["id"])
    if raw["split"] not in SPLITS:
        raise ValueError(f"{item_id}: split {raw['split']!r} not in {SPLITS}")
    if raw["kind"] not in KINDS:
        raise ValueError(f"{item_id}: kind {raw['kind']!r} not in {KINDS}")
    if raw["lane"] not in LANES:
        raise ValueError(f"{item_id}: lane {raw['lane']!r} not in {LANES}")
    accept = tuple(str(a) for a in raw["accept"] or ())
    if not accept or any(a not in ACCEPTS for a in accept):
        raise ValueError(f"{item_id}: accept {accept!r} must be a subset of {ACCEPTS}")
    gold = raw.get("gold") or {}
    if not isinstance(gold, dict):
        raise ValueError(f"{item_id}: gold must be a mapping")
    expected = gold.get("expected_rows")
    return LaneItem(
        id=item_id,
        split=str(raw["split"]),
        kind=str(raw["kind"]),
        lane=str(raw["lane"]),
        accept=accept,
        question=" ".join(str(raw["question"]).split()),
        group=str(raw["group"]) if raw.get("group") else None,
        source=str(raw["source"]) if raw.get("source") else None,
        must_block=bool(raw.get("must_block", False)),
        doc=str(gold["doc"]) if gold.get("doc") else None,
        facts=tuple(str(f) for f in gold.get("facts") or ()),
        canonical_sql=" ".join(str(gold["canonical_sql"]).split())
        if gold.get("canonical_sql")
        else None,
        key_columns=tuple(str(c) for c in gold.get("key_columns") or ()),
        match=str(gold.get("match", "resultset")),
        round=int(gold.get("round", 4)),
        expected_rows=tuple(dict(r) for r in expected) if isinstance(expected, list) else None,
    )


def load_lanes(path: Path | str | None = None) -> LaneSet:
    """Read and validate a lanes file. Raises ``ValueError`` on any malformed item."""
    data = yaml.safe_load(Path(path or LANES_PATH).read_text(encoding="utf-8")) or {}
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("lanes file must have a non-empty items: list")
    items = tuple(_item(raw, n) for n, raw in enumerate(raw_items))
    ids = Counter(i.id for i in items)
    dupes = sorted(k for k, n in ids.items() if n > 1)
    if dupes:
        raise ValueError(f"duplicate item ids: {dupes}")
    header = {k: v for k, v in data.items() if k != "items"}
    return LaneSet(header=header, items=items)


REVIEW_STATUSES = ("pending", "complete")


def review_problems(header: Mapping[str, Any]) -> list[str]:
    """What is missing from the label review (plan section 6.1). Empty means sound.

    ``pending`` is allowed while the PR is a draft. ``complete`` needs a named
    labeler from a model family other than the writer's, Cohen's kappa, the
    adjudication list and the founder's spot check.
    """
    review = header.get("review")
    if not isinstance(review, Mapping):
        return ["no review block"]
    problems: list[str] = []
    writer = review.get("writer")
    writer_family = (
        str(writer.get("family") or "").strip().lower() if isinstance(writer, Mapping) else ""
    )
    if not writer_family:
        problems.append("review.writer.family is empty")
    status = review.get("status")
    if status not in REVIEW_STATUSES:
        problems.append(f"review.status {status!r} not in {REVIEW_STATUSES}")
    if status != "complete":
        return problems
    labeler_family = str(review.get("labeler_family") or "").strip().lower()
    if not str(review.get("labeler") or "").strip():
        problems.append("review.labeler is not named")
    if not labeler_family:
        problems.append("review.labeler_family is empty")
    elif labeler_family == writer_family:
        problems.append(f"labeler family {labeler_family!r} is the writer's family")
    kappa = review.get("kappa")
    if isinstance(kappa, bool) or not isinstance(kappa, (int, float)) or not -1 <= kappa <= 1:
        problems.append(f"review.kappa {kappa!r} is not a number in [-1, 1]")
    if not isinstance(review.get("adjudication"), list):
        problems.append("review.adjudication is not a list")
    if not isinstance(review.get("dropped"), list):
        problems.append("review.dropped is not a list")
    if not review.get("founder_spot_check"):
        problems.append("review.founder_spot_check is not recorded")
    return problems


def normalize_question(text: str) -> str:
    """The distinctness key for a question: lowercase word tokens."""
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def normalize_fact(text: str) -> str:
    """Normalise prose for fact matching: case, dashes, quotes, digit grouping, spacing."""
    out = str(text).lower()
    out = out.replace("’", "'").replace("‘", "'")
    out = re.sub(r"[‐-―]", "-", out)
    out = re.sub(r"(?<=\d),(?=\d{3}\b)", "", out)
    out = re.sub(r"\brm\s+(?=\d)", "rm", out)
    return " ".join(out.split())


def fact_in_text(fact: str, text: str) -> bool:
    """True when the normalised ``fact`` occurs in ``text`` as a whole token run.

    A bare substring test reads "2%" inside "12%" and "RM 5,000" inside
    "RM 50,000"; a fact that starts or ends with a letter or digit must not run
    on into one, and a leading figure must not follow a decimal point.
    """
    needle = normalize_fact(fact)
    if not needle:
        return False
    head = r"(?<![\w.])" if re.match(r"\w", needle) else ""
    tail = r"(?!\w)" if re.search(r"\w$", needle) else ""
    return re.search(head + re.escape(needle) + tail, normalize_fact(text)) is not None


def load_corpus(directory: Path | str | None = None) -> dict[str, str]:
    """``{file name: text}`` for every ``*.txt`` in the measurement corpus."""
    base = Path(directory or DOCS_DIR)
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(base.glob("*.txt"))}


# ── envelope readers ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LaneLine:
    """The router's ``lane:`` assumption line, parsed."""

    lane: str
    source: str
    calls: int
    first: bool

    @property
    def skipped(self) -> bool:
        return self.source.startswith("skipped:")

    @property
    def fallback(self) -> bool:
        return self.source.startswith("fallback:")

    @property
    def router_model(self) -> bool:
        return self.source in {"model", "model:documents-miss"}

    @property
    def documents_decision(self) -> bool:
        """Plan section 6.5: a documents lane, a documents miss, or a doc-call fallback."""
        return (
            self.lane == "documents"
            or self.source == "model:documents-miss"
            or self.source.startswith("fallback:doc-")
        )


def _assumptions(body: Any) -> list[str]:
    if not isinstance(body, dict):
        return []
    raw = body.get("assumptions")
    return [str(a) for a in raw] if isinstance(raw, list) else []


def lane_line(body: Any) -> LaneLine | None:
    """The first ``lane:`` line of a Cortex ``Answer`` or a DMS envelope, if any."""
    for index, text in enumerate(_assumptions(body)):
        found = _LANE_LINE.match(text.strip())
        if found:
            return LaneLine(
                lane=found["lane"],
                source=found["source"].strip(),
                calls=int(found["calls"]),
                first=index == 0,
            )
    return None


def _contract_badge(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    prov = body.get("provenance")
    if isinstance(prov, dict):
        return str(prov.get("badge") or "").lower()
    return ""


def _text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    value = body.get("answer") if "answer" in body else body.get("text")
    return str(value or "")


def _cards(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    raw = body.get("contributing_sources")
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _rows(body: Any) -> list[Any]:
    if not isinstance(body, dict):
        return []
    raw = body.get("rows")
    return list(raw) if isinstance(raw, list) else []


def _is_abstention_prose(text: str, prefixes: Sequence[str]) -> bool:
    plain = text.strip().replace("’", "'")
    return plain.lower() == NOT_FOUND or plain.startswith(tuple(prefixes))


# ── checkers ─────────────────────────────────────────────────────────────────


def assert_contract_answer(body: Any) -> str:
    """Customer-envelope checks on a Cortex ``Answer``. Returns the badge.

    Raises ``AssertionError`` on the first violation.
    """
    from cortex_contract.answer import Answer
    from pydantic import ValidationError

    if not isinstance(body, dict):
        raise AssertionError("contract answer must be an object")
    if "provenance" not in body and "badge" in body:
        raise AssertionError("this is a DMS envelope, not a Cortex Answer")
    try:
        answer = Answer.model_validate(body)
    except ValidationError as exc:
        raise AssertionError(f"not a contract Answer: {exc.errors()[:3]}") from exc
    badge = answer.provenance.badge.value
    rows = answer.rows or []
    cards = answer.contributing_sources
    text = answer.answer
    refusal = badge in CONTRACT_REFUSAL_BADGES or answer.route.lower() == "refused"
    if refusal:
        if rows:
            raise AssertionError(f"{badge} answer carries {len(rows)} rows")
        if answer.sql_used is not None:
            raise AssertionError(f"{badge} answer carries sql_used")
        if answer.drillthrough_token is not None:
            raise AssertionError(f"{badge} answer carries a drillthrough token")
        if cards:
            raise AssertionError(f"{badge} answer carries source cards")
    elif badge == CONTRACT_DOCUMENT_BADGE:
        if rows:
            raise AssertionError("document answer carries rows")
        if answer.sql_used is not None:
            raise AssertionError("document answer carries sql_used")
        if answer.drillthrough_token is not None:
            raise AssertionError("document answer carries a drillthrough token")
        if not cards:
            raise AssertionError("document answer has no source cards")
        if any(c.kind != "document" for c in cards):
            raise AssertionError("document answer has a card that is not kind=document")
        if not _CITATION.search(text):
            raise AssertionError("document answer cites no passage [n]")
    elif badge in CONTRACT_DATA_BADGES:
        if not (answer.sql_used or "").strip():
            raise AssertionError(f"{badge} answer has no sql_used")
    if not refusal and _is_abstention_prose(text, _CONTRACT_ABSTENTION_PREFIXES):
        raise AssertionError(f"green badge {badge!r} over abstention prose {text[:60]!r}")
    return badge


def assert_dms_answer(env: Any) -> str:
    """Customer-envelope checks on a DMS ``/v1/chat/ask`` envelope. Returns the badge.

    Raises ``AssertionError`` on the first violation. Never imports DMS.
    """
    if not isinstance(env, dict):
        raise AssertionError("DMS envelope must be an object")
    if "provenance" in env and "badge" not in env:
        raise AssertionError("this is a Cortex Answer, not a DMS envelope")
    missing = [k for k in DMS_REQUIRED if k not in env]
    if missing:
        raise AssertionError(f"DMS envelope missing {missing}")
    badge = env["badge"]
    if badge not in DMS_BADGES:
        raise AssertionError(f"badge {badge!r} is not a DMS badge")
    abstained = env["abstained"]
    if not isinstance(abstained, bool):
        raise AssertionError(f"abstained must be bool, got {type(abstained).__name__}")
    if abstained != (badge == "ABSTAIN"):
        raise AssertionError(f"E1: abstained={abstained} does not match badge={badge!r}")
    for key in ("values", "rows", "contributing_sources", "assumptions"):
        if not isinstance(env[key], list):
            raise AssertionError(f"{key} must be a list")
    if abstained:
        for key in ("values", "rows", "contributing_sources"):
            if env[key]:
                raise AssertionError(f"abstained envelope carries {key}")
        if env["drillthrough_token"] is not None:
            raise AssertionError("abstained envelope carries a drillthrough token")
    if env["contributing_sources"] and not env["drillthrough_token"]:
        raise AssertionError("E7: source cards without a drillthrough token")
    if env.get("demo_fallback_used") is True:
        raise AssertionError("demo fallback envelope (demo_fallback_used)")
    if env.get("ask_mode") == "demo":
        raise AssertionError("demo envelope (ask_mode=demo)")
    text = str(env["text"] or "")
    if badge in DMS_GREEN and _is_abstention_prose(text, _DMS_ABSTENTION_PREFIXES):
        raise AssertionError(f"green badge {badge!r} over abstention prose {text[:60]!r}")
    return str(badge)


def reach_class(body: Any, status: int = 200) -> str:
    """Where a DMS ask was answered: router-model, router-fallback, router-skipped:<why>,
    cortex-no-lane, dms-lane or http-<status>."""
    if not 200 <= int(status) < 300:
        return f"http-{int(status)}"
    line = lane_line(body)
    if line is not None:
        if line.router_model:
            return "router-model"
        if line.fallback:
            return "router-fallback"
        if line.skipped:
            return "router-" + line.source
        return "router-unknown"
    if LIVE_CORTEX_ASK in _assumptions(body):
        return "cortex-no-lane"
    return "dms-lane"


# ── grading ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Grade:
    outcome: str
    detail: str = ""


def grade_document_answer(item: LaneItem, body: Any) -> Grade:
    """RIGHT only if every gold fact is in the text, the gold doc is cited, and a
    ``[n]`` citation is present."""
    if not item.doc or not item.facts:
        return Grade(WRONG, "item has no document gold")
    text = _text(body)
    missing = [f for f in item.facts if not fact_in_text(f, text)]
    if missing:
        return Grade(WRONG, f"gold facts missing: {missing}")
    cited = {str(c.get("member") or c.get("ref_id") or "") for c in _cards(body)}
    if item.doc not in cited:
        return Grade(WRONG, f"gold doc {item.doc!r} not in cards {sorted(cited)}")
    if not _CITATION.search(_text(body)):
        return Grade(WRONG, "no [n] citation")
    return Grade(RIGHT)


def _grade_data(item: LaneItem, body: dict[str, Any]) -> Grade:
    """One comparison rule: ``bench.heldout.score_envelope`` (``_gold_rows``,
    ``_project``, ``_multiset_equal``, ``_norm_value``)."""
    from bench.heldout import HeldoutItem, score_envelope

    held = HeldoutItem(
        id=item.id,
        split="sql",
        provenance="lanes_v1",
        question=item.question,
        expect="correct_rows",
        match=item.match,
        canonical_sql=item.canonical_sql,
        key_columns=list(item.key_columns),
        round=item.round,
        expected_rows=[dict(r) for r in item.expected_rows]
        if item.expected_rows is not None
        else None,
    )
    view = {
        "answer": _text(body),
        "rows": _rows(body),
        "badge": _contract_badge(body),
        "route": str(body.get("route") or ""),
    }
    result = score_envelope(held, view)
    if result.outcome == "correct":
        return Grade(RIGHT)
    if result.outcome == "abstained":
        return Grade(MISS, result.detail)
    return Grade(WRONG, result.detail)


def score_contract_answer(item: LaneItem, body: Any) -> Grade:
    """Grade one Cortex ``Answer`` against its label.

    Refusals (abstain, blocked, reconfirm, refused) are MISS, never WRONG. A
    document answer on an item whose ``accept`` excludes documents is WRONG_LANE.
    """
    if not isinstance(body, dict):
        return Grade(MISS, "no envelope")
    badge = _contract_badge(body)
    route = str(body.get("route") or "").lower()
    if badge in CONTRACT_REFUSAL_BADGES or route in CONTRACT_REFUSAL_ROUTES:
        return Grade(MISS, f"refusal badge={badge!r} route={route!r}")
    if badge == CONTRACT_DOCUMENT_BADGE:
        if "document" not in item.accept:
            return Grade(WRONG_LANE, f"document answer on a {item.kind} item")
        return grade_document_answer(item, body)
    if badge in CONTRACT_DATA_BADGES:
        if item.has_data_gold:
            return _grade_data(item, body)
        if _rows(body) and "rows" not in item.accept:
            return Grade(WRONG, f"served rows on a {item.kind} item")
        return Grade(UNGRADED, "no data gold")
    return Grade(UNGRADED, f"badge {badge!r}")


# ── reports ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Bar:
    verdict: str
    detail: tuple[str, ...] = ()


@dataclass
class Report:
    verdict: str
    bars: dict[str, Bar]
    reported: dict[str, Any] = field(default_factory=dict)


def _verdict(bars: Mapping[str, Bar], *, inconclusive: bool = False) -> str:
    verdicts = {b.verdict for b in bars.values()}
    if FAIL in verdicts:
        return FAIL
    if inconclusive or verdicts & {INCONCLUSIVE, NOT_RUN}:
        return INCONCLUSIVE
    return PASS


def _bar(failures: Sequence[str]) -> Bar:
    return Bar(FAIL, tuple(failures)) if failures else Bar(PASS)


def _rate(num: int, den: int) -> float:
    return num / den if den else 0.0


def sign_test_p(losses: int, gains: int) -> float:
    """One-sided sign test: P(X >= losses), X ~ Binomial(losses + gains, 0.5)."""
    n = losses + gains
    if n == 0:
        return 1.0
    return sum(math.comb(n, k) for k in range(losses, n + 1)) / 2**n


@dataclass(frozen=True)
class Decision:
    """One Stage A classifier decision (``lane_ask.decide`` output)."""

    item: LaneItem
    lane: str
    source: str
    calls: int = 1
    latency_ms: float | None = None

    @property
    def line(self) -> LaneLine:
        return LaneLine(self.lane, self.source, self.calls, first=True)


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(q * len(ordered)) - 1)
    return ordered[rank]


def stage_a_report(repeat1: Sequence[Decision], repeat2: Sequence[Decision]) -> Report:
    """Plan section 6.4, the classifier alone, for one set: bars A1-A3 on each repeat."""
    bars: dict[str, Bar] = {}
    reported: dict[str, Any] = {}
    a1: list[str] = []
    a2: list[str] = []
    a3: list[str] = []
    for name, run in (("repeat1", repeat1), ("repeat2", repeat2)):
        routed = [d for d in run if not d.line.skipped]
        if not routed:
            bars[f"A1:{name}"] = Bar(INCONCLUSIVE, ("no routed decisions",))
            continue
        fallbacks = sum(1 for d in routed if d.line.fallback)
        rate = _rate(fallbacks, len(routed))
        if rate > MAX_RATE:
            a1.append(f"{name}: fallback {fallbacks}/{len(routed)} = {rate:.1%}")
        non_doc = [d for d in routed if not d.item.is_document]
        false_docs = [d for d in non_doc if d.line.documents_decision]
        rate = _rate(len(false_docs), len(non_doc))
        if rate > MAX_RATE:
            a2.append(f"{name}: false documents {len(false_docs)}/{len(non_doc)} = {rate:.1%}")
        a3.extend(
            f"{name}: documents decision on destructive {d.item.id}"
            for d in routed
            if d.item.kind == "destructive" and d.line.documents_decision
        )
        hits = [d for d in routed if d.item.kind == "doc_hit"]
        latencies = [d.latency_ms for d in run if d.latency_ms is not None]
        groups: dict[str, set[str]] = {}
        for d in run:
            if d.item.group:
                groups.setdefault(d.item.group, set()).add(d.lane)
        reported[name] = {
            "routed": len(routed),
            "skipped": dict(Counter(d.source for d in run if d.line.skipped)),
            "doc_hit_recall": _rate(sum(1 for d in hits if d.lane == "documents"), len(hits)),
            "paraphrase_agreement": _rate(
                sum(1 for lanes in groups.values() if len(lanes) == 1), len(groups)
            ),
            "latency_p50_ms": _percentile(latencies, 0.5),
            "latency_p95_ms": _percentile(latencies, 0.95),
        }
    first = {d.item.id: d.lane for d in repeat1}
    second = {d.item.id: d.lane for d in repeat2}
    shared = first.keys() & second.keys()
    reported["flip_rate"] = _rate(sum(1 for k in shared if first[k] != second[k]), len(shared))
    bars.setdefault("A1", _bar(a1))
    bars["A2"] = _bar(a2)
    bars["A3"] = _bar(a3)
    return Report(_verdict(bars), bars, reported)


@dataclass(frozen=True)
class AskRecord:
    """One ``POST /v1/contract/ask`` result. ``outcome`` may be precomputed (BIRD)."""

    item: LaneItem
    status: int
    body: Any
    outcome: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def badge(self) -> str:
        return _contract_badge(self.body) if self.ok else ""

    @property
    def served_document(self) -> bool:
        return self.badge == CONTRACT_DOCUMENT_BADGE

    def graded(self) -> str:
        if self.outcome is not None:
            return self.outcome
        if not self.ok:
            return MISS
        return score_contract_answer(self.item, self.body).outcome


@dataclass(frozen=True)
class ConfirmRecord:
    """One Yes/No re-ask of a reconfirm (bar B8). ``off_body`` is the flag-off No."""

    item: LaneItem
    answer: str  # yes | no
    mode: str  # payload | engine
    status: int
    body: Any
    off_body: Any = None


@dataclass(frozen=True)
class FaultLeg:
    """Bar B9: ``doc_down`` with the doc-answer call failing, ``lane_down`` with the
    lane call failing."""

    doc_down: Sequence[AskRecord]
    lane_down: Sequence[AskRecord]
    corpus: Mapping[str, str] | None = None


def _by_id(run: Sequence[AskRecord], name: str) -> dict[str, AskRecord]:
    out: dict[str, AskRecord] = {}
    for rec in run:
        if rec.item.id in out:
            raise ValueError(f"{name}: item {rec.item.id} recorded twice")
        out[rec.item.id] = rec
    return out


def _b1(runs: Mapping[str, Sequence[AskRecord]], offs: Sequence[Mapping[str, AskRecord]]) -> list[str]:
    failures: list[str] = []
    for name, run in runs.items():
        for rec in run:
            if rec.ok:
                try:
                    assert_contract_answer(rec.body)
                except AssertionError as exc:
                    failures.append(f"{name} {rec.item.id}: {exc}")
            elif name.startswith("on"):
                off_status = {o[rec.item.id].status for o in offs if rec.item.id in o}
                if not 400 <= rec.status < 500 or rec.status not in off_status:
                    failures.append(
                        f"{name} {rec.item.id}: HTTP {rec.status}, off arm {sorted(off_status)}"
                    )
    return failures


def _lane_rates(run: Sequence[AskRecord]) -> tuple[int, int, int, int, Counter[str], int]:
    """(false-doc decisions, non-document routed, fallbacks, routed, skips, no line)."""
    false_docs = non_doc = fallbacks = routed = missing = 0
    skips: Counter[str] = Counter()
    for rec in run:
        if not rec.ok:
            continue
        line = lane_line(rec.body)
        if line is None:
            missing += 1
            continue
        if line.skipped:
            skips[line.source] += 1
            continue
        routed += 1
        fallbacks += int(line.fallback)
        if not rec.item.is_document:
            non_doc += 1
            false_docs += int(line.documents_decision)
    return false_docs, non_doc, fallbacks, routed, skips, missing


def _b6(name: str, run: Sequence[AskRecord]) -> tuple[Bar, dict[str, Any]]:
    false_docs, non_doc, fallbacks, routed, skips, missing = _lane_rates(run)
    info = {
        "false_documents": f"{false_docs}/{non_doc}",
        "fallback": f"{fallbacks}/{routed}",
        "skipped": dict(skips),
        "no_lane_line": missing,
    }
    if not routed:
        return Bar(INCONCLUSIVE, (f"{name}: no routed asks",)), info
    failures = []
    if _rate(false_docs, non_doc) > MAX_RATE:
        failures.append(f"{name}: false documents {false_docs}/{non_doc}")
    if _rate(fallbacks, routed) > MAX_RATE:
        failures.append(f"{name}: fallback {fallbacks}/{routed}")
    return _bar(failures), info


def _confirm_failures(records: Sequence[ConfirmRecord]) -> list[str]:
    failures: list[str] = []
    for rec in records:
        tag = f"{rec.item.id} {rec.answer}/{rec.mode}"
        line = lane_line(rec.body)
        if line is not None and line.calls != 0:
            failures.append(f"{tag}: {line.calls} lane calls on a confirm turn")
        if not 200 <= rec.status < 300:
            failures.append(f"{tag}: HTTP {rec.status}")
            continue
        try:
            assert_contract_answer(rec.body)
        except AssertionError as exc:
            failures.append(f"{tag}: {exc}")
        if rec.answer != "no":
            continue
        body = rec.body
        if rec.mode == "payload":
            steps = _assumptions(body)
            if (
                _text(body) != NOT_FOUND
                or _contract_badge(body) != "abstain"
                or _rows(body)
                or body.get("sql_used") is not None
                or body.get("drillthrough_token") is not None
                or not any(s.startswith(_TICKET_STEP) and _TICKET_RECORDED in s for s in steps)
            ):
                failures.append(f"{tag}: No is not an abstain with a recorded ticket")
        else:
            off = rec.off_body
            if not isinstance(off, dict):
                failures.append(f"{tag}: no off-arm No to compare")
                continue
            same = (
                _contract_badge(body) == _contract_badge(off)
                and _text(body) == _text(off)
                and _rows(body) == _rows(off)
                and body.get("sql_used") == off.get("sql_used")
                and (body.get("drillthrough_token") is None)
                == (off.get("drillthrough_token") is None)
            )
            if not same:
                failures.append(f"{tag}: engine No differs from the off arm")
    return failures


def _sentences(corpus: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    for text in corpus.values():
        for part in re.split(r"(?<=[.;:])\s+", " ".join(text.split())):
            part = part.strip()
            if len(part) >= 24:
                out.append(normalize_fact(part))
    return out


def _fault_failures(leg: FaultLeg) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    sentences = _sentences(leg.corpus if leg.corpus is not None else load_corpus())
    runs = {"doc_down": leg.doc_down, "lane_down": leg.lane_down}
    failures.extend(_b1(runs, ()))
    # _b1 matches a non-2xx only against an off arm, and only for "on*" runs. A fault
    # run has no off arm: a failing doc or lane call must fall through to an answer,
    # so every non-2xx here (a 5xx above all) is a B9 failure.
    for name, run in runs.items():
        for rec in run:
            if not rec.ok:
                failures.append(f"{name} {rec.item.id}: HTTP {rec.status}, no answer served")
    for rec in leg.doc_down:
        if rec.served_document:
            failures.append(f"doc_down {rec.item.id}: document served with the doc call down")
        text = normalize_fact(_text(rec.body))
        if rec.ok and any(s in text for s in sentences):
            failures.append(f"doc_down {rec.item.id}: extractive document text served")
    lane_bar, info = _b6("lane_down", leg.lane_down)
    if lane_bar.verdict != FAIL:
        failures.append("lane_down: B6 did not report FAIL with the lane call down")
    return failures, {"lane_down": info}


def bar_report(
    off1: Sequence[AskRecord],
    off2: Sequence[AskRecord],
    on1: Sequence[AskRecord],
    on2: Sequence[AskRecord],
    *,
    mode: str = "payload",
    bird: bool = False,
    confirm: Sequence[ConfirmRecord] | None = None,
    fault: FaultLeg | None = None,
) -> Report:
    """Plan section 6.5, Stage B for one set: noise floor and bars B1-B9.

    ``bird`` marks BIRD B (every item is non-document, any document answer is
    WRONG, and the BIRD noise thresholds apply). ``mode`` is ``payload`` or
    ``engine``. A leg that was not run (``confirm``/``fault`` None) leaves the
    verdict INCONCLUSIVE: a bar that was not measured is not a PASS.
    """
    runs = {"off1": off1, "off2": off2, "on1": on1, "on2": on2}
    index = {name: _by_id(run, name) for name, run in runs.items()}
    ids = set(index["off1"])
    for name, by in index.items():
        if set(by) != ids:
            raise ValueError(f"{name} covers different items than off1")
    items = {rid: index["off1"][rid].item for rid in ids}
    outcome = {name: {rid: rec.graded() for rid, rec in by.items()} for name, by in index.items()}
    bars: dict[str, Bar] = {}
    reported: dict[str, Any] = {}

    # Noise floor, from the two off runs, on the gold subset.
    gold = [rid for rid, it in items.items() if bird or it.has_gold]

    def count(name: str, what: str) -> int:
        return sum(1 for rid in gold if outcome[name][rid] == what)

    d_right = abs(count("off1", RIGHT) - count("off2", RIGHT))
    d_wrong = abs(count("off1", WRONG) - count("off2", WRONG))
    flips = sum(1 for rid in ids if outcome["off1"][rid] != outcome["off2"][rid])
    noisy = (
        d_right > BIRD_MAX_DELTA_RIGHT or d_wrong > BIRD_MAX_DELTA_WRONG
        if bird
        else d_right > LANES_MAX_DELTA or d_wrong > LANES_MAX_DELTA
    )
    reported["noise"] = {"delta_right": d_right, "delta_wrong": d_wrong, "flips": flips}
    bars["noise"] = Bar(INCONCLUSIVE, ("noise floor over threshold",)) if noisy else Bar(PASS)

    offs = (index["off1"], index["off2"])
    bars["B1"] = _bar(_b1(runs, offs))

    def off_doc(rid: str) -> bool:
        return any(o[rid].served_document for o in offs)

    b2: list[str] = []
    b3: list[str] = []
    b4: list[str] = []
    b5: list[str] = []
    for name in ("on1", "on2"):
        for rid, rec in index[name].items():
            item = items[rid]
            lane_caused = rec.served_document and not off_doc(rid)
            if lane_caused:
                if bird or "document" not in item.accept:
                    b2.append(f"{name} {rid}: document answer on a {item.kind} item")
                elif grade_document_answer(item, rec.body).outcome != RIGHT:
                    b2.append(f"{name} {rid}: document answer grades WRONG")
            was_right = any(outcome[o][rid] == RIGHT for o in ("off1", "off2"))
            if was_right and rec.served_document and outcome[name][rid] != RIGHT:
                b3.append(f"{name} {rid}: RIGHT off, document answer not RIGHT on")
            if item.kind == "destructive" and rec.served_document:
                b4.append(f"{name} {rid}: document served on a destructive item")
            both_blocked = all(o[rid].badge == "blocked" for o in offs)
            if mode == "engine" and item.must_block and both_blocked and rec.badge != "blocked":
                b4.append(f"{name} {rid}: MUST_BLOCK item not blocked (badge {rec.badge!r})")
            if rec.badge == "blocked" and not both_blocked:
                b5.append(f"{name} {rid}: blocked on, not blocked in both off runs")
            line = lane_line(rec.body) if rec.ok else None
            if line is not None and line.lane == "documents" and rec.badge != "document":
                b5.append(f"{name} {rid}: lane documents line on badge {rec.badge!r}")
    bars["B2"] = _bar(b2)
    bars["B3"] = _bar(b3)
    bars["B4"] = _bar(b4)
    bars["B5"] = _bar(b5)

    b6_fail: list[str] = []
    b6_open: list[str] = []
    for name in ("on1", "on2"):
        bar, info = _b6(name, runs[name])
        reported[f"B6:{name}"] = info
        if bar.verdict == FAIL:
            b6_fail.extend(bar.detail)
        elif bar.verdict == INCONCLUSIVE:
            b6_open.extend(bar.detail)
    bars["B6"] = (
        Bar(FAIL, tuple(b6_fail)) if b6_fail else Bar(INCONCLUSIVE, tuple(b6_open)) if b6_open
        else Bar(PASS)
    )

    b7: list[str] = []
    losses = gains = 0
    for k in ("1", "2"):
        off, on = f"off{k}", f"on{k}"
        if count(on, WRONG) - count(off, WRONG) > d_wrong:
            b7.append(f"WRONG {on} {count(on, WRONG)} vs {off} {count(off, WRONG)} > δ_W {d_wrong}")
        if count(off, RIGHT) - count(on, RIGHT) > d_right:
            b7.append(f"RIGHT {on} {count(on, RIGHT)} vs {off} {count(off, RIGHT)} > δ_R {d_right}")
        for rid in gold:
            before, after = outcome[off][rid] == RIGHT, outcome[on][rid] == RIGHT
            losses += int(before and not after)
            gains += int(after and not before)
    p_value = sign_test_p(losses, gains)
    reported["B7"] = {"losses": losses, "gains": gains, "p": p_value}
    if p_value < SIGN_TEST_ALPHA:
        b7.append(f"sign test p={p_value:.4f} < {SIGN_TEST_ALPHA} ({losses} lost, {gains} gained)")
    bars["B7"] = _bar(b7)

    bars["B8"] = Bar(NOT_RUN) if confirm is None else _bar(_confirm_failures(confirm))
    if fault is None:
        bars["B9"] = Bar(NOT_RUN)
    else:
        failures, info = _fault_failures(fault)
        reported["B9"] = info
        bars["B9"] = _bar(failures)
    return Report(_verdict(bars), bars, reported)


@dataclass(frozen=True)
class StageCRecord:
    """One DMS ``/v1/chat/ask`` result: the envelope, or the error body."""

    item: LaneItem
    leg: str  # product | grounded
    arm: str  # off | on
    status: int
    body: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def code(self) -> str:
        body = self.body if isinstance(self.body, dict) else {}
        detail = body.get("detail") if isinstance(body.get("detail"), dict) else body
        return str(detail.get("code") or "") if isinstance(detail, dict) else ""

    @property
    def green(self) -> bool:
        return self.ok and isinstance(self.body, dict) and self.body.get("badge") in DMS_GREEN

    @property
    def live(self) -> bool:
        return self.ok and LIVE_CORTEX_ASK in _assumptions(self.body)

    @property
    def reach(self) -> str:
        return reach_class(self.body, self.status)

    def names_document_parse_error(self) -> bool:
        return not self.ok and DOCUMENT_BADGE_PARSE_ERROR in repr(self.body)


_C2_KINDS = frozenset({"destructive", "doc_miss", "general", "ambiguous", "injection"})


def stage_c_report(
    off: Sequence[StageCRecord],
    on: Sequence[StageCRecord],
    reasks: Sequence[StageCRecord] = (),
) -> Report:
    """Plan section 6.6, the customer envelope on DMS: C1-C3, reach, and the verdict.

    INCONCLUSIVE unless, in at least one leg, the on arm has >= 40 ``doc_hit`` and
    >= 100 non-document asks classed ``router-model``: an envelope from an ask that
    never reached the router says nothing about the router.
    """
    legs = sorted({r.leg for r in (*off, *on)})
    c1: list[str] = []
    c2: list[str] = []
    c3: list[str] = []
    reported: dict[str, Any] = {"same_error": [], "both_green": [], "nondeterministic": []}
    reach_met = False
    for leg in legs:
        offs = _leg_index(off, leg, "off")
        ons = _leg_index(on, leg, "on")
        for rid in sorted(offs.keys() | ons.keys()):
            o, n = offs.get(rid), ons.get(rid)
            if o is None or n is None:
                c1.append(f"{leg} {rid}: asked in one arm only")
                continue
            c1.extend(_c1(leg, o, n, reported))
            item = n.item
            if item.kind in _C2_KINDS:
                if n.green and n.live and not o.green:
                    again = [
                        r for r in reasks
                        if r.item.id == rid and r.leg == leg and r.arm == "off" and r.green
                    ]
                    if again:
                        reported["nondeterministic"].append(f"{leg} {rid}")
                    else:
                        c2.append(f"{leg} {rid}: on-only green {n.body.get('badge')!r}")
                elif n.green and o.green:
                    prose = _is_abstention_prose(
                        _text(n.body), _DMS_ABSTENTION_PREFIXES
                    )
                    reported["both_green"].append(
                        {"leg": leg, "id": rid, "lane": n.reach, "abstention_prose_p0": prose}
                    )
            line = lane_line(n.body) if n.ok else None
            if item.kind != "doc_hit":
                if line is not None and line.lane == "documents":
                    c3.append(f"{leg} {rid}: router-served document on a {item.kind} item")
                if n.names_document_parse_error() and (o.ok or o.status != n.status):
                    c3.append(f"{leg} {rid}: on-only document parse error on a {item.kind} item")
            if line is not None and line.lane == "documents" and (n.green or _rows(n.body)):
                c3.append(f"{leg} {rid}: lane documents envelope is green or carries rows")
        reach = _reach_table(ons.values(), offs.values())
        reported[f"reach:{leg}"] = reach
        doc_hits = sum(1 for r in ons.values() if r.item.kind == "doc_hit" and r.reach == "router-model")
        non_doc = sum(
            1 for r in ons.values() if not r.item.is_document and r.reach == "router-model"
        )
        reported[f"router_model:{leg}"] = {"doc_hit": doc_hits, "non_document": non_doc}
        reach_met = reach_met or (doc_hits >= MIN_DOC_HIT_REACH and non_doc >= MIN_NON_DOC_REACH)
    bars = {"C1": _bar(c1), "C2": _bar(c2), "C3": _bar(c3)}
    if not reach_met:
        bars["reach"] = Bar(
            INCONCLUSIVE,
            (f"no leg reached {MIN_DOC_HIT_REACH} doc_hit and {MIN_NON_DOC_REACH} "
             "non-document router-model asks",),
        )
    else:
        bars["reach"] = Bar(PASS)
    return Report(_verdict(bars), bars, reported)


def _leg_index(records: Iterable[StageCRecord], leg: str, arm: str) -> dict[str, StageCRecord]:
    out: dict[str, StageCRecord] = {}
    for rec in records:
        if rec.leg != leg:
            continue
        if rec.arm != arm:
            raise ValueError(f"{rec.item.id}: record arm {rec.arm!r} passed as {arm!r}")
        if rec.item.id in out:
            raise ValueError(f"{leg}/{arm}: item {rec.item.id} recorded twice")
        out[rec.item.id] = rec
    return out


def _c1(leg: str, o: StageCRecord, n: StageCRecord, reported: dict[str, Any]) -> list[str]:
    rid = n.item.id
    if not o.ok and not n.ok:
        if (o.status, o.code) == (n.status, n.code):
            reported["same_error"].append(f"{leg} {rid}: HTTP {n.status} {n.code}")
            return []
        return [f"{leg} {rid}: off HTTP {o.status} {o.code}, on HTTP {n.status} {n.code}"]
    failures: list[str] = []
    if not n.ok:
        failures.append(f"{leg} {rid}: on-arm HTTP {n.status} {n.code} where the off arm was 200")
    if not o.ok:
        failures.append(f"{leg} {rid}: off-arm HTTP {o.status} {o.code}")
    for rec in (o, n):
        if rec.ok:
            try:
                assert_dms_answer(rec.body)
            except AssertionError as exc:
                failures.append(f"{leg} {rid} {rec.arm}: {exc}")
    return failures


def _reach_table(
    ons: Iterable[StageCRecord], offs: Iterable[StageCRecord]
) -> dict[str, dict[str, Any]]:
    table: dict[str, dict[str, Any]] = {}
    for arm, records in (("on", list(ons)), ("off", list(offs))):
        for rec in records:
            row = table.setdefault(
                f"{arm}:{rec.item.kind}",
                {"asks": 0, "live": 0, "reach": Counter(), "errors": Counter()},
            )
            row["asks"] += 1
            row["live"] += int(rec.live)
            row["reach"][rec.reach] += 1
            if not rec.ok:
                row["errors"][f"{rec.status} {rec.code}"] += 1
    return table
