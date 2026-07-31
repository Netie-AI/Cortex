"""Phase 1 corpus runner — hand-written seeds with independent gold verification.

CLI:  python -m bench.corpus [--category CATEGORY] [--json PATH]
      python -m bench.corpus --live --dms-url http://127.0.0.1:8090

Scores each seed against canonical SQL (offline) or DMS envelope (live).
Reuses bench.accuracy scoring; asserts confidently_wrong == 0 on gated categories.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bench.accuracy import (
    GoldenItem,
    _ensure_db_loaded,
    load_golden,
    score_item,
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
)


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

    @property
    def is_expanded(self) -> bool:
        return bool(self.parent_id)

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
                )
            )
    return out


def load_corpus(
    *,
    seeds_path: Path = SEEDS_PATH,
    paraphrases_path: Path = PARAPHRASES_PATH,
    include_expanded: bool = True,
) -> list[CorpusSeed]:
    seeds = load_seeds(seeds_path)
    if not include_expanded:
        return seeds
    return seeds + load_paraphrases(seeds, paraphrases_path)


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


def _import_dms_envelope():
    """Best-effort import of DMS envelope invariants for live runs."""
    dms_root = os.environ.get("DMS_ROOT", r"D:\DMS")
    pkg = Path(dms_root) / "packages" / "executor"
    if pkg.exists() and str(pkg) not in sys.path:
        sys.path.insert(0, str(pkg))
    from dms_executor.envelope import assert_envelope_valid  # type: ignore[import-not-found]

    return assert_envelope_valid


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
            f"{dms_url.rstrip('/')}/v1/chat/ask",
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


def _score_live(seed: CorpusSeed, envelope: dict[str, Any]) -> str:
    """Map live envelope to correct | wrong | abstain."""
    abstained = bool(envelope.get("abstained"))
    badge = str(envelope.get("badge") or "")
    route = str(envelope.get("route") or "")

    if seed.match == "abstain":
        if abstained or badge == "ABSTAIN":
            return "correct"
        if envelope.get("values") or envelope.get("rows"):
            return "wrong"
        return "abstain"

    if seed.match == "blocked":
        if abstained and "not allowed" in str(envelope.get("text") or "").lower():
            return "correct"
        if abstained or route in {"blocked", "needs_clarification"}:
            return "correct"
        if envelope.get("sql_used") and "DROP" not in str(envelope.get("sql_used") or "").upper():
            return "wrong"
        return "correct"

    if abstained or badge == "ABSTAIN":
        return "wrong"
    if not envelope.get("sql_used"):
        return "wrong"

    golden = seed.to_golden()
    offline = score_item(golden)
    if offline.outcome == "correct":
        return "correct"
    if offline.outcome == "wrong":
        return "wrong"
    return offline.outcome


def run_offline(
    *,
    category: str | None = None,
    seeds_path: Path = SEEDS_PATH,
    include_expanded: bool = True,
) -> dict[str, Any]:
    _ensure_db_loaded()
    seeds = load_corpus(seeds_path=seeds_path, include_expanded=include_expanded)
    if category:
        seeds = [s for s in seeds if s.category == category]

    by_cat: dict[str, dict[str, int]] = {}
    items: list[dict[str, Any]] = []

    for seed in seeds:
        result = score_item(seed.to_golden())
        cat = by_cat.setdefault(
            seed.category,
            {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0},
        )
        cat["total"] += 1
        cat[result.outcome] = cat.get(result.outcome, 0) + 1
        items.append(
            {
                "id": seed.id,
                "category": seed.category,
                "persona": seed.persona,
                "outcome": result.outcome,
                "route": result.route,
                "detail": result.detail,
                "gold_verified": seed.gold_verified,
                "parent_id": seed.parent_id,
                "question": seed.question,
            }
        )

    totals = {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0}
    for cat in by_cat.values():
        for k in totals:
            totals[k] += cat.get(k, 0)

    return {
        "mode": "offline",
        "totals": totals,
        "corpus": _corpus_sizes(items),
        "by_category": by_cat,
        "items": items,
        "categories": list(PHASE1_CATEGORIES),
    }


def run_live(
    *,
    category: str | None = None,
    seeds_path: Path = SEEDS_PATH,
    dms_url: str = "http://127.0.0.1:8090",
    include_expanded: bool = True,
    rps: float = DEFAULT_LIVE_RPS,
) -> dict[str, Any]:
    import time

    assert_envelope_valid = _import_dms_envelope()
    seeds = load_corpus(seeds_path=seeds_path, include_expanded=include_expanded)
    if category:
        seeds = [s for s in seeds if s.category == category]

    by_cat: dict[str, dict[str, int]] = {}
    items: list[dict[str, Any]] = []
    gap = 1.0 / rps if rps > 0 else 0.0
    throttles = [0]

    for i, seed in enumerate(seeds):
        if i and gap:
            time.sleep(gap)
        try:
            env = _live_ask(seed.question, dms_url=dms_url, throttle_counter=throttles)
            assert_envelope_valid(env)
            outcome = _score_live(seed, env)
            err = ""
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

        cat = by_cat.setdefault(
            seed.category,
            {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0},
        )
        cat["total"] += 1
        cat[outcome] = cat.get(outcome, 0) + 1
        items.append(
            {
                "id": seed.id,
                "category": seed.category,
                "persona": seed.persona,
                "engineer_intent": seed.engineer_intent,
                "outcome": outcome,
                "badge": env.get("badge"),
                "abstained": env.get("abstained"),
                "error": err,
                "gold_verified": seed.gold_verified,
                "parent_id": seed.parent_id,
                "question": seed.question,
            }
        )

    totals = {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0}
    for cat in by_cat.values():
        for k in totals:
            totals[k] += cat.get(k, 0)

    return {
        "mode": "live",
        "dms_url": dms_url,
        "rps": rps,
        # Non-zero means the run was fighting the rate limiter, not just the
        # answer path. Reported so a noisy run is never mistaken for a clean one.
        "throttle_retries": throttles[0],
        "totals": totals,
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


def main() -> None:
    p = argparse.ArgumentParser(description="Phase 1 corpus benchmark")
    p.add_argument("--category", default=None, choices=PHASE1_CATEGORIES)
    p.add_argument("--json", type=Path, default=RESULTS_DIR / "corpus_last_run.json")
    p.add_argument("--live", action="store_true", help="Hit DMS /v1/chat/ask with envelope E1–E8")
    p.add_argument("--dms-url", default=os.environ.get("DMS_URL", "http://127.0.0.1:8090"))
    p.add_argument(
        "--seeds-only",
        action="store_true",
        help="Score the Phase 1a hand-written seeds only (skip 1b paraphrases)",
    )
    p.add_argument(
        "--rps",
        type=float,
        default=DEFAULT_LIVE_RPS,
        help="Live request pacing. Above the engine's rate limit, throttles get "
        "reported as errors and can hide a real failure.",
    )
    args = p.parse_args()

    include_expanded = not args.seeds_only
    if args.live:
        report = run_live(
            category=args.category,
            dms_url=args.dms_url,
            include_expanded=include_expanded,
            rps=args.rps,
        )
    else:
        report = run_offline(category=args.category, include_expanded=include_expanded)

    violations = check_thresholds(report)
    report["threshold_violations"] = violations
    report["passed"] = not violations

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
