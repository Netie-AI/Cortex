"""C7-04 held-out eval harness.

Scores each item on the customer envelope (answer text + rows + badge/route),
never on generated SQL alone. Classes: correct / abstained / incorrect.

Empty rows on an answerable item are always incorrect (SKU-BETA / G4 class),
even if gold SQL also returned nothing.

The frozen split is ``bench/heldout/c7_heldout_v1.yaml``. CI invokes
``score_fixture`` on a tiny canned envelope set so the gate can go red
without serving L2 or calling the answer engine.
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
HELDOUT_PATH = ROOT / "bench" / "heldout" / "c7_heldout_v1.yaml"
METRICS_PATH = ROOT / "packs" / "dms" / "semantic" / "metrics.yaml"
GOLDEN_PATH = ROOT / "bench" / "golden" / "dms_golden_v1.yaml"
PARAPHRASE_PATH = ROOT / "bench" / "golden" / "dms_paraphrase_v1.yaml"

OUTCOMES = ("correct", "abstained", "incorrect")
SQL_PROVENANCE = frozenset({"bird_style", "spider_style"})
ABSTAIN_PROVENANCE = "different_model_abstain"
NEAR_COPY = 0.85
MIN_COPY_TOKENS = 4

_REFUSAL_BADGES = frozenset({
    "abstain", "refused", "blocked", "needs_clarification",
})
_REFUSAL_ROUTES = frozenset({
    "needs_clarification", "abstain", "refused", "blocked",
})
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@dataclass(slots=True)
class HeldoutItem:
    id: str
    split: str  # sql | must_abstain
    provenance: str
    question: str
    expect: str  # correct_rows | abstain
    match: str = "resultset"
    canonical_sql: str | None = None
    key_columns: list[str] = field(default_factory=list)
    round: int = 4
    expected_rows: list[dict[str, Any]] | None = None

    @property
    def answerable(self) -> bool:
        return self.expect == "correct_rows"


@dataclass(slots=True)
class HeldoutResult:
    id: str
    split: str
    outcome: str
    detail: str = ""
    badge: str = ""
    route: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "split": self.split,
            "outcome": self.outcome,
            "detail": self.detail,
            "badge": self.badge,
            "route": self.route,
        }


def _norm(text: str) -> str:
    return " ".join(_TOKEN.findall(str(text).lower()))


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(str(text).lower()))


def _jaccard(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def load_heldout(path: Path | str | None = None) -> list[HeldoutItem]:
    data = yaml.safe_load(Path(path or HELDOUT_PATH).read_text(encoding="utf-8")) or {}
    if "paraphrases" in data:
        raise ValueError(
            "held-out file has a paraphrases: key — that is the team-paraphrase "
            "shape from bench/golden/dms_paraphrase_v1.yaml, not this split"
        )
    items_raw = data.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise ValueError("held-out file must have a non-empty items: list")
    items: list[HeldoutItem] = []
    for raw in items_raw:
        expected = raw.get("expected_rows")
        items.append(
            HeldoutItem(
                id=raw["id"],
                split=raw["split"],
                provenance=raw["provenance"],
                question=raw["question"],
                expect=raw["expect"],
                match=raw.get("match", "resultset"),
                canonical_sql=raw.get("canonical_sql"),
                key_columns=list(raw.get("key_columns") or []),
                round=int(raw.get("round", 4)),
                expected_rows=list(expected) if isinstance(expected, list) else None,
            )
        )
    return items


def _phrases_from_metrics(path: Path) -> list[tuple[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, str]] = []
    for metric in data.get("metrics") or []:
        mid = metric.get("id") or "?"
        for syn in metric.get("synonyms") or []:
            out.append((f"metrics.yaml:{mid}", str(syn)))
        name = str(metric.get("name") or "").strip()
        if name:
            out.append((f"metrics.yaml:{mid}:name", name))
    return out


def _phrases_from_golden(path: Path) -> list[tuple[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, str]] = []
    for item in data.get("items") or []:
        q = item.get("question")
        if q:
            out.append((f"dms_golden_v1.yaml:{item.get('id')}", str(q)))
    return out


def _phrases_from_paraphrase(path: Path) -> list[tuple[str, str]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[tuple[str, str]] = []
    for parent, phrases in (data.get("paraphrases") or {}).items():
        for i, phrase in enumerate(phrases or []):
            out.append((f"dms_paraphrase_v1.yaml:{parent}#{i}", str(phrase)))
    return out


def team_dev_phrases(root: Path | None = None) -> list[tuple[str, str]]:
    """Questions/synonyms the held-out set is forbidden to copy."""
    if root is None:
        metrics, golden, paraphrase = METRICS_PATH, GOLDEN_PATH, PARAPHRASE_PATH
    else:
        metrics = root / "packs" / "dms" / "semantic" / "metrics.yaml"
        golden = root / "bench" / "golden" / "dms_golden_v1.yaml"
        paraphrase = root / "bench" / "golden" / "dms_paraphrase_v1.yaml"
    out: list[tuple[str, str]] = []
    out.extend(_phrases_from_metrics(metrics))
    out.extend(_phrases_from_golden(golden))
    out.extend(_phrases_from_paraphrase(paraphrase))
    return out


def overlap_hits(
    questions: list[str],
    forbidden: list[tuple[str, str]] | None = None,
    *,
    root: Path | None = None,
) -> list[str]:
    """Return human-readable hits if questions look like team paraphrases."""
    phrases = forbidden if forbidden is not None else team_dev_phrases(root)
    hits: list[str] = []
    for question in questions:
        qn = _norm(question)
        if not qn:
            continue
        q_tokens = _tokens(question)
        for source, phrase in phrases:
            pn = _norm(phrase)
            if not pn:
                continue
            if qn == pn:
                hits.append(f"{question!r} exact-match {source} ({phrase!r})")
                break
            p_tokens = _tokens(phrase)
            if (
                len(q_tokens) >= MIN_COPY_TOKENS
                and len(p_tokens) >= MIN_COPY_TOKENS
                and _jaccard(question, phrase) >= NEAR_COPY
            ):
                hits.append(
                    f"{question!r} near-copy of {source} ({phrase!r})"
                )
                break
    return hits


def assert_not_team_paraphrases(
    items: list[HeldoutItem] | None = None,
    *,
    root: Path | None = None,
) -> None:
    rows = items if items is not None else load_heldout()
    hits = overlap_hits([i.question for i in rows], root=root)
    if hits:
        raise AssertionError(
            "held-out set overlaps team metrics/golden paraphrases:\n- "
            + "\n- ".join(hits)
        )


def envelope_view(env: dict[str, Any]) -> dict[str, Any]:
    """Normalize engine and DMS envelope spellings to one view."""
    answer = str(env.get("answer") or env.get("text") or "")
    rows = env.get("rows")
    if rows is None:
        rows = env.get("values")
    if not isinstance(rows, list):
        rows = []
    badge = str(env.get("badge") or "")
    route = str(env.get("route") or env.get("layer") or "")
    abstained = env.get("abstained")
    return {
        "answer": answer,
        "rows": rows,
        "badge": badge,
        "route": route,
        "abstained": abstained,
    }


def _is_refusal(view: dict[str, Any]) -> bool:
    badge = view["badge"].lower()
    route = view["route"].lower()
    if view["abstained"] is True:
        return True
    if badge in _REFUSAL_BADGES or route in _REFUSAL_ROUTES:
        return True
    return False


def _gold_rows(item: HeldoutItem) -> list[dict[str, Any]] | str:
    if item.expected_rows is not None:
        return list(item.expected_rows)
    if not item.canonical_sql:
        return "answerable item has no expected_rows and no canonical_sql"
    from bench.accuracy import _ensure_db_loaded, _run_canonical

    _ensure_db_loaded()
    try:
        return _run_canonical(item.canonical_sql)
    except Exception as exc:  # noqa: BLE001
        return f"canonical SQL failed: {exc!r}"


def score_envelope(item: HeldoutItem, env: Any) -> HeldoutResult:
    """Score one served envelope. Does not inspect sql_used as a pass condition."""
    if not isinstance(env, dict):
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail="envelope is not an object",
        )
    view = envelope_view(env)
    badge, route = view["badge"], view["route"]
    rows = view["rows"]
    answer = view["answer"]
    refused = _is_refusal(view)

    if item.expect == "abstain":
        if refused and not rows:
            return HeldoutResult(
                item.id, item.split, "abstained",
                badge=badge, route=route,
            )
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail=(
                f"must-abstain served badge={badge!r} route={route!r} "
                f"rows={len(rows)}"
            ),
            badge=badge, route=route,
        )

    # Answerable: refusal is a safe miss (abstained), not incorrect.
    if refused and not rows:
        return HeldoutResult(
            item.id, item.split, "abstained",
            detail="system abstained on an answerable question",
            badge=badge, route=route,
        )
    if refused and rows:
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail="refusal badge with served rows (G-env)",
            badge=badge, route=route,
        )

    # SKU-BETA class: never treat empty correct_rows as correct.
    if not rows:
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail="empty rows on an answerable item (SKU-BETA / G4 class)",
            badge=badge, route=route,
        )
    if not str(answer).strip():
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail="empty answer text on an answerable item",
            badge=badge, route=route,
        )

    gold = _gold_rows(item)
    if isinstance(gold, str):
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail=gold, badge=badge, route=route,
        )
    if not gold:
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail="gold rows empty — empty correct_rows is never correct",
            badge=badge, route=route,
        )

    from bench.accuracy import _multiset_equal, _project

    keys = list(item.key_columns)
    if not keys:
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail="answerable item missing key_columns",
            badge=badge, route=route,
        )
    truth = _project(gold, keys, item.round)
    got = _project(rows, keys, item.round)
    if isinstance(truth, str):
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail=f"gold defect: {truth}", badge=badge, route=route,
        )
    if isinstance(got, str):
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail=got, badge=badge, route=route,
        )
    if not _multiset_equal(got, truth):
        return HeldoutResult(
            item.id, item.split, "incorrect",
            detail=(
                f"rows mismatch: {len(got)} served vs {len(truth)} gold "
                f"on {keys}"
            ),
            badge=badge, route=route,
        )
    if item.match == "scalar" and gold:
        needle = str(next(iter(gold[0].values())))
        if needle and needle not in answer:
            return HeldoutResult(
                item.id, item.split, "incorrect",
                detail=f"scalar {needle!r} missing from answer text",
                badge=badge, route=route,
            )
    return HeldoutResult(
        item.id, item.split, "correct", badge=badge, route=route,
    )


def summarize(results: list[HeldoutResult]) -> dict[str, Any]:
    totals = {
        "total": len(results),
        "correct": 0,
        "abstained": 0,
        "incorrect": 0,
    }
    by_split = {
        "sql": {"total": 0, "correct": 0, "abstained": 0, "incorrect": 0},
        "must_abstain": {"total": 0, "correct": 0, "abstained": 0, "incorrect": 0},
    }
    for row in results:
        totals[row.outcome] = totals.get(row.outcome, 0) + 1
        bucket = by_split.setdefault(
            row.split, {"total": 0, "correct": 0, "abstained": 0, "incorrect": 0},
        )
        bucket["total"] += 1
        bucket[row.outcome] = bucket.get(row.outcome, 0) + 1
    must = by_split.get("must_abstain") or {"total": 0, "abstained": 0}
    must_n = int(must["total"])
    must_ok = int(must.get("abstained") or 0)
    return {
        "totals": totals,
        "by_split": by_split,
        "g_abs_recall": (must_ok / must_n) if must_n else 1.0,
        "incorrect_rate": (
            totals["incorrect"] / totals["total"] if totals["total"] else 0.0
        ),
        "results": [r.to_dict() for r in results],
    }


def load_fixture(path: Path | str) -> tuple[list[HeldoutItem], dict[str, dict[str, Any]]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    items = load_heldout(path) if "items" in data else []
    envelopes = data.get("envelopes") or {}
    if not isinstance(envelopes, dict):
        raise ValueError("fixture envelopes must be a mapping of id -> envelope")
    return items, envelopes


def score_fixture(path: Path | str) -> dict[str, Any]:
    """CI entry: score canned envelopes. No engine, no L2, no DuckDB."""
    items, envelopes = load_fixture(path)
    results: list[HeldoutResult] = []
    for item in items:
        env = envelopes.get(item.id)
        if env is None:
            results.append(
                HeldoutResult(
                    item.id, item.split, "incorrect",
                    detail="fixture missing envelope",
                )
            )
            continue
        results.append(score_envelope(item, env))
    report = summarize(results)
    report["fixture"] = str(path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default=None,
        help="YAML with items + envelopes (CI path; no live serve)",
    )
    parser.add_argument("--json", default=None, help="write report JSON")
    args = parser.parse_args()
    if not args.fixture:
        parser.error(
            "pass --fixture PATH; live engine scoring is C7-05, not this harness default"
        )
    report = score_fixture(args.fixture)
    print(json.dumps(report["totals"]))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
