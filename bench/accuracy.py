"""DMS answer-accuracy benchmark — the contract behind the 99% program.

Runs the golden question set (bench/golden/dms_golden_v1.yaml) through the real
answer path (`CortexOS.dms.query_service.answer_question`) and scores each item
against canonical SQL executed on the same DuckDB.

Scoring philosophy (from docs/research/findings/NL2SQL_ACCURACY_2026.md):
a wrong answer delivered confidently is the only unacceptable outcome. Abstaining
is a safe miss. So the headline metrics are:
  * answered_precision = correct / (correct + wrong)   ← must approach 1.0
  * coverage           = correct / answerable          ← grows with Q2 layers
  * wrong == 0 is the CI gate for the `core` tier.

CLI:  python -m bench.accuracy [--tier core|target|safety|all] [--json PATH]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_PATH = ROOT / "bench" / "golden" / "dms_golden_v1.yaml"
RESULTS_DIR = ROOT / "bench" / "results"

ABSTAIN_ROUTES = {"needs_clarification"}
DEFAULT_ROUND = 4


@dataclass(slots=True)
class GoldenItem:
    id: str
    tier: str
    question: str
    match: str
    canonical_sql: str | None = None
    key_columns: list[str] = field(default_factory=list)
    round: int = DEFAULT_ROUND
    known_gap: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ItemResult:
    id: str
    tier: str
    outcome: str          # correct | wrong | abstain | error
    detail: str = ""
    route: str = ""
    sql_used: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "tier": self.tier, "outcome": self.outcome,
            "detail": self.detail, "route": self.route, "sql_used": self.sql_used,
        }


@dataclass(slots=True)
class TierSummary:
    tier: str
    total: int = 0
    correct: int = 0
    wrong: int = 0
    abstain: int = 0
    error: int = 0

    @property
    def answered_precision(self) -> float:
        answered = self.correct + self.wrong
        return self.correct / answered if answered else 1.0

    @property
    def coverage(self) -> float:
        return self.correct / self.total if self.total else 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier, "total": self.total, "correct": self.correct,
            "wrong": self.wrong, "abstain": self.abstain, "error": self.error,
            "answered_precision": round(self.answered_precision, 4),
            "coverage": round(self.coverage, 4),
        }


def load_golden(path: Path | str | None = None) -> list[GoldenItem]:
    data = yaml.safe_load(Path(path or GOLDEN_PATH).read_text(encoding="utf-8"))
    items: list[GoldenItem] = []
    for raw in data.get("items", []):
        items.append(GoldenItem(
            id=raw["id"], tier=raw["tier"], question=raw["question"],
            match=raw["match"], canonical_sql=raw.get("canonical_sql"),
            key_columns=list(raw.get("key_columns") or []),
            round=int(raw.get("round", DEFAULT_ROUND)),
            known_gap=raw.get("known_gap", ""), tags=list(raw.get("tags") or []),
        ))
    return items


def _norm_value(value: Any, digits: int) -> Any:
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, int):
        return round(float(value), digits)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _project(rows: list[dict], key_columns: list[str], digits: int) -> list[tuple] | str:
    """Rows → multiset signatures over key_columns. Returns an error string if a
    key column is missing (that is a wrong answer, not a formatting issue)."""
    out: list[tuple] = []
    for row in rows:
        missing = [c for c in key_columns if c not in row]
        if missing:
            return f"missing columns {missing} in answer rows"
        out.append(tuple(_norm_value(row[c], digits) for c in key_columns))
    return out


def _multiset_equal(a: list[tuple], b: list[tuple]) -> bool:
    key = lambda t: tuple(repr(v) for v in t)  # noqa: E731 — mixed-type sort key
    return sorted(a, key=key) == sorted(b, key=key)


def _run_canonical(sql: str) -> list[dict]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    # read_only: the benchmark must not take DuckDB's EXCLUSIVE read-write lock.
    # A live API process holding that lock made canonical SQL fail at random
    # ("IO Error: ... used by another process") — the documented "flaky golden
    # benchmark". Read-only connections share the file.
    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description] if rel.description else []
        return [dict(zip(cols, row)) for row in rel.fetchall()]
    finally:
        con.close()


def _ensure_db_loaded() -> None:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, load_inventory_csv, table_row_counts

    counts = table_row_counts(DEFAULT_DB) if DEFAULT_DB.exists() else {}
    if not counts or sum(counts.values()) == 0:
        load_inventory_csv()


def score_answer(item: GoldenItem, result: dict[str, Any]) -> ItemResult:
    """Score one already-produced answer against gold.

    Split out of ``score_item`` so there is exactly ONE comparison rule and every
    caller reaches it the same way. The live corpus runner used to have no gold
    comparison of its own, so it re-ran the local engine and scored *that* while
    stamping the report ``mode: live`` (EVAL-01, R-0011). The cure is not to give
    the live path a second copy of this logic - two copies can disagree - it is
    to make this one function take the answer as an argument, so an offline
    answer dict and a live DMS envelope are judged by the same code.

    ``result`` needs only the customer-visible keys: ``route``, ``rows``,
    ``sql_used`` and, for ``listing_total``, ``total_count``.
    """
    route = result.get("route", "")
    rows = result.get("rows") or []
    sql_used = result.get("sql_used")

    if item.match == "blocked":
        ok = route == "blocked"
        return ItemResult(item.id, item.tier, "correct" if ok else "wrong",
                          detail="" if ok else f"expected blocked, got route={route}",
                          route=route, sql_used=sql_used)

    if item.match == "abstain":
        if route in ABSTAIN_ROUTES:
            return ItemResult(item.id, item.tier, "correct", route=route)
        return ItemResult(item.id, item.tier, "wrong", route=route, sql_used=sql_used,
                          detail=f"expected abstain, got route={route} with {len(rows)} rows")

    # Answerable item: system abstaining is a safe miss, not a wrong answer.
    if route in ABSTAIN_ROUTES:
        return ItemResult(item.id, item.tier, "abstain", route=route,
                          detail="system abstained on an answerable question")
    if route == "blocked":
        return ItemResult(item.id, item.tier, "wrong", route=route,
                          detail="system blocked a legitimate question")

    # listing_total: a large listing must DISCLOSE its true total, not silently
    # cap. Correct iff the answer's total_count equals the canonical row count.
    if item.match == "listing_total":
        try:
            truth_rows = _run_canonical(item.canonical_sql or "")
        except Exception as exc:  # noqa: BLE001
            return ItemResult(item.id, item.tier, "error", detail=f"canonical SQL failed: {exc!r}")
        expected = len(truth_rows)
        got = result.get("total_count")
        if got == expected:
            return ItemResult(item.id, item.tier, "correct", route=route, sql_used=sql_used)
        return ItemResult(item.id, item.tier, "wrong", route=route, sql_used=sql_used,
                          detail=f"disclosed total {got} != true total {expected} (silent truncation)")

    try:
        truth_rows = _run_canonical(item.canonical_sql or "")
    except Exception as exc:  # noqa: BLE001
        return ItemResult(item.id, item.tier, "error", detail=f"canonical SQL failed: {exc!r}")

    truth = _project(truth_rows, item.key_columns, item.round)
    if isinstance(truth, str):
        return ItemResult(item.id, item.tier, "error", detail=f"golden defect: {truth}")

    if item.match == "scalar":
        if len(rows) != 1 or len(truth_rows) != 1:
            return ItemResult(item.id, item.tier, "wrong", route=route, sql_used=sql_used,
                              detail=f"expected scalar, got {len(rows)} answer rows")
        got = _project(rows, item.key_columns, item.round)
        if isinstance(got, str):
            return ItemResult(item.id, item.tier, "wrong", route=route, sql_used=sql_used, detail=got)
        ok = _multiset_equal(got, truth)
        return ItemResult(item.id, item.tier, "correct" if ok else "wrong", route=route,
                          sql_used=sql_used,
                          detail="" if ok else f"scalar mismatch: got {got}, want {truth}")

    got = _project(rows, item.key_columns, item.round)
    if isinstance(got, str):
        return ItemResult(item.id, item.tier, "wrong", route=route, sql_used=sql_used, detail=got)
    if _multiset_equal(got, truth):
        return ItemResult(item.id, item.tier, "correct", route=route, sql_used=sql_used)
    return ItemResult(
        item.id, item.tier, "wrong", route=route, sql_used=sql_used,
        detail=(f"result-set mismatch: {len(got)} answer rows vs {len(truth)} canonical rows "
                f"on {item.key_columns}"),
    )


def score_item(item: GoldenItem) -> ItemResult:
    """Ask the local engine, then score. The offline entry point."""
    from CortexOS.dms.query_service import answer_question

    try:
        result = answer_question(item.question)
    except Exception as exc:  # noqa: BLE001 - a crash is a benchmark outcome
        return ItemResult(item.id, item.tier, "error", detail=f"answer_question raised: {exc!r}")

    return score_answer(item, result)


def run_benchmark(tier: str = "all", golden_path: Path | str | None = None) -> dict[str, Any]:
    _ensure_db_loaded()
    items = load_golden(golden_path)
    if tier != "all":
        items = [i for i in items if i.tier == tier]

    results = [score_item(item) for item in items]
    tiers: dict[str, TierSummary] = {}
    for r in results:
        summary = tiers.setdefault(r.tier, TierSummary(tier=r.tier))
        summary.total += 1
        if r.outcome == "correct":
            summary.correct += 1
        elif r.outcome == "wrong":
            summary.wrong += 1
        elif r.outcome == "abstain":
            summary.abstain += 1
        else:
            summary.error += 1

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "golden_set": str(golden_path or GOLDEN_PATH),
        "tiers": {t: s.to_dict() for t, s in sorted(tiers.items())},
        "results": [r.to_dict() for r in results],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# DMS accuracy benchmark", "", f"Run: {report['generated_at']}", ""]
    lines += ["| tier | total | correct | wrong | abstain | error | answered precision | coverage |",
              "|---|---|---|---|---|---|---|---|"]
    for t in report["tiers"].values():
        lines.append(
            f"| {t['tier']} | {t['total']} | {t['correct']} | {t['wrong']} | {t['abstain']} "
            f"| {t['error']} | {t['answered_precision']:.2%} | {t['coverage']:.2%} |"
        )
    failures = [r for r in report["results"] if r["outcome"] not in ("correct",)]
    if failures:
        lines += ["", "## Non-correct items", ""]
        for r in failures:
            lines.append(f"- `{r['id']}` ({r['tier']}): **{r['outcome']}** - {r['detail']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", default="all", choices=["core", "target", "safety", "all"])
    parser.add_argument("--json", default=None, help="write full JSON report to this path")
    args = parser.parse_args()

    report = run_benchmark(tier=args.tier)
    print(render_markdown(report))

    out = Path(args.json) if args.json else RESULTS_DIR / "accuracy_last_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON report: {out}")


if __name__ == "__main__":
    main()
