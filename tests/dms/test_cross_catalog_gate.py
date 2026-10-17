"""Cross-catalog names are refused on every SQL path.

``otherdb.main.<table>`` is a catalog the caller does not hold. The pack path
(no schema_context), the schema_context path, and the other static gates
refuse it. The reason names the gate and does not repeat the SQL.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from packs.dms.security.rate_limit import reset_limiter

INTENT = "how many skus"
PACK_SQL = "SELECT sku FROM otherdb.main.inventory LIMIT 5"
SCHEMA = "\n".join(
    [
        "SCHEMA",
        "- alpha_ledger",
        "- qty integer",
        "- beta_entry",
        "- ref_code integer",
    ]
)
SCHEMA_SQL = "SELECT sum(amount) FROM otherdb.main.alpha_ledger"
NEEDLE = "cross-catalog table reference refused"
HEADERS = {
    "Authorization": "Bearer ov_schema_ctx_test",
    "X-API-Key": "dms-demo-admin-key",
}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "routes.db"))
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    reset_limiter(per_minute=600)
    from CortexOS.api.app import create_app

    return TestClient(create_app(), headers=HEADERS)


def _bind(monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "vault_live": True,
            "local_keys": False,
            "cloud_keys": False,
            "detail": "test-armed",
            "live_5000_ci": False,
        },
    )

    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        text = "use the granted tables" if purpose == "think" else sql
        return {
            "ok": True,
            "text": text,
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        }

    monkeypatch.setattr(fr, "complete", complete)


def _assert_refused(body: dict[str, Any], sql: str) -> None:
    assert body["status"] == "REFUSE", body.get("answer")
    assert NEEDLE in str(body.get("answer") or "")
    assert not body.get("sql_used")
    assert not (body.get("generative") or {}).get("sql")
    blob = json.dumps(body)
    assert sql not in blob
    assert "otherdb" not in blob.lower()


def test_must_fail_cross_catalog_without_schema_context(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pack-only path. otherdb.main.inventory is not the session catalog."""
    _bind(monkeypatch, PACK_SQL)
    response = api.post(
        "/v1/insights",
        json={"intent": INTENT, "ask": False, "generate": True},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    _assert_refused(response.json(), PACK_SQL)


def test_must_fail_cross_catalog_with_schema_context(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """schema_context path. otherdb.main.alpha_ledger is not the caller catalog."""
    _bind(monkeypatch, SCHEMA_SQL)
    response = api.post(
        "/v1/insights",
        json={
            "intent": "count qty on the supplied ledger",
            "ask": False,
            "generate": True,
            "schema_context": SCHEMA,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    _assert_refused(response.json(), SCHEMA_SQL)


def test_pack_gate_and_guardrail_refuse_other_catalog() -> None:
    """The pack check and the guardrail, not only the insights route."""
    from CortexOS.crew.cot_climb import _check_sql
    from CortexOS.dms.sql_guardrail import validate_sql as guard_validate

    class _Open:
        def validate_sql(self, sql, allowed, columns=None):  # noqa: ANN001
            del allowed, columns
            return {"ok": True, "sql": sql, "tables": ["inventory"], "reason": "", "check": "open"}

    pack = _check_sql(_Open(), PACK_SQL, {"source": "pack"}, {"inventory"}, {"inventory": ["sku"]})
    assert pack["ok"] is False
    assert pack["sql"] is None
    assert pack["reason"] == NEEDLE
    assert "otherdb" not in pack["reason"]

    guard = guard_validate(
        PACK_SQL,
        {"tables": {"inventory": {"columns": ["sku"]}}},
    )
    assert guard.passed is False
    assert guard.safe_sql is None
    assert NEEDLE in guard.violations

    own = guard_validate(
        "SELECT q FROM lake.silver.t LIMIT 5",
        {"tables": {"t": {"columns": ["q"]}}, "catalog": "lake"},
    )
    assert own.passed is True
    assert own.safe_sql
