"""INSIGHTS-LANE-INVERSION-01: POST /v1/insights stamps say which lane answered.

No new wire fields. The existing ``plan_source`` / ``model_called`` /
``served_*`` / ``query_sql`` fields must agree with what produced the SQL:

* model lane (the model wrote the SQL that was served, including SQL that
  ``l2_serve`` executed after its gates): ``plan_source=ontology_plan``,
  ``model_called=true``, ``served_model`` is the vault's served model and
  ``served_reason`` does not say the model call was rejected;
* certified lane (``certified_serve`` returned the stored certified SQL and no
  model was called): ``plan_source=other``, ``model_called=false``,
  ``served_model`` null and ``served_reason`` naming the certified query.

Every lane asserts the rendered answer text, the returned values and that the
returned ``sql_used`` gives the oracle rows on the test lake (CLAUDE.md s8).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_contract.execution import Manifest
from fastapi.testclient import TestClient

from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import (
    get_session_registry,
    reset_session_registry_for_tests,
)
from CortexOS.ontology import value_domain
from CortexOS.ontology.registry import pack_dir_for
from packs.dms.security.rate_limit import reset_limiter

SESSION = "insights-lane-stamps"
FIXTURE = Path(__file__).parent / "fixtures" / "c7_05_phase1b_l2_cases.json"
LAKE = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = {row["id"]: row for row in LAKE["cases"]}
VAULT_PROVIDER = "lane-vault-provider"
VAULT_MODEL = "lane-vault-model"
MODEL_LANE = "ontology_plan"
NON_MODEL_LANE = "other"
CERTIFIED_IDENTITY = "cortex:crew:certified-query"


def _stub(sql: str, calls: list[str]) -> Any:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        calls.append(purpose)
        return {
            "ok": True,
            "text": sql,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "requested-only", "model": "requested-only"},
            "stamp": {
                "call_id": f"call-{purpose}",
                "task": "crew-insights-sql",
                "requested": "requested-only",
                "served_provider": VAULT_PROVIDER,
                "served_model": VAULT_MODEL,
                "served_local": True,
                "served_reason": "",
            },
        }

    return fake


def _engine_miss() -> dict[str, Any]:
    return {
        "ok": True,
        "answer": "Engine abstained. No number invented.",
        "badge": "abstain",
        "layer": "l1",
        "metric_id": None,
        "audit_id": None,
        "row_count": 0,
        "rows": [],
        "sources": [],
        "sql_used": None,
        "truncated": False,
    }


@pytest.fixture(scope="module")
def lake_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("lane-stamps") / "lake.duckdb"
    con = duckdb.connect(str(path))
    try:
        for statement in LAKE["setup_sql"]:
            con.execute(statement)
        # VALUE-NORMALIZE-CX-01 (#387): the fixture rows keep the source
        # txn_type spelling; the lake build maps them through the ontology
        # declaration, as every Cortex lake build does.
        value_domain.apply_to_lake(con, value_domain.load_value_domains(pack_dir_for("dms")))
    finally:
        con.close()
    return path


def _rows(lake_db: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(lake_db), read_only=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def _multiset(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(json.dumps(r, sort_keys=True, default=str) for r in rows)


def _bind() -> None:
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    now = datetime.now(timezone.utc)
    get_session_registry().bind(
        VerifiedManifest(
            manifest=Manifest(
                session_id=SESSION,
                org_id="acme",
                pool_id="default",
                issuer_key_id="int-1",
                allowed_paths=["/data/pool/acme/**"],
                row_predicates={
                    "inventory": "1=1",
                    "locations": "1=1",
                    "shipments": "1=1",
                    "suppliers": "1=1",
                    "transactions": "1=1",
                },
                issued_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                signature="not-checked-here",
            ),
            issuer_kid="int-1",
            verified_at=now,
        )
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_session_registry_for_tests()
    yield
    reset_session_registry_for_tests()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lake_db: Path) -> TestClient:
    from CortexOS.crew import freeroute as fr
    from CortexOS.crew.engine_bridge import LocalEngineBridge
    from CortexOS.execution import submit

    async def miss(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return _engine_miss()

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setattr(submit, "DEFAULT_DB", lake_db)
    monkeypatch.setattr(LocalEngineBridge, "ask", miss)
    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    # Loopback peer: the relay tier, so generate runs without a caller ov_ key.
    return TestClient(
        create_app(),
        client=("127.0.0.1", 5555),
        headers={"X-API-Key": "dms-demo-admin-key"},
    )


def _post(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    sql: str,
    *,
    ask: bool,
) -> tuple[dict[str, Any], list[str]]:
    from CortexOS.crew import freeroute as fr

    calls: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(sql, calls))
    res = client.post(
        "/v1/insights",
        json={"intent": question, "ask": ask, "generate": True, "session_id": SESSION},
    )
    assert res.status_code == 200, res.text
    return res.json(), calls


def _lane(body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        body.get("plan_source"),
        body.get("model_called"),
        body.get("served_model"),
        body.get("served_provider"),
    )


@pytest.mark.parametrize("case_id", ["cq_sku_count", "ops_shipment_cost"])
def test_l2_served_model_sql_is_stamped_model_lane(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    lake_db: Path,
    case_id: str,
) -> None:
    """l2_serve executed the model's SQL: the stamps name the model, not a rejection."""
    case = CASES[case_id]
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    _bind()
    body, calls = _post(client, monkeypatch, case["question"], case["sql"], ask=True)

    # User-visible answer: served rows, rendered.
    assert body["status"] == "CERTIFIED", body.get("answer")
    assert body["badge"] == "L2_VALIDATED"
    assert body["layer"] == "generated"
    assert body["answer_step"] == "l2_plausibility"
    assert _multiset(body["values"]) == _multiset(case["oracle"])
    for row in case["oracle"]:
        for key, value in row.items():
            assert f"{key}={value}" in body["answer"]
    # The returned SQL gives the oracle rows on the test lake.
    assert _multiset(_rows(lake_db, body["sql_used"])) == _multiset(case["oracle"])

    # Lane stamps: a model wrote the served SQL.
    assert calls, "the model was called"
    assert body["model_called"] is True
    assert body["plan_source"] == MODEL_LANE
    assert body["query_sql"] == body["sql_used"]
    assert body["served_provider"] == VAULT_PROVIDER
    assert body["served_model"] == VAULT_MODEL
    assert body["served_local"] is True
    reason = str(body.get("served_reason") or "")
    assert "model call rejected" not in reason
    assert "no model called" not in reason
    assert f"plan_source: {MODEL_LANE}" in _tool_text(body)


@pytest.mark.parametrize(
    "case_id", ["cq_sales_top5_value", "cq_capacity_utilisation", "cq_supplier_ranking"]
)
def test_certified_serve_is_stamped_non_model_lane(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    lake_db: Path,
    case_id: str,
) -> None:
    """certified_serve returned stored SQL with no model call: not ontology_plan."""
    case = CASES[case_id]
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    invented = "SELECT 1 AS invented FROM inventory"
    body, calls = _post(client, monkeypatch, case["question"], invented, ask=False)

    # User-visible answer: SQL validated, not executed here; no number invented.
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert f"Served certified query {case_id} as stored; no model called." in body["answer"]
    assert "invented" not in body["sql_used"]
    # The returned SQL gives the oracle rows on the test lake.
    got = _rows(lake_db, body["sql_used"])
    if "ORDER BY" in body["sql_used"].upper():
        assert got == case["oracle"]
    else:
        assert _multiset(got) == _multiset(case["oracle"])

    # Lane stamps: stored SQL, no model.
    assert calls == []
    assert body["model_called"] is False
    assert body["plan_source"] == NON_MODEL_LANE
    assert "query_sql" not in body
    assert body["served_provider"] is None
    assert body["served_model"] is None
    assert body["served_local"] is False
    assert body["served_reason"] == f"certified_query:{case_id} (no model called)"
    assert body["generative"]["check"] == f"certified_query:{case_id}"
    assert body["generative"]["identity"] == CERTIFIED_IDENTITY
    assert body["generative"]["plan_source"] == NON_MODEL_LANE
    text = _tool_text(body)
    assert f"plan_source: {NON_MODEL_LANE}" in text
    assert f"plan_source: {MODEL_LANE}" not in text


def test_model_generated_sql_without_l2_keeps_model_lane(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    lake_db: Path,
) -> None:
    """Positive control: plain model SQL (no certified measure) stays the model lane."""
    case = CASES["cq_sku_count"]
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    body, calls = _post(client, monkeypatch, case["question"], case["sql"], ask=False)

    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert "Validated SQL via FreeRoute" in body["answer"]
    assert _multiset(_rows(lake_db, body["sql_used"])) == _multiset(case["oracle"])
    assert calls
    assert body["model_called"] is True
    assert body["plan_source"] == MODEL_LANE
    assert body["query_sql"] == body["sql_used"]
    assert body["served_model"] == VAULT_MODEL
    assert body["served_provider"] == VAULT_PROVIDER


def test_a_grader_can_tell_the_three_lanes_apart(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model (L2), certified (stored) and no-model engine answers carry distinct stamps."""
    sku = CASES["cq_sku_count"]
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    _bind()
    l2, _ = _post(client, monkeypatch, sku["question"], sku["sql"], ask=True)
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    top5 = CASES["cq_sales_top5_value"]
    certified, _ = _post(client, monkeypatch, top5["question"], sku["sql"], ask=False)

    assert _lane(l2) == (MODEL_LANE, True, VAULT_MODEL, VAULT_PROVIDER)
    assert _lane(certified) == (NON_MODEL_LANE, False, None, None)
    assert str(certified["served_reason"]).startswith("certified_query:")
    assert not str(l2["served_reason"] or "").startswith("certified_query:")


def _tool_text(body: dict[str, Any]) -> str:
    from CortexOS.crew import insights

    return insights.render_tool_text(body)
