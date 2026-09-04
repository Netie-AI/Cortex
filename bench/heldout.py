"""C7-04 held-out eval harness.

Scores each item on the customer envelope (answer text + rows + badge/route),
never on generated SQL alone. Classes: correct / abstained / incorrect.

Empty rows on an answerable item are always incorrect (SKU-BETA / G4 class),
even if gold SQL also returned nothing.

The frozen split is ``bench/heldout/c7_heldout_v1.yaml``. CI invokes
``score_fixture`` on a tiny canned envelope set so the gate can go red
without serving L2 or calling the answer engine. ``score_engine`` is the
C7-05 live path: it scores ``answer()`` envelopes and reports G-abs /
G-err / G-env. It does not set ``DMS_L2_ENABLED`` as a process default.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable
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
    g_abs_recall = (must_ok / must_n) if must_n else 1.0
    incorrect_rate = (
        totals["incorrect"] / totals["total"] if totals["total"] else 0.0
    )
    g_env_hits = [
        r.to_dict()
        for r in results
        if r.outcome == "incorrect"
        and (
            "G-env" in r.detail
            or "SKU-BETA" in r.detail
            or r.detail.startswith("empty rows on an answerable")
            or r.detail.startswith("refusal badge with served rows")
        )
    ]
    return {
        "totals": totals,
        "by_split": by_split,
        "g_abs_recall": g_abs_recall,
        "incorrect_rate": incorrect_rate,
        "g_env_violations": len(g_env_hits),
        "gates": {
            "g_abs": g_abs_recall >= 1.0 and must_n > 0,
            "g_err": incorrect_rate < 0.02,
            "g_env": len(g_env_hits) == 0,
        },
        "results": [r.to_dict() for r in results],
    }


def _shadow_target(path: Path | str | None = None) -> Path:
    if path is not None:
        return Path(path)
    env = (os.environ.get("DMS_L2_SHADOW_PATH") or "").strip()
    if env:
        return Path(env)
    from CortexOS.paths import data_path

    return data_path("engine", "l2_shadow.jsonl")


def shadow_line_count(path: Path | str | None = None) -> int:
    """G-sh input. Missing file is 0 lines, not a hidden pass."""
    target = _shadow_target(path)
    if not target.is_file():
        return 0
    n = 0
    with target.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def _add_dev_question(text: Any, seen: set[str], out: list[str]) -> None:
    q = " ".join(str(text or "").split())
    if len(q) < 12:
        return
    key = q.lower()
    if key in seen:
        return
    seen.add(key)
    out.append(q)


def _add_paraphrase_group(group: Any, seen: set[str], out: list[str]) -> None:
    for item in group or []:
        if isinstance(item, str):
            _add_dev_question(item, seen, out)
        elif isinstance(item, dict):
            _add_dev_question(item.get("q") or item.get("question"), seen, out)


def collect_dev_questions(root: Path | None = None) -> list[str]:
    """Distinct operator questions from metrics, certified, golden, corpus."""
    base = root or ROOT
    seen: set[str] = set()
    out: list[str] = []

    metrics = yaml.safe_load((base / "packs/dms/semantic/metrics.yaml").read_text(encoding="utf-8")) or {}
    for metric in metrics.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        _add_dev_question(metric.get("question"), seen, out)
        for syn in metric.get("synonyms") or []:
            _add_dev_question(syn, seen, out)

    certified = yaml.safe_load((base / "packs/dms/semantic/certified_queries.yaml").read_text(encoding="utf-8")) or {}
    for row in certified.get("certified") or []:
        if not isinstance(row, dict):
            continue
        _add_dev_question(row.get("question"), seen, out)
        for syn in row.get("synonyms") or []:
            _add_dev_question(syn, seen, out)

    for rel in (
        "bench/golden/dms_golden_v1.yaml",
        "bench/heldout/c7_heldout_v1.yaml",
    ):
        data = yaml.safe_load((base / rel).read_text(encoding="utf-8")) or {}
        for item in data.get("items") or []:
            if isinstance(item, dict):
                _add_dev_question(item.get("question"), seen, out)

    for rel in (
        "bench/golden/dms_paraphrase_v1.yaml",
        "bench/corpus/paraphrases_v1.yaml",
    ):
        data = yaml.safe_load((base / rel).read_text(encoding="utf-8")) or {}
        paras = data.get("paraphrases") or {}
        if isinstance(paras, dict):
            for group in paras.values():
                _add_paraphrase_group(group, seen, out)

    for rel in (
        "bench/golden/dms_adversarial_v1.yaml",
        "bench/corpus/seeds_v1.yaml",
    ):
        data = yaml.safe_load((base / rel).read_text(encoding="utf-8")) or {}
        cats = data.get("categories") or {}
        if isinstance(cats, dict):
            for group in cats.values():
                for row in group or []:
                    if isinstance(row, dict):
                        _add_dev_question(row.get("question"), seen, out)

    personas = yaml.safe_load((base / "bench/live_personas.yaml").read_text(encoding="utf-8")) or {}
    for persona in (personas.get("personas") or {}).values():
        if not isinstance(persona, dict):
            continue
        for probe in persona.get("probes") or []:
            if isinstance(probe, dict):
                _add_dev_question(probe.get("raw_question"), seen, out)

    return out


def _answer_from_rows(rows: list[Any], fallback: str) -> str:
    bits: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            bits.extend(str(v) for v in row.values())
        else:
            bits.append(str(row))
    return " ".join(bits).strip() or fallback


def _shadow_l2_env(rec: dict[str, Any]) -> dict[str, Any]:
    rows = rec.get("l2_values")
    if not isinstance(rows, list):
        rows = []
    refused = bool(rec.get("l2_refusal_type")) or not rec.get("l2_sql")
    return {
        "answer": _answer_from_rows(rows, "shadow") if rows else "",
        "rows": rows,
        "badge": "abstain" if refused else "L2_VALIDATED",
        "route": "needs_clarification" if refused else "generated",
        "abstained": refused,
    }


def _shadow_served_env(rec: dict[str, Any]) -> dict[str, Any] | None:
    rows = rec.get("served_values")
    if rows is None:
        n = rec.get("served_row_count")
        if n:
            return None
        rows = []
    if not isinstance(rows, list):
        rows = []
    layer = str(rec.get("served_layer") or "")
    badge = str(rec.get("served_badge") or "")
    return {
        "answer": _answer_from_rows(rows, "served") if rows else "",
        "rows": rows,
        "badge": badge,
        "route": layer,
        "abstained": layer in ("abstain", "refused"),
    }


def summarize_shadow(
    path: Path | str | None = None,
    items: list[HeldoutItem] | None = None,
) -> dict[str, Any]:
    """G-sh comparison: line count plus L1-only-correct vs L2-only-correct."""
    target = _shadow_target(path)
    recs: list[dict[str, Any]] = []
    if target.is_file():
        with target.open(encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    recs.append(rec)
    unique = {str(r.get("question") or "").strip() for r in recs}
    unique.discard("")
    refusals: dict[str, int] = {}
    n_agree = 0
    n_l2_sql = 0
    for rec in recs:
        if rec.get("agree") is True:
            n_agree += 1
        if rec.get("l2_sql"):
            n_l2_sql += 1
        key = str(rec.get("l2_refusal_type") or "") or ("sql" if rec.get("l2_sql") else "none")
        refusals[key] = refusals.get(key, 0) + 1

    l1_only_correct = 0
    l2_only_correct = 0
    both_correct = 0
    labeled = 0
    unscored = 0
    by_q = {it.question.strip(): it for it in (items or [])}
    if by_q:
        for rec in recs:
            item = by_q.get(str(rec.get("question") or "").strip())
            if item is None:
                continue
            labeled += 1
            served_env = _shadow_served_env(rec)
            if served_env is None:
                unscored += 1
                continue
            served = score_envelope(item, served_env)
            shadow = score_envelope(item, _shadow_l2_env(rec))
            if served.outcome == "correct" and shadow.outcome == "correct":
                both_correct += 1
            elif served.outcome == "correct":
                l1_only_correct += 1
            elif shadow.outcome == "correct":
                l2_only_correct += 1

    return {
        "n_lines": len(recs),
        "n_unique": len(unique),
        "n_agree": n_agree,
        "n_l2_sql": n_l2_sql,
        "refusals": refusals,
        "l1_only_correct": l1_only_correct,
        "l2_only_correct": l2_only_correct,
        "both_correct": both_correct,
        "labeled": labeled,
        "unscored": unscored,
        "path": str(target),
    }


def replay_shadow(
    questions: list[str] | None = None,
    *,
    shadow_path: Path | str | None = None,
    limit: int | None = None,
    offset: int = 0,
    ask: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Call answer() with SHADOW on and L2 serve off. Restores env."""
    qs = list(questions if questions is not None else collect_dev_questions())
    start = max(0, int(offset))
    qs = qs[start:]
    if limit is not None:
        qs = qs[: max(0, int(limit))]
    target = _shadow_target(shadow_path)
    prev_shadow = os.environ.get("DMS_L2_SHADOW")
    prev_path = os.environ.get("DMS_L2_SHADOW_PATH")
    prev_l2 = os.environ.get("DMS_L2_ENABLED")
    os.environ.pop("DMS_L2_ENABLED", None)
    os.environ["DMS_L2_SHADOW"] = "1"
    os.environ["DMS_L2_SHADOW_PATH"] = str(target)
    live = ask is None
    try:
        if live:
            from bench.accuracy import _ensure_db_loaded
            from CortexOS.dms.answer_engine import answer as ask

            _ensure_db_loaded()
        n = 0
        for question in qs:
            try:
                ask(question)
            except Exception:  # noqa: BLE001
                pass
            n += 1
        report = summarize_shadow(target)
        report["replayed"] = n
        return report
    finally:
        if prev_l2 is None:
            os.environ.pop("DMS_L2_ENABLED", None)
        else:
            os.environ["DMS_L2_ENABLED"] = prev_l2
        if prev_shadow is None:
            os.environ.pop("DMS_L2_SHADOW", None)
        else:
            os.environ["DMS_L2_SHADOW"] = prev_shadow
        if prev_path is None:
            os.environ.pop("DMS_L2_SHADOW_PATH", None)
        else:
            os.environ["DMS_L2_SHADOW_PATH"] = prev_path


def score_engine(
    items: list[HeldoutItem] | None = None,
    *,
    ask: Callable[..., dict[str, Any]] | None = None,
    enable_l2: bool = False,
    count_shadow: bool = True,
) -> dict[str, Any]:
    """Score frozen items against ``answer()``. Does not persist L2-on.

    ``enable_l2=True`` sets ``DMS_L2_ENABLED=1`` for this call only so L2 can
    run on L0/L1 miss. The previous env value is restored. Cutover still
    requires G-abs/G-err/G-env/G-man/G-sh on a real report.
    """
    rows = items if items is not None else load_heldout()
    live_ask = ask is None
    if live_ask:
        from bench.accuracy import _ensure_db_loaded
        from CortexOS.dms.answer_engine import answer as ask

        _ensure_db_loaded()
    prev = os.environ.get("DMS_L2_ENABLED")
    if enable_l2:
        os.environ["DMS_L2_ENABLED"] = "1"
    results: list[HeldoutResult] = []
    try:
        for item in rows:
            try:
                env = ask(item.question)
            except Exception as exc:  # noqa: BLE001 — one item must not abort the report
                results.append(
                    HeldoutResult(
                        item.id,
                        item.split,
                        "incorrect",
                        detail=f"ask raised {type(exc).__name__}: {exc}",
                    )
                )
                continue
            if not isinstance(env, dict):
                env = {}
            results.append(score_envelope(item, env))
    finally:
        if enable_l2:
            if prev is None:
                os.environ.pop("DMS_L2_ENABLED", None)
            else:
                os.environ["DMS_L2_ENABLED"] = prev
    report = summarize(results)
    report["engine"] = True
    report["l2_enabled_for_run"] = enable_l2
    if count_shadow:
        shadow = summarize_shadow(items=rows)
        report["shadow"] = shadow
        report["shadow_lines"] = int(shadow.get("n_lines") or 0)
    else:
        report["shadow"] = {
            "n_lines": 0,
            "l1_only_correct": 0,
            "l2_only_correct": 0,
        }
        report["shadow_lines"] = 0
    report["gates"]["g_sh"] = report["shadow_lines"] >= 500
    report["gates"]["g_man"] = None  # pytest tests/test_execution, not this harness
    report["cutover"] = False
    return report


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
    parser.add_argument(
        "--engine",
        action="store_true",
        help="score frozen items via answer(); does not persist DMS_L2_ENABLED",
    )
    parser.add_argument(
        "--enable-l2",
        action="store_true",
        help="with --engine, set DMS_L2_ENABLED=1 for this process run only",
    )
    parser.add_argument(
        "--shadow-replay",
        action="store_true",
        help="replay dev questions with DMS_L2_SHADOW=1; never sets DMS_L2_ENABLED",
    )
    parser.add_argument(
        "--shadow-report",
        action="store_true",
        help="print summarize_shadow() for --shadow-path or the default JSONL",
    )
    parser.add_argument("--shadow-path", default=None, help="JSONL path for shadow replay/report")
    parser.add_argument("--limit", type=int, default=None, help="cap --shadow-replay questions")
    parser.add_argument("--offset", type=int, default=0, help="skip first N questions on --shadow-replay")
    parser.add_argument("--json", default=None, help="write report JSON")
    args = parser.parse_args()
    if args.shadow_replay:
        report = replay_shadow(
            shadow_path=args.shadow_path, limit=args.limit, offset=args.offset
        )
        print(json.dumps({
            "n_lines": report["n_lines"],
            "n_unique": report["n_unique"],
            "n_l2_sql": report["n_l2_sql"],
            "l1_only_correct": report["l1_only_correct"],
            "l2_only_correct": report["l2_only_correct"],
            "refusals": report["refusals"],
            "replayed": report.get("replayed"),
        }))
    elif args.shadow_report:
        report = summarize_shadow(args.shadow_path, items=load_heldout())
        print(json.dumps(report))
    elif args.engine:
        report = score_engine(enable_l2=args.enable_l2)
        print(json.dumps({
            "totals": report["totals"],
            "gates": report["gates"],
            "cutover": report["cutover"],
            "shadow_lines": report["shadow_lines"],
            "shadow": {
                "n_unique": (report.get("shadow") or {}).get("n_unique"),
                "n_l2_sql": (report.get("shadow") or {}).get("n_l2_sql"),
                "l1_only_correct": (report.get("shadow") or {}).get("l1_only_correct"),
                "l2_only_correct": (report.get("shadow") or {}).get("l2_only_correct"),
            },
        }))
    elif args.fixture:
        report = score_fixture(args.fixture)
        print(json.dumps(report["totals"]))
    else:
        parser.error("pass --fixture PATH, --engine, --shadow-replay, or --shadow-report")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
