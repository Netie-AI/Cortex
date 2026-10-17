"""VALUE-NORMALIZE-CX-01 (#387): certified sales SQL returns the oracle rows on a mapped lake.

The certified sales queries filter ``txn_type = 'outbound'``, the canonical
value ``packs/dms/ontology/object_types.yaml`` declares. A source writes
``OUT``; every Cortex lake build maps it through that one declaration. Without
the mapping the certified SQL is a silent empty while the envelope still says
it was served (the PR #385 failure).

``test_certified_measure.py::..._rows_equal_the_stored_query`` compares the
served SQL with itself, so its only row check is "non-empty". These tests pin
the rows to a literal oracle written out by hand from the fixture below,
independent of the YAML and of the SQL, and assert the ``POST /v1/insights``
envelope the customer receives.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import insights
from CortexOS.ontology import value_domain
from CortexOS.ontology.registry import pack_dir_for
from packs.dms.security.rate_limit import reset_limiter

ASKS = {
    "cq_sales_top5_value": "Top 5 selling SKUs by revenue",
    "cq_top3_category_sales": "show top 3 category sales",
}

# By hand from _TXNS/_INVENTORY: sale value = quantity_kg * unit_cost_myr over
# the rows the source wrote as OUT. K4 2*100; K1 5*2.5 + 3*2.5; K2 1*10;
# K5 9*1; K6 1*9 (K5 before K6 on the sku tie-break); K7 1*4 is sixth.
# FOOD = K2 + K4; CHEM = K1 (K3 only has an IN row); PACK = K5 + K6; TOOL = K7.
ORACLE: dict[str, list[tuple[str, float]]] = {
    "cq_sales_top5_value": [("K4", 200.0), ("K1", 20.0), ("K2", 10.0), ("K5", 9.0), ("K6", 9.0)],
    "cq_top3_category_sales": [("FOOD", 210.0), ("CHEM", 20.0), ("PACK", 18.0)],
}

_INVENTORY = (
    "('K1','CHEM','S1','L1',10,2.5),('K2','FOOD','S2','L1',4,10),"
    "('K3','CHEM','S3','L2',7,3),('K4','FOOD','S4','L2',1,100),('K5','PACK','S5','L3',50,1),"
    "('K6','PACK','S6','L3',3,9),('K7','TOOL','S1','L1',2,4)"
)
# (txn_id, sku, location_id, txn_type, quantity_kg, unit_cost_myr), as a source writes them.
_TXNS: tuple[tuple[str, str, str, str, float, float], ...] = (
    ("T1", "K1", "L1", "OUT", 5, 2.5),
    ("T2", "K2", "L1", "OUT", 1, 10),
    ("T3", "K3", "L2", "IN", 70, 3),
    ("T4", "K4", "L2", "OUT", 2, 100),
    ("T5", "K5", "L3", "OUT", 9, 1),
    ("T6", "K6", "L3", "OUT", 1, 9),
    ("T7", "K1", "L1", "OUT", 3, 2.5),
    ("T8", "K7", "L1", "OUT", 1, 4),
)


def _domains() -> tuple[value_domain.ValueDomain, ...]:
    return value_domain.load_value_domains(pack_dir_for("dms"))


def _lake(
    txns: tuple[tuple[str, str, str, str, float, float], ...] = _TXNS, *, mapped: bool = True
) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE inventory (sku VARCHAR, category VARCHAR, supplier_id VARCHAR, "
        "location_id VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
    )
    con.execute(f"INSERT INTO inventory VALUES {_INVENTORY}")
    con.execute(
        "CREATE TABLE transactions (txn_id VARCHAR, sku VARCHAR, location_id VARCHAR, "
        "txn_type VARCHAR, quantity_kg DOUBLE, unit_cost_myr DOUBLE)"
    )
    con.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)", list(txns))
    if mapped:
        # The lake build: source spellings to the declared canonical values.
        value_domain.apply_to_lake(con, _domains())
    return con


def _rows(sql: str, con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _stub(prompts: list[str]) -> Any:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        _ = messages, kwargs
        prompts.append(f"{purpose}:{prompt or ''}")
        # The model would sum every movement; it must never be asked.
        return {
            "ok": True,
            "text": "SELECT sku, SUM(quantity_kg * unit_cost_myr) FROM transactions GROUP BY sku",
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "stub", "model": "stub"},
        }

    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> TestClient:
    from CortexOS.crew import freeroute as fr

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setattr(
        fr,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "vault-armed", "live_5000_ci": False},
    )
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    return TestClient(
        create_app(), client=("127.0.0.1", 5555), headers={"X-API-Key": "dms-demo-admin-key"}
    )


def _served_sql(client: TestClient, monkeypatch: pytest.MonkeyPatch, cid: str) -> str:
    from CortexOS.crew import freeroute as fr

    prompts: list[str] = []
    monkeypatch.setattr(fr, "complete", _stub(prompts))
    res = client.post("/v1/insights", json={"intent": ASKS[cid], "ask": False, "generate": True})
    assert res.status_code == 200, res.text
    body = res.json()
    # The customer envelope: served certified SQL, no figure from Cortex, no model.
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["plan_source"] == insights.PLAN_SOURCE_OTHER
    assert body["model_called"] is False
    assert body["served_reason"].startswith(f"certified_query:{cid}")
    assert f"Served certified query {cid}" in body["answer"]
    assert prompts == []
    text = insights.render_tool_text(body)
    assert f"certified_query:{cid}" in text
    assert "plan_source: other" in text
    return str(body["sql_used"])


@pytest.mark.parametrize("cid", sorted(ASKS))
def test_served_certified_sales_rows_equal_the_literal_oracle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cid: str
) -> None:
    sql = _served_sql(client, monkeypatch, cid)
    assert _rows(sql, _lake()) == ORACLE[cid]
    # In addition to the rows: the served SQL pins the declared canonical value.
    assert "txn_type = 'outbound'" in sql


@pytest.mark.parametrize("cid", sorted(ASKS))
def test_served_sql_on_an_unmapped_lake_is_a_silent_empty(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cid: str
) -> None:
    """Must-fail control: the lake mapping is what makes the oracle rows appear."""
    sql = _served_sql(client, monkeypatch, cid)
    assert _rows(sql, _lake(mapped=False)) == []


@pytest.mark.parametrize("cid", sorted(ASKS))
def test_mixed_spelling_lake_is_neither_missed_nor_double_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cid: str
) -> None:
    """Half the sales already canonical, half as the source wrote them."""
    mixed = tuple(
        (t[0], t[1], t[2], "outbound", t[4], t[5]) if t[3] == "OUT" and i % 2 else t
        for i, t in enumerate(_TXNS)
    )
    assert {t[3] for t in mixed} == {"OUT", "outbound", "IN"}
    sql = _served_sql(client, monkeypatch, cid)
    assert _rows(sql, _lake(mixed)) == ORACLE[cid]


@pytest.mark.parametrize("cid", sorted(ASKS))
@pytest.mark.parametrize("spelling", ["Outbound", "out", "SALE"])
def test_undeclared_spelling_is_named_and_not_counted_as_a_sale(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, cid: str, spelling: str
) -> None:
    """Must-fail: a spelling the declaration does not list is not mapped by guesswork.

    The extra row would make K7 / TOOL the top seller if it were folded into
    'outbound'. It is counted and named instead, and the rows stay the oracle.
    """
    txns = (*_TXNS, ("T9", "K7", "L1", spelling, 100.0, 100.0))
    con = _lake(txns)
    assert value_domain.undeclared_values(con, _domains()) == {
        "transactions.txn_type": {spelling: 1}
    }
    assert con.execute("SELECT txn_type FROM transactions WHERE txn_id = 'T9'").fetchone() == (
        spelling,
    )
    sql = _served_sql(client, monkeypatch, cid)
    assert _rows(sql, con) == ORACLE[cid]
