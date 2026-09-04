"""Phase 1 corpus runner — hand-written seeds with independent gold verification.

CLI:  python -m bench.corpus [--category CATEGORY] [--json PATH]
      python -m bench.corpus --live   # requires DMS_ASK_URL

Default is seeds-only (the CI gate). Pass --expanded for Phase 1b paraphrases.
Scores each seed against canonical SQL (offline) or a live ask envelope.
Reuses bench.accuracy.score_answer; asserts wrong == 0 and regression == 0.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from bench.accuracy import (
    GoldenItem,
    ItemResult,
    _ensure_db_loaded,
    _run_canonical,
    score_answer,
)
from bench.envelope import (
    ASK_URL_REQUIRED,
    ask_endpoint,
    assert_ask_envelope,
    resolve_ask_url,
)

ROOT = Path(__file__).resolve().parents[1]
SEEDS_PATH = ROOT / "bench" / "corpus" / "seeds_v1.yaml"
PARAPHRASES_PATH = ROOT / "bench" / "corpus" / "paraphrases_v1.yaml"
THRESHOLDS_PATH = ROOT / "bench" / "thresholds.yaml"
RESULTS_DIR = ROOT / "bench" / "results"

PHASE1_CATEGORIES = (
    "grain_fanout",
    "null_semantics",
    "silent_dedup",
    "temporal",
    "rare_sql",
    "unit_currency",
    "semantic_ambiguity",
    "malay_codeswitch",
    "must_abstain",
    "coercion",
    "value_normalization",
    "fallback_hazard",
    # EVAL-01. Added late, and the reason is the whole point of the ticket: all
    # five live P0s in ebd049b..78309fc were the turn AFTER an answer, and this
    # corpus asked 376 single questions. It reported 376/376 wrong=0 through the
    # entire session that produced them. A corpus that cannot express a
    # conversation cannot catch a conversational defect, however many rows it has.
    "conversation",
)

#: Outcome buckets every report carries, offline and live alike. Kept in ONE
#: place because the two runners used to build their counter dicts separately:
#: live omitted "regression", so `check_thresholds` looked at a key that could
#: never be set and the regression gate was structurally dead on a live run.
#: Making the two dicts agree today would have left them free to disagree again
#: tomorrow (R-0004), so they are no longer written twice.
OUTCOME_KEYS = ("total", "correct", "wrong", "abstain", "error", "regression")


@dataclass(slots=True)
class CorpusSeed:
    id: str
    category: str
    question: str
    match: str
    canonical_sql: str | None = None
    key_columns: list[str] = field(default_factory=list)
    round: int = 4
    persona: str = ""
    engineer_intent: str = ""
    tags: list[str] = field(default_factory=list)
    #: Phase 1a seeds are hand-written with independently computed gold, so they
    #: are verified by construction. Phase 1b paraphrases start false and only a
    #: human review flips them — see paraphrases_v1.yaml and bench/verify_gold.py.
    gold_verified: bool = True
    #: Seed id this item paraphrases; empty for the hand-written seeds themselves.
    parent_id: str = ""
    #: Turns asked BEFORE `question`, in one session, to set up the context the
    #: final question depends on. Empty for a single-question seed. This is what
    #: makes "sum of them" scoreable at all: the pronoun has nothing to point at
    #: unless a prior turn put a listing on the screen.
    turns: list[str] = field(default_factory=list)
    #: Substrings that must NOT appear in the rendered answer text. Every one is
    #: a defect signature taken from a real wrong answer ("followup_count = 491").
    #: Rows alone do not close these: the customer reads prose, and CLAUDE.md
    #: section 8 / R-0001 says the gate asserts the artifact they receive.
    answer_must_not_contain: list[str] = field(default_factory=list)

    @property
    def is_expanded(self) -> bool:
        return bool(self.parent_id)

    @property
    def is_conversation(self) -> bool:
        return bool(self.turns)

    def to_golden(self) -> GoldenItem:
        return GoldenItem(
            id=f"{self.category}/{self.id}",
            tier="corpus",
            question=self.question,
            match=self.match,
            canonical_sql=self.canonical_sql,
            key_columns=list(self.key_columns),
            round=self.round,
            tags=list(self.tags) + [self.category],
        )


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_seeds(path: Path = SEEDS_PATH) -> list[CorpusSeed]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    seeds: list[CorpusSeed] = []
    for cat, entries in (raw.get("categories") or {}).items():
        for e in entries or []:
            seeds.append(
                CorpusSeed(
                    id=e["id"],
                    category=cat,
                    question=e["question"],
                    match=e.get("match", "resultset"),
                    canonical_sql=e.get("canonical_sql"),
                    key_columns=list(e.get("key_columns") or []),
                    round=int(e.get("round", 4)),
                    persona=str(e.get("persona") or ""),
                    engineer_intent=str(e.get("engineer_intent") or ""),
                    tags=list(e.get("tags") or []),
                    gold_verified=bool(e.get("gold_verified", True)),
                    turns=[str(t) for t in (e.get("turns") or [])],
                    answer_must_not_contain=[
                        str(t) for t in (e.get("answer_must_not_contain") or [])
                    ],
                )
            )
    return seeds


def load_paraphrases(
    seeds: list[CorpusSeed],
    path: Path = PARAPHRASES_PATH,
) -> list[CorpusSeed]:
    """Expand each seed into its paraphrases, inheriting the parent's gold.

    A paraphrase carries no gold SQL of its own on purpose: if it needed one it
    would be a new seed, not a paraphrase, and the pair could drift apart
    silently. Unknown parent ids are a hard error — a paraphrase pointing at a
    seed that no longer exists is scoring against nothing.
    """
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default_verified = bool(raw.get("default_gold_verified", False))
    by_id = {s.id: s for s in seeds}
    out: list[CorpusSeed] = []

    for parent_id, variants in (raw.get("paraphrases") or {}).items():
        parent = by_id.get(parent_id)
        if parent is None:
            raise ValueError(
                f"{path.name}: '{parent_id}' is not a seed id in {SEEDS_PATH.name}"
            )
        for i, variant in enumerate(variants or []):
            if isinstance(variant, str):
                question, verified = variant, default_verified
            elif isinstance(variant, dict):
                question = str(variant["q"])
                verified = bool(variant.get("gold_verified", default_verified))
            else:
                raise ValueError(f"{path.name}: {parent_id}[{i}] must be a string or mapping")
            out.append(
                CorpusSeed(
                    id=f"{parent_id}#p{i + 1}",
                    category=parent.category,
                    question=question,
                    match=parent.match,
                    canonical_sql=parent.canonical_sql,
                    key_columns=list(parent.key_columns),
                    round=parent.round,
                    persona=parent.persona,
                    engineer_intent=parent.engineer_intent,
                    tags=list(parent.tags),
                    gold_verified=verified,
                    parent_id=parent_id,
                    # A paraphrase rewrites the FINAL question only. The setup
                    # turns and the forbidden-signature list are gold, and gold
                    # is inherited, never authored by the expansion.
                    turns=list(parent.turns),
                    answer_must_not_contain=list(parent.answer_must_not_contain),
                )
            )
    return out


def load_corpus(
    *,
    seeds_path: Path = SEEDS_PATH,
    paraphrases_path: Path = PARAPHRASES_PATH,
    include_expanded: bool = False,
) -> list[CorpusSeed]:
    seeds = load_seeds(seeds_path)
    if not include_expanded:
        return seeds
    return seeds + load_paraphrases(seeds, paraphrases_path)


def new_counters() -> dict[str, int]:
    """The one counter shape. Both runners use it, so neither can drop a key."""
    return dict.fromkeys(OUTCOME_KEYS, 0)


def record_item(
    seed: CorpusSeed,
    *,
    outcome: str,
    regressed: bool,
    by_cat: dict[str, dict[str, int]],
    items: list[dict[str, Any]],
    **extra: Any,
) -> None:
    """The one item-record shape, and the one place a category is tallied.

    Offline and live each used to do this inline. They drifted: live never wrote
    a "regression" field, so `check_thresholds`' `if i.get("regression")` could
    not fire on a live report no matter what the answer path did. Two writers of
    one record is the defect class; one writer is the fix.
    """
    cat = by_cat.setdefault(seed.category, new_counters())
    cat["total"] += 1
    cat[outcome] = cat.get(outcome, 0) + 1
    if regressed:
        cat["regression"] += 1
    items.append(
        {
            "id": seed.id,
            "category": seed.category,
            "persona": seed.persona,
            "engineer_intent": seed.engineer_intent,
            "outcome": outcome,
            "regression": regressed,
            "gold_verified": seed.gold_verified,
            "parent_id": seed.parent_id,
            "turns": list(seed.turns),
            "question": seed.question,
            **extra,
        }
    )


def sum_counters(by_cat: dict[str, dict[str, int]]) -> dict[str, int]:
    totals = new_counters()
    for cat in by_cat.values():
        for k in totals:
            totals[k] += cat.get(k, 0)
    return totals


def _count_totals(items: list[dict[str, Any]], predicate) -> dict[str, int]:
    counts = {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0}
    for item in items:
        if not predicate(item):
            continue
        counts["total"] += 1
        counts[item["outcome"]] = counts.get(item["outcome"], 0) + 1
    return counts


def _corpus_sizes(items: list[dict[str, Any]]) -> dict[str, Any]:
    """The two numbers that must never be conflated.

    ``expanded_n`` is everything scored. ``claim_n`` is only what a human has
    verified — the denominator the "0 confidently wrong" claim is allowed to use.
    Reporting one number for both is how a corpus of 376 machine-written
    questions turns into a claim nobody checked.
    """
    return {
        "expanded_n": len(items),
        "claim_n": sum(1 for i in items if i.get("gold_verified")),
        "seed_n": sum(1 for i in items if not i.get("parent_id")),
        "unverified_n": sum(1 for i in items if not i.get("gold_verified")),
        "claim_totals": _count_totals(items, lambda i: i.get("gold_verified")),
        "expanded_totals": _count_totals(items, lambda i: not i.get("gold_verified")),
    }


# Cortex throttles /dms/* at DMS_RATE_LIMIT_PER_MIN (default 120/min) and a single
# ask spends MORE THAN ONE token — the compliance gate call and the answer call
# both land on /dms/*. A 376-case run fired flat out trips it, and DMS surfaces the
# throttle as `gate_refused` 403 or `submit_failed` 500, which the runner then
# scored as `error`. Dozens of throttles reported as errors is worse than useless:
# it is noise a real failure can hide inside.
#
# So: pace below half the limit, retry a throttle, and COUNT the retries into the
# report. A benchmark that quietly absorbed throttling would be reporting on the
# rate limiter instead of the answer path. To run at speed, raise the engine's
# limit for the run (DMS_RATE_LIMIT_PER_MIN=6000) rather than widening this retry.
DEFAULT_LIVE_RPS = 0.8
_THROTTLE_MARKERS = ("rate limit", "gate_refused", "429", "pool_saturated")


class LiveAskError(RuntimeError):
    """An HTTP failure from the DMS ask, carrying the status so it can be scored.

    A 403 from the compliance gate on a destructive question is the product
    working. Collapsing every non-200 into "error" hid that, and the coercion
    category — the one where a refusal is the whole point — reported as broken.
    """

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"DMS ask HTTP {status}: {body[:400]}")


def _is_gate_refusal(status: int, body: str) -> bool:
    return status == 403 and "gate_refused" in body.lower()


def _is_throttle(status: int, body: str) -> bool:
    if status == 429:
        return True
    low = body.lower()
    if status in (403, 500, 503):
        return any(marker in low for marker in _THROTTLE_MARKERS)
    return False


def _live_ask(
    question: str,
    *,
    dms_url: str,
    session_id: str | None = None,
    retries: int = 3,
    throttle_counter: list[int] | None = None,
) -> dict[str, Any]:
    import time
    import urllib.error
    import urllib.request

    payload: dict[str, Any] = {"question": question}
    if session_id:
        payload["session_id"] = session_id
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            ask_endpoint(dms_url),
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last = f"DMS ask HTTP {exc.code}: {body[:400]}"
            if attempt < retries and _is_throttle(exc.code, body):
                if throttle_counter is not None:
                    throttle_counter[0] += 1
                time.sleep(2.0 * (attempt + 1))  # let the token bucket refill
                continue
            raise LiveAskError(exc.code, body) from exc
    raise RuntimeError(last or "DMS ask failed")


BASELINE_PATH = Path(__file__).resolve().parent / "corpus" / "answering_baseline.json"


def load_answering_baseline(path: Path = BASELINE_PATH) -> frozenset[str]:
    """Ids that answered when the baseline was recorded. Empty if absent."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return frozenset()
    return frozenset(str(i) for i in (raw.get("answering") or []))


def _is_regression(item_id: str, outcome: str, baseline: frozenset[str]) -> bool:
    """Did an item that used to answer stop answering?

    EVAL-01. `abstain` was a free bucket. The only thing bounding it was a rate
    ceiling of 0.38 over 47 claim items, so items could silently flip from
    answer to abstain — three did, from 2026-07-31 — while the gate stayed green
    on `wrong == 0`. An answer that quietly became a refusal is a regression the
    customer feels, and scoring it as neither right nor wrong is how five live
    P0s went uncaught.

    The first attempt at this compared against the *gold* — "gold says
    answerable but it abstained" — and skipped expanded paraphrases on the
    reasoning that they abstain by design. Reintroducing the ANS-01 defect
    proved that wrong: it produced `abstain: 3, regression: 0, PASS`, because
    the three real items were paraphrases (`…#p6`, `…#p3`). An assertion that
    looks right and catches nothing is the thing this ticket is about.

    So the comparison is against recorded behaviour, not against gold. The
    question is not "should this answer?" — which invites pressure to answer
    where abstaining is correct — but "did this change?", which is answerable
    without an opinion. An item enters the baseline only by genuinely
    answering, so the ratchet moves one way.
    """
    return outcome == "abstain" and item_id in baseline


# --- the artifact the customer receives --------------------------------------


def _rendered_forms(value: Any, digits: int) -> set[str]:
    """Every string a gold value could plausibly be rendered as in prose.

    Deliberately generous. A false "the text does not name the answer" would be
    a control refusing legitimate work (R-0005), and the point of the check is
    to catch a number that is *absent*, not one that is formatted unusually.
    """
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bool):
        return {str(value), str(value).lower()}
    if isinstance(value, (_dt.date, _dt.datetime)):
        return {value.isoformat(), str(value)}
    if isinstance(value, int):
        return {str(value), f"{value:,}"}
    if isinstance(value, float):
        r = round(value, digits)
        forms = {f"{r}", f"{r:,}"}
        if r == int(r):
            forms |= {str(int(r)), f"{int(r):,}"}
        return forms
    return {str(value)}


def _text_gate(
    seed: CorpusSeed,
    result: ItemResult,
    answer_text: str,
    truth_rows: list[dict[str, Any]] | None,
) -> ItemResult:
    """Judge the rendered answer, not only the rows (R-0001, CLAUDE.md section 8).

    Two checks, in the order that matters.

    A defect signature in the prose is `wrong` whatever the rows say. Every one
    of the five live P0s put a real number from a different question in front of
    the customer - `followup_count = 491` for a sum, `sku_count = 509` for a
    ranking, `followup_count = 1` after a scalar - and in some of those the
    engine's own row payload was self-consistent. Scoring the payload alone is
    how the corpus stayed at 376/376 through the session that produced them.

    Then: an answer that scored correct but does not NAME its gold value in the
    text is not an answer the customer received. Applied to conversation seeds
    only - their results are a handful of rows, so "the text names them" is a
    fair demand; a 1000-row listing renders a page and would fail it unfairly.
    """
    text = answer_text or ""
    hit = next((tok for tok in seed.answer_must_not_contain if tok and tok in text), None)
    if hit is not None:
        return ItemResult(
            result.id,
            result.tier,
            "wrong",
            detail=f"answer text carries the defect signature {hit!r}: {text[:200]}",
            route=result.route,
            sql_used=result.sql_used,
        )

    if result.outcome != "correct" or not seed.is_conversation or not truth_rows:
        return result

    for row in truth_rows:
        col = next((c for c in (seed.key_columns or list(row)) if c in row), None)
        if col is None:
            continue
        if not any(form in text for form in _rendered_forms(row[col], seed.round)):
            return ItemResult(
                result.id,
                result.tier,
                "wrong",
                detail=(
                    f"rows are right but the answer text never names {col}="
                    f"{row[col]!r}, so the customer did not receive it: {text[:200]}"
                ),
                route=result.route,
                sql_used=result.sql_used,
            )
    return result


def _gold_rows(seed: CorpusSeed) -> list[dict[str, Any]] | None:
    if not seed.canonical_sql:
        return None
    try:
        return _run_canonical(seed.canonical_sql)
    except Exception:  # noqa: BLE001 - a broken gold query is reported by score_answer
        return None


def _run_seed(seed: CorpusSeed) -> dict[str, Any]:
    """Ask the local engine. A conversation seed replays its turns in one session."""
    from CortexOS.dms.answer_engine import answer, clear_session

    if not seed.is_conversation:
        return answer(seed.question)

    session_id = f"corpus-{seed.id}"
    clear_session(session_id)
    for turn in seed.turns:
        answer(turn, session_id=session_id)
    return answer(seed.question, session_id=session_id)


def score_seed_offline(seed: CorpusSeed) -> ItemResult:
    golden = seed.to_golden()
    try:
        result = _run_seed(seed)
    except Exception as exc:  # noqa: BLE001 - a crash is a benchmark outcome
        return ItemResult(golden.id, golden.tier, "error", detail=f"answer path raised: {exc!r}")
    scored = score_answer(golden, result)
    return _text_gate(seed, scored, str(result.get("answer") or ""), _gold_rows(seed))


# --- live scoring -----------------------------------------------------------


class LiveUnscorable(RuntimeError):
    """The live envelope carried nothing to compare against gold.

    Raised, not swallowed. `--live` used to answer this case by re-running the
    LOCAL engine and scoring that, while the report said `"mode": "live"` - the
    report claimed a measurement it had not taken (R-0011). If a live run cannot
    be scored the honest output is a visible error, not a borrowed number.
    """


def _score_live(seed: CorpusSeed, envelope: dict[str, Any]) -> ItemResult:
    """Score the DMS envelope itself. Never re-runs the local answer path.

    Answerable items go through `bench.accuracy.score_answer` - the same
    function the offline runner uses - fed with the envelope's own rows. One
    comparison rule, two sources of answers.
    """
    golden = seed.to_golden()
    abstained = bool(envelope.get("abstained"))
    badge = str(envelope.get("badge") or "")
    route = str(envelope.get("route") or "")
    text = str(envelope.get("text") or envelope.get("answer") or "")

    def _verdict(outcome: str, detail: str = "") -> ItemResult:
        res = ItemResult(golden.id, golden.tier, outcome, detail=detail, route=route,
                         sql_used=envelope.get("sql_used"))
        return _text_gate(seed, res, text, None)

    if seed.match == "abstain":
        if abstained or badge == "ABSTAIN":
            return _verdict("correct")
        if envelope.get("values") or envelope.get("rows"):
            return _verdict("wrong", "answered a question gold says must abstain")
        return _verdict("abstain")

    if seed.match == "blocked":
        if abstained or route in {"blocked", "needs_clarification"}:
            return _verdict("correct")
        if envelope.get("sql_used") and "DROP" not in str(envelope.get("sql_used") or "").upper():
            return _verdict("wrong", "a destructive request was served")
        return _verdict("correct")

    if abstained or badge == "ABSTAIN":
        return _verdict("abstain", "abstained on an answerable question")

    rows = envelope.get("rows")
    if not rows:
        rows = [r for r in (envelope.get("values") or []) if isinstance(r, dict)]
    if not rows:
        raise LiveUnscorable(
            f"{seed.id}: envelope has neither rows nor dict values, so there is "
            f"nothing to compare against gold (badge={badge!r} route={route!r})"
        )

    scored = score_answer(
        golden,
        {
            "route": route or "live",
            "rows": rows,
            "sql_used": envelope.get("sql_used"),
            "total_count": envelope.get("total_count"),
        },
    )
    return _text_gate(seed, scored, text, _gold_rows(seed))


def run_offline(
    *,
    category: str | None = None,
    seeds_path: Path = SEEDS_PATH,
    include_expanded: bool = False,
) -> dict[str, Any]:
    _ensure_db_loaded()
    seeds = load_corpus(seeds_path=seeds_path, include_expanded=include_expanded)
    if category:
        seeds = [s for s in seeds if s.category == category]

    by_cat: dict[str, dict[str, int]] = {}
    items: list[dict[str, Any]] = []
    baseline = load_answering_baseline()

    for seed in seeds:
        result = score_seed_offline(seed)
        record_item(
            seed,
            outcome=result.outcome,
            regressed=_is_regression(seed.id, result.outcome, baseline),
            by_cat=by_cat,
            items=items,
            route=result.route,
            detail=result.detail,
        )

    return {
        "mode": "offline",
        "totals": sum_counters(by_cat),
        "corpus": _corpus_sizes(items),
        "by_category": by_cat,
        "items": items,
        "categories": list(PHASE1_CATEGORIES),
    }


def run_live(
    *,
    category: str | None = None,
    seeds_path: Path = SEEDS_PATH,
    dms_url: str,
    include_expanded: bool = False,
    rps: float = DEFAULT_LIVE_RPS,
) -> dict[str, Any]:
    import time

    # Gold is computed here, from SQL against the warehouse, even in live mode -
    # the answers come off the wire, the truth never does. Without this the live
    # runner had no gold of its own, which is how it ended up borrowing the
    # offline scorer's.
    _ensure_db_loaded()
    seeds = load_corpus(seeds_path=seeds_path, include_expanded=include_expanded)
    if category:
        seeds = [s for s in seeds if s.category == category]

    by_cat: dict[str, dict[str, int]] = {}
    items: list[dict[str, Any]] = []
    gap = 1.0 / rps if rps > 0 else 0.0
    throttles = [0]
    # The same ratchet the offline runner uses. It was absent here entirely, so
    # `check_thresholds` could not see a live answer that had become a refusal.
    baseline = load_answering_baseline()

    for i, seed in enumerate(seeds):
        if i and gap:
            time.sleep(gap)
        try:
            session_id = f"corpus-live-{seed.id}" if seed.is_conversation else None
            for turn in seed.turns:
                if gap:
                    time.sleep(gap)
                _live_ask(
                    turn,
                    dms_url=dms_url,
                    session_id=session_id,
                    throttle_counter=throttles,
                )
            env = _live_ask(
                seed.question,
                dms_url=dms_url,
                session_id=session_id,
                throttle_counter=throttles,
            )
            assert_ask_envelope(env)
            scored = _score_live(seed, env)
            outcome, err = scored.outcome, scored.detail
        except LiveAskError as exc:
            env = {}
            if seed.match == "blocked" and _is_gate_refusal(exc.status, exc.body):
                # The compliance gate refused a destructive request. That is the
                # answer, delivered as an HTTP status instead of an envelope.
                outcome, err = "correct", ""
            else:
                outcome, err = "error", str(exc)[:300]
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            env = {}
            err = str(exc)[:300]

        record_item(
            seed,
            outcome=outcome,
            regressed=_is_regression(seed.id, outcome, baseline),
            by_cat=by_cat,
            items=items,
            badge=env.get("badge"),
            abstained=env.get("abstained"),
            error=err,
            detail=err,
        )

    return {
        "mode": "live",
        "dms_url": dms_url,
        "rps": rps,
        # Non-zero means the run was fighting the rate limiter, not just the
        # answer path. Reported so a noisy run is never mistaken for a clean one.
        "throttle_retries": throttles[0],
        "totals": sum_counters(by_cat),
        "corpus": _corpus_sizes(items),
        "by_category": by_cat,
        "items": items,
        "categories": list(PHASE1_CATEGORIES),
    }


def check_thresholds(report: dict[str, Any], thresholds: dict[str, Any] | None = None) -> list[str]:
    """Return list of threshold violations (empty = pass)."""
    thresholds = thresholds or load_thresholds()
    violations: list[str] = []
    totals = report.get("totals") or {}
    wrong = int(totals.get("wrong") or 0)

    # Confidently wrong is checked across EVERYTHING scored, verified or not. A
    # machine-authored paraphrase that gets a confident wrong answer is a real
    # defect in the answer path; excusing it because nobody reviewed the question
    # would be grading the corpus instead of the engine.
    if wrong > int(thresholds.get("confidently_wrong", 0)):
        violations.append(f"confidently_wrong={wrong} exceeds floor 0")

    # A seed that used to answer and now abstains is a regression, not a free
    # outcome. Checked per item rather than as a rate: a ceiling of 0.38 over 47
    # seeds let three flip unnoticed (EVAL-01). Named individually because
    # "regression=3" sends the reader hunting; the ids do not.
    regressed = [
        str(i.get("id"))
        for i in (report.get("items") or [])
        if i.get("regression")
    ]
    if regressed:
        violations.append(
            f"items that used to answer now abstain ({len(regressed)}): "
            + ", ".join(sorted(regressed))
            + " — fix the answer path, or regenerate the baseline only if the "
            "abstain is genuinely the right behaviour now"
        )

    # Rate floors apply to the human-verified claim set — the population they were
    # calibrated on. Paraphrases abstain more by design; holding them to the seed
    # floor would create pressure to answer where abstaining is correct.
    sizes = report.get("corpus") or {}
    claim_totals = sizes.get("claim_totals") or totals
    claim_total = int(claim_totals.get("total") or 0)
    if claim_total:
        correct_rate = int(claim_totals.get("correct") or 0) / claim_total
        floor = float(thresholds.get("correct_rate_floor", 0))
        if correct_rate < floor:
            violations.append(f"correct_rate={correct_rate:.3f} below floor {floor}")

        abstain_rate = int(claim_totals.get("abstain") or 0) / claim_total
        ceiling = float(thresholds.get("abstain_rate_ceiling", 1))
        if abstain_rate > ceiling:
            violations.append(f"abstain_rate={abstain_rate:.3f} above ceiling {ceiling}")

    expanded_totals = sizes.get("expanded_totals") or {}
    expanded_total = int(expanded_totals.get("total") or 0)
    expanded_ceiling = thresholds.get("expanded_abstain_rate_ceiling")
    if expanded_total and expanded_ceiling is not None:
        rate = int(expanded_totals.get("abstain") or 0) / expanded_total
        if rate > float(expanded_ceiling):
            violations.append(
                f"expanded_abstain_rate={rate:.3f} above ceiling {expanded_ceiling}"
            )

    gated = thresholds.get("gated_categories") or []
    by_cat = report.get("by_category") or {}
    for cat in gated:
        summary = by_cat.get(cat) or {}
        if int(summary.get("wrong") or 0) > 0:
            violations.append(f"gated category {cat}: wrong={summary['wrong']}")

    return violations


def _write_baseline(report: dict[str, Any], *, seeds_only: bool, live: bool) -> None:
    """Record which items answer, refusing to snapshot a broken state.

    Learned the hard way while building this: the first baseline was generated
    by hand from whatever `corpus_last_run.json` happened to hold, and that file
    was the output of a run with a defect deliberately reintroduced. The three
    broken items were therefore recorded as "expected to abstain" — so the gate
    stayed green on exactly the regression it was built to catch.

    A baseline is a claim that the current behaviour is correct. Taking one
    while anything else is failing makes that claim false, so this refuses.
    """
    blocking = [v for v in (report.get("threshold_violations") or []) if "used to answer" not in v]
    if blocking:
        raise SystemExit(
            "refusing to write a baseline from a run with violations:\n  "
            + "\n  ".join(blocking)
        )
    if live:
        raise SystemExit(
            "baseline must come from an offline run — a live run records a "
            "different artifact and would silently retarget the ratchet"
        )

    ids = sorted(str(i["id"]) for i in report["items"] if i.get("outcome") != "abstain")
    regen = "python -m bench.corpus --write-baseline"
    if not seeds_only:
        regen += " --expanded"
    payload = {
        "_comment": (
            "EVAL-01 answering baseline. Every id here answered when recorded. If "
            "one abstains later, an answer the customer used to get silently "
            "became a refusal — a regression the corpus previously scored as free, "
            "bounded only by a 0.38 rate ceiling over 47 claim items."
        ),
        "_regenerate": regen,
        "answering": ids,
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"baseline: recorded {len(ids)} answering items -> {BASELINE_PATH.name}")


def main() -> None:
    p = argparse.ArgumentParser(description="Phase 1 corpus benchmark")
    p.add_argument("--category", default=None, choices=PHASE1_CATEGORIES)
    p.add_argument("--json", type=Path, default=RESULTS_DIR / "corpus_last_run.json")
    p.add_argument("--live", action="store_true", help="Hit DMS_ASK_URL /v1/chat/ask")
    p.add_argument(
        "--dms-url",
        default=None,
        help="Override DMS_ASK_URL. No default host or port.",
    )
    p.add_argument(
        "--expanded",
        action="store_true",
        help="Also score Phase 1b paraphrases (slow; default CI is seeds-only)",
    )
    p.add_argument(
        "--seeds-only",
        action="store_true",
        help="Default. Kept so older flags still mean the CI gate.",
    )
    p.add_argument(
        "--rps",
        type=float,
        default=DEFAULT_LIVE_RPS,
        help="Live request pacing. Above the engine's rate limit, throttles get "
        "reported as errors and can hide a real failure.",
    )
    p.add_argument(
        "--write-baseline",
        action="store_true",
        help="Record which items answer, as the regression baseline. Refuses "
        "unless the run is otherwise clean — a baseline captured while "
        "something is broken locks the breakage in as expected.",
    )
    args = p.parse_args()

    include_expanded = bool(args.expanded) and not args.seeds_only
    if args.live:
        ask_url = resolve_ask_url(args.dms_url)
        if not ask_url:
            print(ASK_URL_REQUIRED, file=sys.stderr)
            raise SystemExit(1)
        report = run_live(
            category=args.category,
            dms_url=ask_url,
            include_expanded=include_expanded,
            rps=args.rps,
        )
    else:
        report = run_offline(category=args.category, include_expanded=include_expanded)

    violations = check_thresholds(report)
    report["threshold_violations"] = violations
    report["passed"] = not violations

    if args.write_baseline:
        _write_baseline(report, seeds_only=not include_expanded, live=args.live)

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"], indent=2))
    sizes = report.get("corpus") or {}
    if sizes:
        print(
            f"corpus: expanded_n={sizes['expanded_n']} "
            f"claim_n={sizes['claim_n']} (human-verified) "
            f"unverified_n={sizes['unverified_n']}"
        )
    if violations:
        print("VIOLATIONS:", violations)
        raise SystemExit(1)
    print("PASS")


if __name__ == "__main__":
    main()
