"""CX-MT-01 — multi-turn refinement: regroup / filter / year over the prior SQL.

Every case is a two-turn ``answer()`` in one session + space. Assertions are on
the rendered answer text and the returned rows first (CLAUDE.md §8); SQL only
in addition.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from cortex_contract.execution import Manifest

from CortexOS.dms.answer_engine import ABSTAIN, answer, clear_session
from CortexOS.dms.followup_refine import (
    RefineAbstain,
    Refinement,
    apply_refinement,
    detect_refinement,
)
from CortexOS.execution.manifest import VerifiedManifest

STOCK_BY_CATEGORY = "What is total stock value by category?"
TOTAL_REVENUE = "What is total revenue?"
SPACE = "space-refine-a"


@pytest.fixture(scope="module", autouse=True)
def _seeded_db() -> None:
    from bench.accuracy import _ensure_db_loaded

    _ensure_db_loaded()


@pytest.fixture
def session(request) -> str:
    sid = f"refine-{request.node.name}"
    clear_session(sid, space_id=SPACE)
    clear_session(sid, space_id="space-refine-b")
    clear_session(sid)
    yield sid
    clear_session(sid, space_id=SPACE)
    clear_session(sid, space_id="space-refine-b")
    clear_session(sid)


def _direct(sql: str) -> list[dict]:
    from CortexOS.dms.warehouse_db import DEFAULT_DB, get_connection

    con = get_connection(DEFAULT_DB, read_only=True)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=False)) for r in rel.fetchall()]
    finally:
        con.close()


def _verified(grants: dict[str, str]) -> VerifiedManifest:
    when = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="cx-mt-01",
        org_id="acme",
        pool_id="default",
        issuer_key_id="int-1",
        allowed_paths=["/data/pool/acme/**"],
        row_predicates=grants,
        issued_at=when.isoformat(),
        expires_at=(when + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=when)


def _assert_abstained(result: dict) -> None:
    assert result["route"] in (ABSTAIN, "refused")
    assert result["badge"] in ("abstain", "refused")
    assert result["rows"] == []
    assert result["chart_spec"] is None
    assert "Refined the previous answer" not in result["answer"]


# ── regroup ──────────────────────────────────────────────────────────────────
def test_regroup_by_location_keys_rows_by_location_and_keeps_total(session: str) -> None:
    first = answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)
    assert first["rows"] and "category" in first["rows"][0]
    turn1_total = sum(float(r["total_value_myr"]) for r in first["rows"])

    second = answer("and by location?", session_id=session, space_id=SPACE)

    assert second["layer"] == "session"
    assert second["badge"] == "session"
    assert second["route"] == "sql"
    rows = second["rows"]
    assert rows
    assert all(set(r) == {"location_id", "total_value_myr"} for r in rows)
    locations = {r["location_id"] for r in rows}
    assert len(locations) == len(rows) > 1
    # Keyed by the stored location_id encoding (the seed dual-codes some rows).
    direct = _direct("SELECT DISTINCT location_id FROM inventory")
    assert locations == {r["location_id"] for r in direct}
    assert "LOC-001" in locations
    assert sum(float(r["total_value_myr"]) for r in rows) == pytest.approx(turn1_total)
    # Rendered text names what was done and shows location-keyed rows.
    assert "Refined the previous answer (regroup by location_id)" in second["answer"]
    assert "location_id=LOC-" in second["answer"]
    assert "category=" not in second["answer"]
    assert second["assumptions"] == "refined prior turn: regroup by location_id"
    # SQL, in addition.
    assert "GROUP BY location_id" in second["sql_used"]
    assert "category" not in second["sql_used"]


def test_regroup_adds_group_to_ungrouped_aggregate(session: str) -> None:
    first = answer(TOTAL_REVENUE, session_id=session, space_id=SPACE)
    assert len(first["rows"]) == 1
    total = float(first["rows"][0]["revenue_myr"])

    second = answer("by location", session_id=session, space_id=SPACE)

    assert second["badge"] == "session"
    assert len(second["rows"]) > 1
    assert all("location_id" in r for r in second["rows"])
    assert sum(float(r["revenue_myr"]) for r in second["rows"]) == pytest.approx(total, abs=0.05)
    assert "regroup by location_id" in second["answer"]
    assert "GROUP BY location_id" in second["sql_used"]


# ── filter ───────────────────────────────────────────────────────────────────
def test_only_beta_filters_sku_beta_and_matches_direct_query(session: str) -> None:
    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)

    second = answer("only BETA", session_id=session, space_id=SPACE)

    direct = _direct(
        "SELECT category, SUM(quantity_kg * unit_cost_myr) AS total_value_myr "
        "FROM inventory WHERE sku = 'SKU-BETA' GROUP BY category"
    )
    assert direct
    assert second["badge"] == "session"
    assert second["layer"] == "session"
    assert second["rows"]
    assert len(second["rows"]) == len(direct)
    assert second["rows"][0]["category"] == direct[0]["category"]
    assert float(second["rows"][0]["total_value_myr"]) == pytest.approx(
        float(direct[0]["total_value_myr"])
    )
    assert "Refined the previous answer (filter sku = SKU-BETA)" in second["answer"]
    assert f"category={direct[0]['category']}" in second["answer"]
    assert second["assumptions"] == "refined prior turn: filter sku = SKU-BETA"
    assert "sku = 'SKU-BETA'" in second["sql_used"]


def test_filter_on_ungrouped_total_matches_direct_query(session: str) -> None:
    answer(TOTAL_REVENUE, session_id=session, space_id=SPACE)

    second = answer("only sku-00296", session_id=session, space_id=SPACE)

    direct = _direct(
        "SELECT ROUND(COALESCE(SUM(quantity_kg * unit_cost_myr), 0), 2) AS v "
        "FROM transactions WHERE txn_type = 'OUT' AND sku = 'SKU-00296'"
    )[0]["v"]
    assert float(direct) > 0
    assert second["badge"] == "session"
    assert second["rows"] == [{"revenue_myr": pytest.approx(float(direct))}]
    assert str(direct) in second["answer"]
    assert "sku = 'SKU-00296'" in second["sql_used"]


def test_refinements_chain(session: str) -> None:
    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)
    answer("and by location?", session_id=session, space_id=SPACE)

    third = answer("only BETA", session_id=session, space_id=SPACE)

    assert third["badge"] == "session"
    assert third["rows"] == [
        {"location_id": "LOC-001", "total_value_myr": pytest.approx(3600.0)}
    ]
    assert "location_id=LOC-001" in third["answer"]
    assert "GROUP BY location_id" in third["sql_used"]
    assert "sku = 'SKU-BETA'" in third["sql_used"]


def test_unknown_value_abstains_with_empty_rows(session: str) -> None:
    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)

    second = answer("only ZZZ-NOPE", session_id=session, space_id=SPACE)

    _assert_abstained(second)
    assert "ZZZ-NOPE" in second["answer"]
    assert "does not match any stored value" in second["answer"]
    assert second["sql_used"] is None


def test_filter_that_selects_nothing_abstains_not_zero(session: str) -> None:
    """SKU-BETA has no OUT transactions: 'revenue 0' would be a false success."""
    answer(TOTAL_REVENUE, session_id=session, space_id=SPACE)

    second = answer("only BETA", session_id=session, space_id=SPACE)

    _assert_abstained(second)
    assert "no rows once refined" in second["answer"]


def test_unknown_dimension_abstains(session: str) -> None:
    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)

    second = answer("and by planet?", session_id=session, space_id=SPACE)

    _assert_abstained(second)
    assert "planet" in second["answer"]


# ── scope + non-matches ──────────────────────────────────────────────────────
def test_other_space_does_not_see_prior_turn(session: str) -> None:
    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)

    other = answer("and by location?", session_id=session, space_id="space-refine-b")

    assert other["layer"] != "session"
    assert "Refined the previous answer" not in other["answer"]
    assert not str(other.get("assumptions") or "").startswith("refined prior turn")
    assert not any("total_value_myr" in r for r in other["rows"])


@pytest.mark.parametrize(
    "question",
    ["stock by location last month", "sales by region last month"],
)
def test_fresh_question_is_not_a_refinement(session: str, question: str) -> None:
    assert detect_refinement(question) is None

    cold = answer(question, session_id=session, space_id=SPACE)
    assert cold["layer"] != "session"
    assert "Refined the previous answer" not in cold["answer"]

    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)
    warm = answer(question, session_id=session, space_id=SPACE)
    assert warm["layer"] != "session"
    assert "Refined the previous answer" not in warm["answer"]
    assert not str(warm.get("assumptions") or "").startswith("refined prior turn")
    assert not any("total_value_myr" in r for r in warm["rows"])


# ── manifest ─────────────────────────────────────────────────────────────────
def test_refinement_runs_through_manifest_when_granted(session: str) -> None:
    grant = _verified({"inventory": "TRUE"})
    first = answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE, verified=grant)
    assert first["rows"]

    second = answer("only BETA", session_id=session, space_id=SPACE, verified=grant)

    assert second["badge"] == "session"
    assert second["rows"] == [
        {"category": "FOOD_DRY", "total_value_myr": pytest.approx(3600.0)}
    ]
    assert "category=FOOD_DRY" in second["answer"]


def test_refinement_outside_session_manifest_is_refused(session: str) -> None:
    answer(STOCK_BY_CATEGORY, session_id=session, space_id=SPACE)

    narrow = _verified({"transactions": "TRUE"})
    regroup = answer("and by location?", session_id=session, space_id=SPACE, verified=narrow)
    _assert_abstained(regroup)
    assert regroup["badge"] == "refused"
    assert "inventory" in regroup["answer"]

    filt = answer("only BETA", session_id=session, space_id=SPACE, verified=narrow)
    _assert_abstained(filt)
    assert filt["badge"] == "refused"


# ── pure rewrite ─────────────────────────────────────────────────────────────
def test_apply_refinement_abstains_on_join_and_ambiguity() -> None:
    joined = (
        "SELECT l.location_code, SUM(s.cost_myr) AS c FROM shipments s "
        "JOIN locations l ON s.destination_location_id = l.location_id "
        "GROUP BY l.location_code"
    )
    with pytest.raises(RefineAbstain):
        apply_refinement(joined, Refinement("regroup", "carrier"), columns=["carrier"], value_index={})
    with pytest.raises(RefineAbstain, match="more than one"):
        apply_refinement(
            "SELECT SUM(x) AS s FROM t",
            Refinement("filter", "beta"),
            columns=["sku", "bin"],
            value_index={"sku": ["SKU-BETA"], "bin": ["BETA-01"]},
        )
    sql = apply_refinement(
        "SELECT SUM(x) AS s FROM t",
        Refinement("filter", " sku-beta "),
        columns=["sku"],
        value_index={"sku": ["SKU-BETA", "SKU-ALPHA"]},
    )
    assert sql == "SELECT SUM(x) AS s FROM t WHERE sku = 'SKU-BETA'"
