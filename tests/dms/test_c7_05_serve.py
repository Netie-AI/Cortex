"""C7-05 — L2 serve only after plausibility; flag-off by default.

Held-out G-sh/G-err are not green on published reports, so this does not
flip DMS_L2_ENABLED as a process default or set bench cutover true.
"""

from __future__ import annotations

import ast
from pathlib import Path

from CortexOS.dms.l2_plausibility import PlausibilityResult

ROOT = Path(__file__).resolve().parents[2]
ANSWER_ENGINE = ROOT / "CortexOS" / "dms" / "answer_engine.py"


class _InventoryPort:
    def is_configured(self) -> bool:
        return True

    def retrieve_schema(self, question: str) -> dict:
        del question
        return {"tables": {"inventory": {}}}

    def generate_candidates(self, question, schema, *, prior_violations=None):
        del question, schema, prior_violations
        return ["SELECT sku FROM inventory LIMIT 5"]

    def record_validated(self, question, sql):
        del question, sql
        return None


def _force_l0_l1_miss(monkeypatch) -> None:
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr(
        "packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False
    )


def _ask_l2(monkeypatch, port, *, enabled: str | None = "1"):
    from CortexOS.dms import l2_generation
    from CortexOS.dms.answer_engine import answer

    if enabled is None:
        monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    else:
        monkeypatch.setenv("DMS_L2_ENABLED", enabled)
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)
    _force_l0_l1_miss(monkeypatch)
    return answer("list some skus from inventory stock")


def test_l2_flag_off_cannot_serve_generated(monkeypatch):
    r = _ask_l2(monkeypatch, _InventoryPort(), enabled=None)
    assert r.get("layer") != "generated"
    assert r.get("badge") != "L2_VALIDATED"
    assert r.get("rows") == []
    assert r["route"] != "sql"


def test_plausibility_trip_stays_abstain_not_l2(monkeypatch):
    from CortexOS.dms import l2_plausibility

    monkeypatch.setattr(
        l2_plausibility,
        "assess_plausibility",
        lambda *a, **k: PlausibilityResult(
            ok=False, code="implausible_empty", reason="empty-success: forced"
        ),
    )
    r = _ask_l2(monkeypatch, _InventoryPort())
    assert r["badge"] == "abstain"
    assert r["layer"] == "abstain"
    assert r["rows"] == []
    assert r.get("sql_used") is None
    assert "empty-success" in (r.get("assumptions") or "")
    assert "empty-success" in (r.get("answer") or "")
    assert r.get("badge") != "L2_VALIDATED"


def test_gated_serve_calls_plausibility_before_l2_validated(monkeypatch):
    from CortexOS.dms import l2_plausibility

    calls: list[int] = []
    real = l2_plausibility.assess_plausibility

    def wrapped(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(l2_plausibility, "assess_plausibility", wrapped)
    r = _ask_l2(monkeypatch, _InventoryPort())
    assert calls, "serve path skipped assess_plausibility"
    assert r["layer"] == "generated"
    assert r["badge"] == "L2_VALIDATED"
    assert r["route"] == "sql"
    rows = r.get("rows") or []
    assert rows
    rendered = r.get("answer") or ""
    assert rendered.strip()
    for row in rows:
        sku = str(row.get("sku") or "")
        assert sku
        assert sku in rendered


def test_answer_engine_stamps_l2_validated_only_after_plausibility():
    """Deleting the plausibility call must not leave an earlier L2_VALIDATED copy."""
    text = ANSWER_ENGINE.read_text(encoding="utf-8")
    start = text.index("def answer(")
    body = text[start:]
    assert "l2_out.badge" not in body
    assert 'badge = "L2_VALIDATED"' in body
    assert body.index("assess_plausibility(") < body.index('badge = "L2_VALIDATED"')
    assert 'layer == "generated" and badge != "L2_VALIDATED"' in body
    assert "promote=False" in body

    tree = ast.parse(text, filename=str(ANSWER_ENGINE))
    leaked = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            leaked.extend(
                alias.name
                for alias in node.names
                if alias.name == "packs" or alias.name.startswith("packs.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "packs" or node.module.startswith("packs."):
                leaked.append(node.module)
    # answer_engine already imports packs.dms.semantic (pre-C2). Do not add generative.
    assert "packs.dms.generative" not in leaked
    src = text
    assert "packs.dms.generative" not in src
    assert "sql_generator" not in src


def test_score_engine_cutover_stays_false_when_l2_flag_on():
    from bench.heldout import HeldoutItem, score_engine

    item = HeldoutItem(
        id="abs",
        split="must_abstain",
        provenance="different_model_abstain",
        question="berlin weather esg 1997",
        expect="abstain",
    )

    def ask(question: str) -> dict:
        del question
        return {
            "answer": "I can't answer that.",
            "rows": [],
            "badge": "abstain",
            "route": "needs_clarification",
        }

    report = score_engine([item], ask=ask, enable_l2=True, count_shadow=False)
    assert report["cutover"] is False
    assert report["l2_enabled_for_run"] is True
    assert report["gates"]["g_sh"] is False
