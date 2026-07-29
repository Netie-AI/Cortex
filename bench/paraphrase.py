"""DMS paraphrase-robustness benchmark — the GENERALIZATION contract.

`bench.accuracy` asks the engine the exact questions it was built against. Since
the L1 router is hand-written regex, that score is largely self-confirming: it
measures memorisation, not understanding. This benchmark asks the *same
questions in different words* and scores each paraphrase against the SAME
canonical SQL as its golden parent.

Metrics (per golden item and overall):
  * robustness    = correct paraphrases / paraphrases           ← the headline
  * wrong         = confidently wrong answers                   ← must stay 0
  * abstain       = safe misses (coverage loss, not a lie)
  * brittle items = golden items whose robustness < 1.0

CLI:  python -m bench.paraphrase [--json PATH] [--only GOLDEN_ID]
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from bench.accuracy import (
    GOLDEN_PATH,
    RESULTS_DIR,
    GoldenItem,
    _ensure_db_loaded,
    load_golden,
    score_item,
)

ROOT = Path(__file__).resolve().parents[1]
PARAPHRASE_PATH = ROOT / "bench" / "golden" / "dms_paraphrase_v1.yaml"


def load_paraphrases(path: Path | str | None = None) -> dict[str, list[str]]:
    data = yaml.safe_load(Path(path or PARAPHRASE_PATH).read_text(encoding="utf-8"))
    return {k: list(v or []) for k, v in (data.get("paraphrases") or {}).items()}


def _variant(parent: GoldenItem, question: str, idx: int) -> GoldenItem:
    """A paraphrase inherits its parent's entire correctness contract."""
    return GoldenItem(
        id=f"{parent.id}#p{idx}",
        tier=parent.tier,
        question=question,
        match=parent.match,
        canonical_sql=parent.canonical_sql,
        key_columns=list(parent.key_columns),
        round=parent.round,
        known_gap=parent.known_gap,
        tags=list(parent.tags),
    )


@contextmanager
def _no_skill_capture():
    """A benchmark must never teach the system the answers it is being graded on.
    Query-skill capture is disabled for the whole run and restored after."""
    prev = os.environ.get("DMS_QUERY_SKILL_CAPTURE")
    os.environ["DMS_QUERY_SKILL_CAPTURE"] = "0"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("DMS_QUERY_SKILL_CAPTURE", None)
        else:
            os.environ["DMS_QUERY_SKILL_CAPTURE"] = prev


def run_benchmark(
    *,
    golden_path: Path | str | None = None,
    paraphrase_path: Path | str | None = None,
    only: str | None = None,
) -> dict[str, Any]:
    with _no_skill_capture():
        return _run(golden_path=golden_path, paraphrase_path=paraphrase_path, only=only)


def _run(
    *,
    golden_path: Path | str | None,
    paraphrase_path: Path | str | None,
    only: str | None,
) -> dict[str, Any]:
    _ensure_db_loaded()
    parents = {item.id: item for item in load_golden(golden_path)}
    paraphrases = load_paraphrases(paraphrase_path)

    unknown = sorted(set(paraphrases) - set(parents))
    per_item: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    totals = {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0}

    for gid, variants in sorted(paraphrases.items()):
        if gid in unknown or (only and gid != only):
            continue
        parent = parents[gid]
        counts = {"total": 0, "correct": 0, "wrong": 0, "abstain": 0, "error": 0}
        for idx, question in enumerate(variants, start=1):
            res = score_item(_variant(parent, question, idx))
            counts["total"] += 1
            counts[res.outcome] += 1
            totals["total"] += 1
            totals[res.outcome] += 1
            results.append({**res.to_dict(), "question": question, "golden_id": gid})
        robustness = counts["correct"] / counts["total"] if counts["total"] else 1.0
        per_item.append({
            "golden_id": gid, "tier": parent.tier, "expected_match": parent.match,
            **counts, "robustness": round(robustness, 4),
        })

    answered = totals["correct"] + totals["wrong"]
    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "golden_set": str(golden_path or GOLDEN_PATH),
        "paraphrase_set": str(paraphrase_path or PARAPHRASE_PATH),
        "unknown_golden_ids": unknown,
        "totals": {
            **totals,
            "robustness": round(totals["correct"] / totals["total"], 4) if totals["total"] else 1.0,
            "answered_precision": round(totals["correct"] / answered, 4) if answered else 1.0,
        },
        "per_item": per_item,
        "results": results,
    }


def render_markdown(report: dict[str, Any]) -> str:
    t = report["totals"]
    lines = [
        "# DMS paraphrase-robustness benchmark", "",
        f"Run: {report['generated_at']}", "",
        "| total | correct | wrong | abstain | error | robustness | answered precision |",
        "|---|---|---|---|---|---|---|",
        f"| {t['total']} | {t['correct']} | {t['wrong']} | {t['abstain']} | {t['error']} "
        f"| {t['robustness']:.2%} | {t['answered_precision']:.2%} |",
        "",
    ]
    brittle = [i for i in report["per_item"] if i["robustness"] < 1.0]
    if brittle:
        lines += ["## Brittle intents (robustness < 100%)", "",
                  "| golden id | tier | correct/total | wrong | abstain |", "|---|---|---|---|---|"]
        for i in sorted(brittle, key=lambda x: x["robustness"]):
            lines.append(f"| `{i['golden_id']}` | {i['tier']} | {i['correct']}/{i['total']} "
                         f"| {i['wrong']} | {i['abstain']} |")
        lines.append("")
    wrong = [r for r in report["results"] if r["outcome"] == "wrong"]
    if wrong:
        lines += ["## Confidently WRONG answers (must be zero)", ""]
        for r in wrong:
            lines.append(f"- `{r['golden_id']}` - {r['question']!r} -> {r['detail']}")
        lines.append("")
    missed = [r for r in report["results"] if r["outcome"] == "abstain"]
    if missed:
        lines += ["## Abstained paraphrases (safe misses - the coverage gap)", ""]
        for r in missed:
            lines.append(f"- `{r['golden_id']}` - {r['question']!r}")
    if report["unknown_golden_ids"]:
        lines += ["", "## Unknown golden ids in the paraphrase set", ""]
        lines += [f"- `{g}`" for g in report["unknown_golden_ids"]]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=None, help="write full JSON report to this path")
    parser.add_argument("--only", default=None, help="run one golden id's paraphrases")
    args = parser.parse_args()

    report = run_benchmark(only=args.only)
    print(render_markdown(report))

    out = Path(args.json) if args.json else RESULTS_DIR / "paraphrase_last_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nJSON report: {out}")


if __name__ == "__main__":
    main()
