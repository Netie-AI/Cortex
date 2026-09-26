"""Generated SQL is checked against the EXECUTING warehouse's columns, not only the ontology.

Live prove (aa98768, env-direct armed): the packs/dms ontology lists
``transactions.timestamp``, ``shipments.supplier_id``, ``locations.location_name``
… and the crew static check accepted model SQL using them because it compares
against the ontology's column list. The DMS warehouse has ``ts`` / no
``supplier_id`` / ``name``, so DMS's EXPLAIN raised BinderException on 10 of 18
model answers. Cortex must refuse that SQL itself, with the missing column named,
instead of handing DMS SQL its warehouse cannot bind.

The warehouse here mirrors the DMS demo serving schema (dms
packages/executor/dms_executor/demo_warehouse.py, SCHEMA_VERSION 5), trimmed to
the tables these questions rank. The model is a fake transport.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from CortexOS.crew import insights
from CortexOS.execution.warehouse import close_cached_connections

BAD_SQL = (
    "SELECT date_trunc('month', timestamp) AS month, SUM(quantity_kg) AS outbound_kg "
    "FROM transactions WHERE txn_type = 'outbound' GROUP BY 1 ORDER BY 1"
)
GOOD_SQL = (
    "SELECT sku, SUM(quantity_kg) AS outbound_kg FROM transactions "
    "WHERE txn_type = 'outbound' GROUP BY sku ORDER BY outbound_kg DESC"
)
INTENT = "Show monthly outbound transactions"


class _Bridge:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, question: str) -> dict[str, Any]:  # pragma: no cover - ask=False
        self.asked.append(question)
        return {"ok": False}


@pytest.fixture()
def dms_shaped_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    db = tmp_path / "dms_serving.duckdb"
    con = duckdb.connect(str(db))
    con.execute(
        "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, location_id VARCHAR, "
        "txn_type VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE, ts TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO transactions VALUES "
        "('T001','RS622XK','WH-A','outbound',400,4.5,'2026-06-02 10:00:00')"
    )
    con.close()
    close_cached_connections()
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(db))
    yield db
    close_cached_connections()


def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )


def _transport(*sql_replies: str):
    """Fake model transport: think turns get prose, SQL turns get the scripted SQL."""
    sql_turns = list(sql_replies)
    seen: list[dict[str, str]] = []

    async def fake_complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        seen.append({"purpose": purpose, "prompt": prompt})
        if purpose == "generative_ask":
            text = sql_turns.pop(0) if len(sql_turns) > 1 else sql_turns[0]
        else:
            text = "Group outbound transactions by month and sum quantity."
        return {
            "ok": True,
            "text": text,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "fake", "model": "fake/model"},
            "stamp": {"line": "FreeRoute crew-insights-sql: fake transport", "served": "fake/model"},
        }

    fake_complete.seen = seen  # type: ignore[attr-defined]
    return fake_complete


@pytest.mark.asyncio
async def test_model_sql_on_a_missing_column_abstains_with_the_column_named(
    monkeypatch, crew_env, dms_shaped_warehouse
) -> None:
    # Precondition: the ontology still lists the column, so the ontology-only
    # check accepts BAD_SQL — exactly the gap the warehouse check closes.
    ranking = insights.retrieve_ontology(INTENT)
    tables = {row["where"]["table"]: row["where"]["columns"] for row in ranking["locations"]}
    assert "timestamp" in tables["transactions"]
    _armed(monkeypatch)
    bridge = _Bridge()
    env = await insights.run_insights(
        INTENT, bridge=bridge, ask=False, generate=True, complete=_transport(BAD_SQL)
    )

    # Nothing answered: no status that claims a result, no values, no rows.
    assert env["status"] == "REFUSE"
    assert env["values"] == []
    assert not env.get("rows")
    assert bridge.asked == []
    gen = env["generative"]
    assert gen["ok"] is False
    assert gen["valid"] is False
    assert gen["sql"] is None
    assert gen["values"] == []
    # No SQL leaves Cortex for DMS to EXPLAIN.
    assert env.get("sql_used") is None
    assert "query_sql" not in env
    assert env["plan_source"] == insights.PLAN_SOURCE_OTHER

    # The named reason, on the envelope and in the rendered text the operator sees.
    reason = str(gen["refuse_reason"])
    assert "column not in executing warehouse" in reason
    assert "transactions.timestamp" in reason
    unsure = " ".join(str(row.get("why") or "") for row in env["validation"]["unsure"])
    assert "transactions.timestamp" in unsure
    text = insights.render_tool_text(env)
    assert "sql_valid: False" in text
    assert "column not in executing warehouse" in text
    assert "transactions.timestamp" in text
    assert "400" not in text  # the seeded row's value never surfaces


@pytest.mark.asyncio
async def test_retry_gets_the_named_column_and_a_bindable_rewrite_passes(
    monkeypatch, crew_env, dms_shaped_warehouse
) -> None:
    _armed(monkeypatch)
    transport = _transport(BAD_SQL, GOOD_SQL)
    env = await insights.run_insights(
        INTENT, bridge=_Bridge(), ask=False, generate=True, complete=transport
    )
    gen = env["generative"]
    assert gen["ok"] is True, gen["refuse_reason"]
    assert gen["valid"] is True
    assert "timestamp" not in str(gen["sql"])
    assert "columns resolved against executing warehouse" in gen["check"]
    # Still not executed: numbers are never certified on this path.
    assert env["status"] == "ABSTAIN"
    assert env["values"] == []
    # The critique the model was re-prompted with named the missing column.
    later = [s["prompt"] for s in transport.seen[2:]]
    assert any("transactions.timestamp" in p for p in later)


@pytest.mark.asyncio
async def test_without_a_warehouse_file_the_check_says_it_did_not_run(
    monkeypatch, crew_env, tmp_path
) -> None:
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "absent.duckdb"))
    _armed(monkeypatch)
    env = await insights.run_insights(
        INTENT, bridge=_Bridge(), ask=False, generate=True, complete=_transport(GOOD_SQL)
    )
    gen = env["generative"]
    assert gen["valid"] is True
    assert "warehouse columns not checked" in gen["check"]
    assert "columns resolved against executing warehouse" not in gen["check"]
