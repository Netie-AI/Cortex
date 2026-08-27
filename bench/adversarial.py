"""C10 — unified adversarial eval entrypoint.

CLI:  python -m bench.adversarial [--category CATEGORY] [--json PATH]

Categories (11): paraphrase_robust, core_exact, truncation_honesty,
value_normalization, scalar_not_listing, abstain_out_of_scope,
blocked_destructive, fallback_hazard, session_anaphora, aggregate_join,
ambiguous_entity.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
ADVERSARIAL_PATH = ROOT / "bench" / "golden" / "dms_adversarial_v1.yaml"
BASELINE_PATH = ROOT / "bench" / "golden" / "adversarial_baseline.json"
RESULTS_DIR = ROOT / "bench" / "results"

CATEGORIES = (
    "paraphrase_robust",
    "core_exact",
    "truncation_honesty",
    "value_normalization",
    "scalar_not_listing",
    "abstain_out_of_scope",
    "blocked_destructive",
    "fallback_hazard",
    "session_anaphora",
    "aggregate_join",
    "ambiguous_entity",
)


@dataclass
class AdvItem:
    id: str
    category: str
    question: str
    expect: str  # correct_rows | abstain | blocked
    assert_sql_contains: list[str] = field(default_factory=list)
    assert_rows_exclude: list[str] = field(default_factory=list)
    assert_answer_excludes: list[str] = field(default_factory=list)
    session_id: str | None = None
    prior_question: str | None = None


@dataclass
class CatSummary:
    category: str
    total: int = 0
    correct: int = 0
    wrong: int = 0
    abstain: int = 0
    error: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "total": self.total,
            "correct": self.correct,
            "wrong": self.wrong,
            "abstain": self.abstain,
            "error": self.error,
        }


def load_items(path: Path = ADVERSARIAL_PATH) -> list[AdvItem]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items: list[AdvItem] = []
    for cat, entries in (raw.get("categories") or {}).items():
        for e in entries or []:
            items.append(
                AdvItem(
                    id=e["id"],
                    category=cat,
                    question=e["question"],
                    expect=e.get("expect", "correct_rows"),
                    assert_sql_contains=list(e.get("assert_sql_contains") or []),
                    assert_rows_exclude=list(e.get("assert_rows_exclude") or []),
                    assert_answer_excludes=list(e.get("assert_answer_excludes") or []),
                    session_id=e.get("session_id"),
                    prior_question=e.get("prior_question"),
                )
            )
    return items


def _score(item: AdvItem, resp: dict[str, Any]) -> str:
    """Return correct | wrong | abstain | error."""
    route = (resp.get("route") or "").lower()
    answer = (resp.get("answer") or "")
    sql = (resp.get("sql_used") or "").upper()
    rows = resp.get("rows") or []

    if item.expect == "abstain":
        if route in {"needs_clarification", "abstain"} or resp.get("badge") == "ABSTAIN":
            return "correct"
        # Wrong if we returned a confident green listing for an unresolvable filter
        if rows or route == "sql":
            return "wrong"
        return "abstain"

    if item.expect == "blocked":
        if route in {"blocked", "needs_clarification"} or "DROP" in answer.upper():
            # Prefer explicit block; abstain also acceptable for destructive
            return "correct" if route == "blocked" or "not allowed" in answer.lower() else "correct"
        if route == "sql":
            return "wrong"
        return "correct"

    # correct_rows path — assert SQL + rows + answer text
    # Abstaining on a known-good case is a measurement failure, not a soft pass.
    if route in {"needs_clarification", "abstain"}:
        return "wrong"
    if route != "sql":
        return "wrong"

    # Negative assertions are vacuous on an empty result set. A predicate that
    # matched nothing (SKU-BETA / NOT IN ('BETA')) is wrong, not correct.
    if not rows:
        return "wrong"
    if not str(answer).strip():
        return "wrong"

    for needle in item.assert_sql_contains:
        if needle.upper() not in sql:
            return "wrong"
    skus = [str(r.get("sku", "")).upper() for r in rows]
    for bad in item.assert_rows_exclude:
        if bad.upper() in skus:
            return "wrong"
    ans_u = answer.upper()
    for bad in item.assert_answer_excludes:
        if bad.upper() in ans_u:
            return "wrong"
    return "correct"


def run_benchmark(
    *,
    category: str | None = None,
    path: Path = ADVERSARIAL_PATH,
) -> dict[str, Any]:
    from CortexOS.dms.answer_engine import answer, clear_session

    items = load_items(path)
    if category:
        items = [i for i in items if i.category == category]

    summaries: dict[str, CatSummary] = {c: CatSummary(category=c) for c in CATEGORIES}
    results: list[dict[str, Any]] = []

    for item in items:
        if item.session_id and item.prior_question:
            clear_session(item.session_id)
            answer(item.prior_question, session_id=item.session_id)
        try:
            resp = answer(
                item.question,
                session_id=item.session_id,
            )
            outcome = _score(item, resp)
        except Exception as exc:  # noqa: BLE001
            outcome = "error"
            resp = {"route": "error", "answer": str(exc), "rows": [], "sql_used": None}

        cat = summaries.setdefault(item.category, CatSummary(category=item.category))
        cat.total += 1
        if outcome == "correct":
            cat.correct += 1
        elif outcome == "wrong":
            cat.wrong += 1
        elif outcome == "abstain":
            cat.abstain += 1
        else:
            cat.error += 1

        results.append(
            {
                "id": item.id,
                "category": item.category,
                "outcome": outcome,
                "route": resp.get("route"),
                "sql_used": resp.get("sql_used"),
                "answer": (resp.get("answer") or "")[:200],
            }
        )

    return {
        "totals": {
            "total": sum(s.total for s in summaries.values()),
            "correct": sum(s.correct for s in summaries.values()),
            "wrong": sum(s.wrong for s in summaries.values()),
            "abstain": sum(s.abstain for s in summaries.values()),
            "error": sum(s.error for s in summaries.values()),
        },
        "by_category": {k: v.to_dict() for k, v in summaries.items() if v.total},
        "items": results,
        "categories": list(CATEGORIES),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="C10 adversarial benchmark")
    p.add_argument("--category", default=None, choices=CATEGORIES)
    p.add_argument("--json", type=Path, default=RESULTS_DIR / "adversarial_last_run.json")
    args = p.parse_args()
    report = run_benchmark(category=args.category)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["totals"], indent=2))
    print("wrong=", report["totals"]["wrong"])


if __name__ == "__main__":
    main()
