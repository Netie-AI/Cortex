"""Evaluation read API — ``GET /dms/eval/*`` over the benchmark artifacts on disk.

The engine measures itself in ``bench/``; this surfaces those artifacts so the
product can *show* the evidence instead of asserting it in a slide. Everything
here is a straight read of ``bench/thresholds.yaml`` and ``bench/results/*.json``
— no scoring happens in the route, because a scorer behind an API is a scorer
nobody can re-run offline.

The ``claim`` block is deliberately conservative: it reports the corpus size the
"0 confidently wrong" claim is currently backed by, and refuses to call the claim
supported until the run is green *and* the corpus reaches the target in
``thresholds.yaml``. A green badge on thin evidence is the same class of bug as a
green badge on a wrong number.

No ``packs`` import: RBAC is injected by the registrar, same as the ontology API.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench"
RESULTS = BENCH / "results"

router = APIRouter(prefix="/dms/eval", tags=["eval"])

#: Artifact name → (file, human label, what the run proves).
RUN_FILES: dict[str, tuple[str, str, str]] = {
    "corpus": (
        "corpus_last_run.json",
        "Phase 1 corpus",
        "Hand-written seeds across the 12 failure categories, scored against independent gold SQL.",
    ),
    "live_probe": (
        "live_probe_last_run.json",
        "Live persona probes",
        "Real-user phrasings driven through the DMS envelope end to end (E1-E8 asserted).",
    ),
    "paraphrase": (
        "paraphrase_last_run.json",
        "Paraphrase robustness",
        "The same governed questions reworded — measures routing, not luck.",
    ),
    "accuracy": (
        "accuracy_last_run.json",
        "Golden accuracy",
        "The offline golden set by tier (core / safety / target).",
    ),
    "adversarial": (
        "adversarial_last.json",
        "C10 adversarial",
        "Value normalisation, truncation honesty, scalar-not-listing, out-of-scope abstain.",
    ),
}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _stamp(path: Path) -> str | None:
    if not path.is_file():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _thresholds() -> dict[str, Any]:
    path = BENCH / "thresholds.yaml"
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data if isinstance(data, dict) else {}


def _rate(totals: dict[str, Any], key: str) -> float | None:
    total = totals.get("total")
    if not isinstance(total, int | float) or not total:
        return None
    value = totals.get(key)
    if not isinstance(value, int | float):
        return None
    return round(float(value) / float(total), 4)


def _sum_totals(groups: Any) -> dict[str, int]:
    """Roll per-group counts up to one total. Only the four scoring buckets."""
    out: dict[str, int] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        for key in ("total", "correct", "wrong", "abstain", "error"):
            value = group.get(key)
            if isinstance(value, int | float):
                out[key] = out.get(key, 0) + int(value)
    return out


def _run_view(name: str) -> dict[str, Any]:
    filename, label, proves = RUN_FILES[name]
    path = RESULTS / filename
    data = _read_json(path)
    if data is None:
        return {
            "id": name,
            "label": label,
            "proves": proves,
            "present": False,
            "file": f"bench/results/{filename}",
        }
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    tiers = data.get("tiers")
    if not totals and isinstance(tiers, dict):
        # The golden set reports per-tier only; roll it up so the card is not blank.
        totals = _sum_totals(tiers.values())
    view: dict[str, Any] = {
        "id": name,
        "label": label,
        "proves": proves,
        "present": True,
        "file": f"bench/results/{filename}",
        "generated_at": data.get("generated_at") or _stamp(path),
        "mode": data.get("mode"),
        "totals": totals,
        "passed": data.get("passed"),
        "threshold_violations": data.get("threshold_violations") or [],
        "rates": {
            "correct": _rate(totals, "correct"),
            "wrong": _rate(totals, "wrong"),
            "abstain": _rate(totals, "abstain"),
            "error": _rate(totals, "error"),
        },
    }
    by_cat = data.get("by_category")
    if isinstance(by_cat, dict):
        view["by_category"] = by_cat
    if isinstance(tiers, dict):
        view["tiers"] = tiers
    corpus = data.get("corpus")
    if isinstance(corpus, dict):
        view["corpus"] = corpus
    return view


def _wrong_count(view: dict[str, Any]) -> int:
    totals = view.get("totals") or {}
    value = totals.get("wrong")
    return int(value) if isinstance(value, int | float) else 0


@router.get("/summary")
def eval_summary() -> dict[str, Any]:
    thresholds = _thresholds()
    runs = {name: _run_view(name) for name in RUN_FILES}
    present = [v for v in runs.values() if v["present"]]

    corpus = runs["corpus"]
    sizes = corpus.get("corpus") or {}
    # claim_n counts human-verified items only. Falling back to the scored total
    # would let a corpus of machine-authored paraphrases carry the claim — the
    # denominator has to be questions somebody read. Phase 1a artifacts predate
    # the split and legitimately have no `corpus` block, so their total stands in.
    expanded_n = int(sizes.get("expanded_n") or (corpus.get("totals") or {}).get("total") or 0)
    claim_n = int(sizes["claim_n"]) if "claim_n" in sizes else expanded_n
    target = int(((thresholds.get("corpus") or {}).get("target_total")) or 0)
    total_wrong = sum(_wrong_count(v) for v in present)
    all_passed = all(v.get("passed") is not False for v in present)
    blockers: list[str] = []
    if total_wrong:
        blockers.append(f"{total_wrong} confidently-wrong answer(s) across the last runs")
    if target and claim_n < target:
        blockers.append(
            f"claim corpus is N={claim_n} human-verified, target is N={target}"
            + (f" ({expanded_n} scored, {expanded_n - claim_n} awaiting review)"
               if expanded_n > claim_n else "")
        )
    if not present:
        blockers.append("no benchmark artifacts on disk — run the bench first")
    if not all_passed:
        blockers.append("at least one run is below its threshold")

    return {
        "thresholds": thresholds,
        "runs": runs,
        "claim": {
            "statement": "0 confidently wrong",
            "supported": not blockers,
            "blockers": blockers,
            "corpus_n": claim_n,
            "corpus_expanded_n": expanded_n,
            "corpus_unverified_n": max(0, expanded_n - claim_n),
            "corpus_target": target,
            "confidently_wrong": total_wrong,
            "phase": thresholds.get("phase"),
        },
    }


@router.get("/runs/{name}")
def eval_run(name: str, limit: int = 200, outcome: str | None = None) -> dict[str, Any]:
    """Item-level detail for one artifact. ``outcome`` filters to e.g. ``wrong``."""
    if name not in RUN_FILES:
        raise HTTPException(status_code=404, detail=f"unknown run: {name}")
    filename, label, proves = RUN_FILES[name]
    data = _read_json(RESULTS / filename)
    if data is None:
        raise HTTPException(status_code=404, detail=f"no artifact at bench/results/{filename}")
    raw = data.get("items") or data.get("results") or []
    items = [row for row in raw if isinstance(row, dict)]
    if outcome:
        wanted = outcome.strip().lower()
        items = [row for row in items if str(row.get("outcome", "")).lower() == wanted]
    return {
        "id": name,
        "label": label,
        "proves": proves,
        "total": len(items),
        "truncated": len(items) > limit,
        "items": items[: max(0, limit)],
    }


def register_eval_routes(app, dependencies: list[Any] | None = None) -> None:
    """Mount the evaluation read API. ``dependencies`` carries the pack's role gate."""
    app.include_router(router, dependencies=dependencies or [])


__all__ = ["register_eval_routes", "router"]
