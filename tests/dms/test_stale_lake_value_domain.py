"""#402 / #235 review finding 1: a lake that does not store the declared values abstains by name.

The DMS ontology declares ``transactions.txn_type`` (canonical ``outbound`` /
``inbound`` / ``ADJUST`` / ``WRITE_OFF``, mapped from the source spellings
``OUT`` / ``IN``) and every Cortex lake build maps it. A lake built before
96da7913, or a ``DMS_WAREHOUSE_DB`` some other loader wrote, still stores
``OUT``: on it ``what is the total revenue`` was served ``governed_metric``
with ``revenue_myr = 0.0`` and ``sales by value top 3`` was served with no rows.

Now SQL that names a declared column runs only on a lake that stores the
declared values: the two query seams every served answer runs through
(``execute_sql``, ``guard_and_execute``) check the lake first. Otherwise the
answer is a named abstain, ``lake_value_domain_stale:transactions.txn_type``,
with no figure and no stored value in the text. The answer tests drive real
routes (``POST /dms/query``, ``POST /v1/insights``, ``POST /v1/contract/ask``)
on a lake built by ``load_inventory_csv`` and assert the rendered answer and
the returned rows (CLAUDE.md section 8); the seam tests call each seam
directly. Oracles are computed in Python from the sample CSVs.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import shutil
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest
from cortex_contract.execution import Manifest
from fastapi.testclient import TestClient

from CortexOS.dms import warehouse_db
from CortexOS.execution import warehouse
from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import (
    get_session_registry,
    reset_session_registry_for_tests,
)
from CortexOS.ontology import value_domain as vd
from tests.test_execution.test_submit_c4 import _signed, issuer, verifier  # noqa: F401

SESSION = "stale-lake-402"
TABLES = ("alerts", "inventory", "locations", "shipments", "suppliers", "transactions")
REVENUE_Q = "what is the total revenue"
TOP3_Q = "sales by value top 3"
STALE = "lake_value_domain_stale:transactions.txn_type"
NOT_MAPPED = "declared source spellings its build did not map"
NOT_DECLARED = "values the declaration does not list"
PHASE1B = json.loads(
    (Path(__file__).parent / "fixtures" / "c7_05_phase1b_l2_cases.json").read_text("utf-8")
)
PHASE1B_CASES = {row["id"]: row for row in PHASE1B["cases"]}


def _domains() -> tuple[vd.ValueDomain, ...]:
    return vd.load_value_domains(warehouse_db.DMS_PACK)


def _txn_domain() -> vd.ValueDomain:
    (dom,) = [d for d in _domains() if d.key == "transactions.txn_type"]
    return dom


# ── oracles: from the CSVs, independent of the YAML and of any SQL ───────────


def _sales() -> list[dict[str, str]]:
    path = warehouse_db.SAMPLES / "transactions_clean.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        # A sale is a row the source system wrote as OUT.
        return [r for r in csv.DictReader(fh) if r["txn_type"] == "OUT"]


def _revenue_oracle() -> float:
    return round(sum(float(r["quantity_kg"]) * float(r["unit_cost_myr"]) for r in _sales()), 2)


def _top3_oracle() -> list[tuple[str, float]]:
    total: dict[str, float] = {}
    for r in _sales():
        total[r["sku"]] = total.get(r["sku"], 0.0) + float(r["quantity_kg"]) * float(
            r["unit_cost_myr"]
        )
    ranked = sorted(total.items(), key=lambda kv: (-round(kv[1], 2), kv[0]))
    return [(sku, round(v, 2)) for sku, v in ranked[:3]]


# ── lakes ────────────────────────────────────────────────────────────────────


def _edit(path: Path, *statements: tuple[str, list[Any]]) -> None:
    warehouse.close_cached_connections()
    con = duckdb.connect(str(path))
    try:
        for sql, params in statements:
            con.execute(sql, params)
    finally:
        con.close()


def _stored(path: Path) -> dict[str, int]:
    warehouse.close_cached_connections()
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute("SELECT txn_type, COUNT(*) FROM transactions GROUP BY 1").fetchall()
    finally:
        con.close()
    return {str(k): int(n) for k, n in rows}


def _unmap(path: Path) -> None:
    """Put the source spellings back, as every build before 96da7913 stored them."""
    _edit(
        path,
        *(
            ("UPDATE transactions SET txn_type = ? WHERE txn_type = ?", [src, canon])
            for src, canon in _txn_domain().sources
        ),
    )


def _insert_sale(path: Path, txn_id: str, spelling: str, kg: float = 1000.0) -> None:
    """Hand-insert one large sale on the top SKU, spelled as given, on an otherwise mapped lake."""
    sku = _top3_oracle()[0][0]
    _edit(
        path,
        (
            "INSERT INTO transactions (txn_id, sku, location_id, txn_type, quantity_kg, "
            "unit_cost_myr, operator_id, timestamp, reference_doc) "
            "SELECT ?, sku, location_id, ?, ?, 1000.0, operator_id, timestamp, reference_doc "
            "FROM transactions WHERE sku = ? LIMIT 1",
            [txn_id, spelling, kg, sku],
        ),
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("stale-lake") / "built.duckdb"
    warehouse_db.load_inventory_csv(db_path=path)
    return path


@pytest.fixture
def lake(built: Path, tmp_path: Path) -> Path:
    """A private copy of a freshly built (mapped) lake; a test may edit it."""
    path = tmp_path / "lake.duckdb"
    shutil.copy(built, path)
    return path


def _bind(sources: tuple[str, ...] = TABLES, *, space_id: str | None = None) -> None:
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    now = datetime.now(timezone.utc)
    get_session_registry().bind(
        VerifiedManifest(
            manifest=Manifest(
                session_id=SESSION,
                org_id="acme",
                space_id=space_id,
                pool_id="default",
                issuer_key_id="int-1",
                allowed_paths=["/data/pool/acme/**"],
                row_predicates={t: "1=1" for t in sources},
                issued_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                signature="not-checked-here",
            ),
            issuer_kid="int-1",
            verified_at=now,
        )
    )


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Any]:
    """``serve(lake)`` points the served answer path at ``lake``; yields an HTTP client."""
    from CortexOS.dms.answer_engine import clear_session
    from CortexOS.execution import submit
    from packs.dms.security.rate_limit import reset_limiter

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    import netie.config

    netie.config._cached_config = None
    reset_limiter(per_minute=10_000)
    vd.clear_lake_cache()
    from CortexOS.api.app import create_app

    client = TestClient(
        create_app(), client=("127.0.0.1", 5555), headers={"X-API-Key": "dms-demo-admin-key"}
    )

    def point(path: Path) -> TestClient:
        monkeypatch.setattr(submit, "DEFAULT_DB", path)
        _bind()
        clear_session(SESSION)
        return client

    yield point
    warehouse.close_cached_connections()
    reset_session_registry_for_tests()
    reset_limiter()
    vd.clear_lake_cache()
    netie.config._cached_config = None


def _dms_query(client: TestClient, question: str) -> dict[str, Any]:
    res = client.post("/dms/query", json={"question": question, "session_id": SESSION})
    assert res.status_code == 200, res.text
    return res.json()


def _insights(client: TestClient, question: str) -> dict[str, Any]:
    res = client.post(
        "/v1/insights",
        json={"intent": question, "ask": True, "generate": False, "session_id": SESSION},
    )
    assert res.status_code == 200, res.text
    return res.json()


_FIGURE = re.compile(r"\d")


def _assert_stale_abstain(body: dict[str, Any], *, kinds: tuple[str, ...] = (NOT_MAPPED,)) -> None:
    """The customer envelope of a stale-lake abstain: named, no figure, no stored value."""
    assert body["badge"] == "abstain", body
    assert body["layer"] == "abstain"
    assert body["rows"] == []
    assert body.get("sql_used") is None
    text = body["answer"]
    assert STALE in text, text
    for kind in kinds:
        assert kind in text, text
    # No figure at all, and never a stored spelling (OUT / IN / the undeclared one).
    assert not _FIGURE.search(text), text
    assert not re.search(r"\b(OUT|IN|Outbound|out|SALE)\b", text), text


# ── /dms/query: the ask route ────────────────────────────────────────────────


def test_mapped_lake_serves_the_oracle_revenue_and_top3(serve: Any, lake: Path) -> None:
    client = serve(lake)
    revenue = _dms_query(client, REVENUE_Q)
    assert revenue["badge"] == "governed_metric", revenue["answer"]
    assert revenue["metric_id"] == "revenue_total"
    (row,) = revenue["rows"]
    assert row["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)
    assert revenue["answer"] == f"Result: revenue_myr = {row['revenue_myr']}"
    assert row["revenue_myr"] > 0

    top3 = _dms_query(client, TOP3_Q)
    assert top3["badge"] == "governed_metric", top3["answer"]
    assert top3["metric_id"] == "sales_by_value"
    want = _top3_oracle()
    assert [r["sku"] for r in top3["rows"]] == [sku for sku, _ in want]
    assert [r["sales_value_myr"] for r in top3["rows"]] == pytest.approx(
        [v for _, v in want], abs=0.011
    )
    for sku, _ in want:
        assert sku in top3["answer"]
    # In addition to rows and text: the served SQL pins the declared canonical value.
    assert "txn_type = 'outbound'" in top3["sql_used"]


@pytest.mark.parametrize("question", [REVENUE_Q, TOP3_Q])
def test_stale_lake_abstains_by_name_with_no_figure(serve: Any, lake: Path, question: str) -> None:
    _unmap(lake)
    assert set(_stored(lake)) == {"OUT", "IN", "ADJUST", "WRITE_OFF"}
    body = _dms_query(serve(lake), question)
    _assert_stale_abstain(body)
    assert body["grant_kind"] == "session"
    assert str(_revenue_oracle()) not in body["answer"]


@pytest.mark.parametrize("question", [REVENUE_Q, TOP3_Q])
def test_one_hand_inserted_source_row_is_enough_to_abstain(
    serve: Any, lake: Path, question: str
) -> None:
    """A mapped lake plus one OUT row a loader wrote later: the build is no longer whole."""
    _insert_sale(lake, "TXN-HAND-1", "OUT")
    assert _stored(lake)["OUT"] == 1
    _assert_stale_abstain(_dms_query(serve(lake), question))


def test_must_fail_without_the_check_the_stale_lake_serves_a_governed_zero(
    serve: Any, lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: the check is what stops the #235 finding, not some other gate."""
    _unmap(lake)
    monkeypatch.setattr(vd, "stale_columns", lambda *args, **kwargs: ())
    body = _dms_query(serve(lake), REVENUE_Q)
    assert body["badge"] == "governed_metric"
    assert body["rows"] == [{"revenue_myr": 0.0}]
    assert body["answer"] == "Result: revenue_myr = 0.0"


# ── must-fail: an undeclared spelling is never mapped, and never summed away ──


@pytest.mark.parametrize("spelling", ["Outbound", "out", "SALE"])
def test_must_fail_undeclared_spelling_is_named_never_mapped_or_summed_away(
    serve: Any, lake: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    _insert_sale(lake, "TXN-HAND-2", spelling)
    before = _stored(lake)
    assert before[spelling] == 1 and "OUT" not in before

    client = serve(lake)
    for question in (REVENUE_Q, TOP3_Q):
        _assert_stale_abstain(_dms_query(client, question), kinds=(NOT_DECLARED,))
    # Nothing on the answer path rewrote the row: it is stored exactly as written.
    assert _stored(lake) == before

    # Without the check the same ask is served green, silently leaving the row out:
    # the revenue is the oracle (which never saw the row), not oracle + 1000 * 1000.
    monkeypatch.setattr(vd, "stale_columns", lambda *args, **kwargs: ())
    served = _dms_query(client, REVENUE_Q)
    assert served["badge"] == "governed_metric"
    assert served["rows"][0]["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)


# ── /v1/insights ─────────────────────────────────────────────────────────────


def test_insights_mapped_lake_certifies_the_oracle_revenue(serve: Any, lake: Path) -> None:
    from CortexOS.crew import insights

    body = _insights(serve(lake), REVENUE_Q)
    assert body["status"] == "CERTIFIED", body["answer"]
    (row,) = body["values"]
    assert row["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)
    assert f"revenue_myr = {row['revenue_myr']}" in body["answer"]
    assert body["trials"][0]["id"] == "revenue_total"
    assert f"revenue_myr = {row['revenue_myr']}" in insights.render_tool_text(body)


@pytest.mark.parametrize(
    ("question", "trial"), [(REVENUE_Q, "revenue_total"), (TOP3_Q, "sales_by_value")]
)
def test_insights_stale_lake_abstains_by_name_and_tries_nothing_else(
    serve: Any, lake: Path, question: str, trial: str
) -> None:
    from CortexOS.crew import insights

    _unmap(lake)
    body = _insights(serve(lake), question)
    assert body["status"] == "ABSTAIN", body["answer"]
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert STALE in body["answer"]
    assert not _FIGURE.search(body["answer"]), body["answer"]
    # The ranked trial named the lake; no lower-ranked trial answered a different question.
    assert [(t["id"], t["badge"]) for t in body["trials"]] == [(trial, "abstain")]
    text = insights.render_tool_text(body)
    assert STALE in text
    assert str(_revenue_oracle()) not in text


# ── /v1/contract/ask: the envelope DMS receives ──────────────────────────────


def _contract_ask(client: TestClient, question: str) -> dict[str, Any]:
    res = client.post("/v1/contract/ask", json={"question": question, "session_id": SESSION})
    assert res.status_code == 200, res.text
    return res.json()


def test_contract_ask_stale_lake_is_an_abstain_envelope(serve: Any, lake: Path) -> None:
    client = serve(lake)
    served = _contract_ask(client, REVENUE_Q)
    assert served["provenance"]["badge"] == "governed_metric"
    assert served["rows"][0]["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)
    assert served["drillthrough_token"]

    _unmap(lake)
    body = _contract_ask(serve(lake), REVENUE_Q)
    assert body["provenance"]["badge"] == "abstain"
    assert body["provenance"]["layer"] == "abstain"
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["contributing_sources"] == []
    assert body["audit_id"]
    assert STALE in body["answer"]
    assert any(STALE in a for a in body["assumptions"])
    assert not _FIGURE.search(body["answer"]), body["answer"]


def test_space_memory_reuse_does_not_replay_onto_a_stale_lake(
    serve: Any, lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed solution re-runs its SQL on current data; a stale lake stops it."""
    from CortexOS.memory import space_memory as sm

    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    sm.reset_space_memory_for_tests()
    client = serve(lake)
    _bind(space_id="alpha")

    def ask() -> dict[str, Any]:
        res = client.post(
            "/v1/contract/ask",
            json={"question": REVENUE_Q, "session_id": SESSION, "space_id": "alpha"},
        )
        assert res.status_code == 200, res.text
        return res.json()

    try:
        first = ask()
        assert first["rows"][0]["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)
        sm.get_space_memory().confirm_solution(
            space_id="alpha",
            question=REVENUE_Q,
            sql=first["sql_used"],
            steward=sm.Actor("steward-alice", "steward"),
            source=f"ask:{first['answer_id']}",
        )
        reused = ask()
        assert reused["reused"] is True and reused["rows"] == first["rows"]

        _unmap(lake)
        body = ask()
        assert body["reused"] is False
        assert body["provenance"]["badge"] == "abstain"
        assert body["rows"] in ([], None)
        assert STALE in body["answer"]
        assert not _FIGURE.search(body["answer"]), body["answer"]
    finally:
        sm.reset_space_memory_for_tests()


# ── the check is kept per read-only instance, and sees a rebuild ─────────────


def test_check_scans_once_per_instance_and_sees_a_rebuild(
    serve: Any, lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scans: list[str] = []
    real = vd._drift_counts

    def counted(con: Any, d: vd.ValueDomain, schema: str) -> Any:
        scans.append(d.key)
        return real(con, d, schema)

    monkeypatch.setattr(vd, "_drift_counts", counted)
    _unmap(lake)
    client = serve(lake)
    for _ in range(3):
        _assert_stale_abstain(_dms_query(client, REVENUE_Q))
    assert scans == ["transactions.txn_type"]

    # Rebuild the same file in place through the declaration; nothing is cleared by hand.
    warehouse_db.load_inventory_csv(db_path=lake)
    body = _dms_query(serve(lake), REVENUE_Q)
    assert body["badge"] == "governed_metric", body["answer"]
    assert body["rows"][0]["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)
    assert scans == ["transactions.txn_type", "transactions.txn_type"]


# ── the verdict belongs to the database instance it scanned (review of #407) ──


def _evict(how: str, lake: Path) -> None:
    if how == "close_cached":
        warehouse.close_cached_connections()
    else:
        # A read-write open of the path evicts the cached read-only instance.
        warehouse.get_connection(lake).close()


@pytest.mark.parametrize("how", ["close_cached", "read_write_open"])
def test_a_lake_renamed_over_the_path_is_checked_on_the_instance_that_reads_it(
    serve: Any, lake: Path, tmp_path: Path, how: str
) -> None:
    """Swap a stale lake in by rename, then evict: the stale lake is never served.

    The cached read-only instance keeps reading the file it opened after another
    file is renamed over its path, so a check it runs is about that file. Filed
    under the path's new stamp, that verdict ("clean") outlived the instance and
    the next one served the stale file unchecked: ``revenue_myr = 0.0``, badge
    ``governed_metric``.
    """
    stale = tmp_path / "stale.duckdb"
    shutil.copy(lake, stale)
    _unmap(stale)
    client = serve(lake)
    first = _dms_query(client, REVENUE_Q)
    assert first["badge"] == "governed_metric", first["answer"]
    assert first["rows"][0]["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)

    os.replace(stale, lake)
    # Same instance, same file it opened: it still answers from the mapped lake.
    again = _dms_query(client, REVENUE_Q)
    assert again["badge"] == "governed_metric", again["answer"]
    assert again["rows"] == first["rows"]
    assert again["answer"] == first["answer"]

    _evict(how, lake)
    for question in (REVENUE_Q, TOP3_Q):
        _assert_stale_abstain(_dms_query(client, question))
    assert set(_stored(lake)) == {"OUT", "IN", "ADJUST", "WRITE_OFF"}


def test_connection_instance_names_the_read_only_instance_a_cursor_reads(lake: Path) -> None:
    warehouse.close_cached_connections()
    a = warehouse.get_connection(lake, read_only=True)
    b = warehouse.get_connection(lake, read_only=True)
    try:
        first = warehouse.connection_instance(a)
        assert first is not None
        assert warehouse.connection_instance(b) == first  # two cursors, one instance
    finally:
        a.close()
        b.close()
    warehouse.close_cached_connections()
    c = warehouse.get_connection(lake, read_only=True)
    try:
        second = warehouse.connection_instance(c)
        assert second is not None and second != first  # a new instance is never the old one
    finally:
        c.close()
    # A connection the warehouse cannot pin to one unchanging instance has no name.
    rw = warehouse.get_connection(lake)
    mem = duckdb.connect(":memory:")
    try:
        assert warehouse.connection_instance(rw) is None
        assert warehouse.connection_instance(mem) is None
    finally:
        rw.close()
        mem.close()
        warehouse.close_cached_connections()


def test_lake_drift_caches_per_named_instance_and_rescans_an_unnamed_one(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scans: list[str] = []
    real = vd._drift_counts

    def counted(con: Any, d: vd.ValueDomain, schema: str) -> Any:
        scans.append(d.key)
        return real(con, d, schema)

    monkeypatch.setattr(vd, "_drift_counts", counted)
    vd.clear_lake_cache()
    (dom,) = [d for d in _domains() if d.key == "transactions.txn_type"]
    con = duckdb.connect(str(lake), read_only=True)
    try:
        for _ in range(2):
            assert vd.lake_drift(con, (dom,)) == ()  # no instance named: scanned each time
        assert len(scans) == 2
        for _ in range(2):
            assert vd.lake_drift(con, (dom,), instance=("lake", 1)) == ()
        assert len(scans) == 3
        assert vd.lake_drift(con, (dom,), instance=("lake", 2)) == ()
        assert len(scans) == 4  # another instance is scanned on its own
    finally:
        con.close()
        vd.clear_lake_cache()


# ── the plan+SQL ladder (CORTEX_PLAN_SQL=1) stops on a stale lake ────────────

PLAN_SQL_PAYLOAD: dict[str, Any] = {
    "tables": [
        {
            "name": "transactions",
            "columns": [
                {"name": "txn_type", "type": "VARCHAR"},
                {"name": "sku", "type": "VARCHAR"},
                {"name": "quantity_kg", "type": "DOUBLE"},
            ],
        }
    ]
}
SALES_KG_Q = "how many kg did we sell"
SALES_KG_SQL = "SELECT SUM(quantity_kg) AS kg FROM transactions WHERE txn_type = 'outbound'"
# What a model writes once told the filtered SQL failed: every movement, not sales.
ALL_KG_SQL = "SELECT SUM(quantity_kg) AS kg FROM transactions"


def _kg_oracle(*, sales_only: bool) -> float:
    path = warehouse_db.SAMPLES / "transactions_clean.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return round(
        sum(float(r["quantity_kg"]) for r in rows if not sales_only or r["txn_type"] == "OUT"),
        2,
    )


def _model_reply(fake: Any, content: str) -> None:
    fake.replies.append(
        (
            200,
            {
                "model": "openai/gpt-oss-120b",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "served_provider": "groq",
                "served_model": "openai/gpt-oss-120b",
                "served_local": False,
            },
        )
    )


@pytest.mark.parametrize("mapped", [True, False])
def test_plan_sql_ladder_stops_by_name_on_a_stale_lake_and_never_retries(
    serve: Any,
    lake: Path,
    armed_openvault: Any,
    monkeypatch: pytest.MonkeyPatch,
    mapped: bool,
) -> None:
    """Must-fail for the plan+SQL ladder: the stale reason is not fed back as retryable.

    Before, ``LakeValueDomainStale`` was caught as a generic ``SqlGateAbstain``
    and handed to the model as a gate error. The only SQL it could then serve
    for a sales question drops ``txn_type``: served badge ``session``, rung
    ``error-fed-retry-1``, ``kg`` = every movement instead of the outbound total.
    """
    from CortexOS.dms import plan_sql_ask
    from CortexOS.execution import submit

    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    real = submit.execute_sql
    ran: list[str] = []

    def spy(verified: Any, sql: str, **kwargs: Any) -> Any:
        if not sql.lstrip().upper().startswith("SELECT DISTINCT"):  # not a sample probe
            ran.append(sql)
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", spy)
    if not mapped:
        _unmap(lake)
    _model_reply(armed_openvault, "1. Read transactions.\n2. Sum quantity_kg of sales.")
    _model_reply(armed_openvault, f"```sql\n{SALES_KG_SQL};\n```")
    _model_reply(armed_openvault, f"```sql\n{ALL_KG_SQL};\n```")
    client = serve(lake)
    res = client.post(
        "/v1/contract/ask",
        json={"question": SALES_KG_Q, "session_id": SESSION, "dms_payload": PLAN_SQL_PAYLOAD},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    if mapped:
        assert body["provenance"]["badge"] == "session", body["answer"]
        assert body["plan_sql_rung"] == plan_sql_ask.RUNG_PLAN
        (row,) = body["rows"]
        assert row["kg"] == pytest.approx(_kg_oracle(sales_only=True), abs=0.011)
        assert f"kg = {row['kg']}" in body["answer"]
        assert ran == [body["sql_used"]] and "txn_type = 'outbound'" in body["sql_used"]
        return

    assert body["provenance"]["badge"] == "abstain", body["answer"]
    assert body["provenance"]["layer"] == "abstain"
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["plan_sql_rung"] is None
    assert STALE in body["answer"] and NOT_MAPPED in body["answer"]
    assert any(STALE in line for line in body["assumptions"])
    assert not _FIGURE.search(body["answer"]), body["answer"]
    assert not re.search(r"\b(OUT|IN)\b", body["answer"]), body["answer"]
    for kg in (_kg_oracle(sales_only=True), _kg_oracle(sales_only=False)):
        assert str(kg) not in json.dumps(body)
    # One model SQL (as gated: a row bound added), refused on the lake; the
    # filter-dropping rewrite was never asked for, so it never ran.
    assert len(ran) == 1 and ran[0].startswith(SALES_KG_SQL), ran
    assert len(armed_openvault.chat_calls) == 2  # the plan and that one SQL
    assert len(armed_openvault.replies) == 1


# ── the two query seams every served answer runs through ─────────────────────

REVENUE_SQL = (
    "SELECT ROUND(COALESCE(SUM(quantity_kg * unit_cost_myr), 0), 2) AS revenue_myr "
    "FROM transactions WHERE txn_type = 'outbound'"
)


def test_execute_sql_refuses_a_stale_lake_before_fetching(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from CortexOS.dms.sql_validate_gate import LakeValueDomainStale, SqlGateAbstain
    from CortexOS.execution import submit

    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    vd.clear_lake_cache()
    _bind()
    verified = get_session_registry().resolve(SESSION)
    try:
        (row,) = submit.execute_sql(verified, REVENUE_SQL)[0]
        assert row["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)

        _unmap(lake)
        with pytest.raises(LakeValueDomainStale) as caught:
            submit.execute_sql(verified, REVENUE_SQL)
        assert isinstance(caught.value, SqlGateAbstain)  # callers that only know the gate abstain
        assert caught.value.violations == [STALE]
        assert str(caught.value).startswith(f"{STALE}: ") and NOT_MAPPED in str(caught.value)
        # Reading the table checks its declared columns, even with no column token.
        with pytest.raises(LakeValueDomainStale):
            submit.execute_sql(verified, "SELECT COUNT(*) AS n FROM transactions")
        # A table with no declared domain is not held to one.
        rows, _, _ = submit.execute_sql(verified, "SELECT COUNT(*) AS n FROM inventory")
        assert rows[0]["n"] > 0
        # Opting out is explicit, and shows what the check prevents: a silent zero.
        (zero,) = submit.execute_sql(verified, REVENUE_SQL, value_domains=())[0]
        assert zero == {"revenue_myr": 0.0}
    finally:
        warehouse.close_cached_connections()
        reset_session_registry_for_tests()


def test_contract_submit_on_a_stale_lake_is_a_named_sql_gate_abstain(
    verifier: Any,  # noqa: F811
    issuer: Any,  # noqa: F811
    lake: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw SQL DMS submits runs through the same seam: an existing status, the same reason."""
    from cortex_contract.execution import PoolSpec, SubmitRequest

    from CortexOS.execution import submit

    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    vd.clear_lake_cache()

    def request() -> SubmitRequest:
        return SubmitRequest(
            pool=PoolSpec(id="pool-a"),
            plan={"kind": "sql"},
            body={"sql": REVENUE_SQL},
            manifest=_signed(issuer, predicates={"transactions": "1=1"}),
        )

    try:
        ok = submit.submit_request(request())
        assert ok.ok is True, ok.error
        assert ok.output["rows"][0]["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)

        _unmap(lake)
        refused = submit.submit_request(request())
        assert refused.ok is False
        assert refused.status == submit.SQL_GATE_ABSTAIN
        assert refused.output is None
        assert (refused.error or "").startswith(f"{STALE}: "), refused.error
        assert not _FIGURE.search(refused.error or "")
    finally:
        warehouse.close_cached_connections()


def test_legacy_engine_branch_abstains_through_guard_and_execute(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-process callers without a grant (the watcher agent) read through guard_and_execute."""
    from CortexOS.dms import answer_engine
    from CortexOS.dms.sql_guardrail import guard_and_execute

    monkeypatch.setattr(answer_engine, "DEFAULT_DB", lake)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(lake.parent / "ops.db"))
    vd.clear_lake_cache()
    try:
        served = answer_engine.answer(REVENUE_Q, session_id="stale-legacy")
        assert served["badge"] == "governed_metric", served["answer"]
        (row,) = served["rows"]
        assert row["revenue_myr"] == pytest.approx(_revenue_oracle(), abs=0.011)
        assert served["answer"] == f"Result: revenue_myr = {row['revenue_myr']}"

        _unmap(lake)
        answer_engine.clear_session("stale-legacy")
        body = answer_engine.answer(REVENUE_Q, session_id="stale-legacy")
        assert body["badge"] == "abstain" and body["layer"] == "abstain"
        assert body["rows"] == [] and body["sql_used"] is None
        assert STALE in body["answer"]
        # This branch appends suggested questions; the served figure is not among them.
        assert "revenue_myr" not in body["answer"] and "0.0" not in body["answer"]

        warehouse.close_cached_connections()
        con = duckdb.connect(str(lake), read_only=True)
        try:
            result, rows, entry = guard_and_execute(
                REVENUE_SQL, warehouse_db.load_semantic_layer(), con
            )
        finally:
            con.close()
        assert result.passed is False and rows == []
        assert result.violations[0].startswith(f"{STALE}: ")
        assert entry.violations == [STALE] and entry.passed is False
    finally:
        warehouse.close_cached_connections()


# ── legacy and L2 paths read the same declaration ────────────────────────────


def test_legacy_rule_bridge_abstains_on_a_stale_lake(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-Q2 rule SQL (``WHERE txn_type = 'outbound'``) is held to the same check."""
    from CortexOS.dms import query_service

    monkeypatch.setattr(query_service, "DEFAULT_DB", lake)
    vd.clear_lake_cache()
    question = "top 3 sales revenue by sku"
    served = query_service._answer_question_legacy(question)
    want = _top3_oracle()
    assert [r["sku"] for r in served["rows"]] == [sku for sku, _ in want]
    assert [r["sales_value_myr"] for r in served["rows"]] == pytest.approx(
        [v for _, v in want], abs=0.011
    )

    _unmap(lake)
    body = query_service._answer_question_legacy(question)
    _assert_stale_abstain(body)
    warehouse.close_cached_connections()


def _phase1b_lake(path: Path, *, mapped: bool) -> Path:
    con = duckdb.connect(str(path))
    try:
        for statement in PHASE1B["setup_sql"]:
            con.execute(statement)
        if mapped:
            vd.apply_to_lake(con, _domains())
    finally:
        con.close()
    return path


class _MissBridge:
    session_id = SESSION
    space_id = None

    async def ask(self, question: str) -> dict[str, Any]:
        del question
        return {
            "ok": True,
            "answer": "Engine abstained.",
            "badge": "abstain",
            "layer": "l1",
            "audit_id": None,
            "row_count": 0,
            "rows": [],
            "sql_used": None,
        }


def _complete(sql: str) -> Any:
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        return {
            "ok": True,
            "text": sql,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "stub", "model": "stub"},
            "stamp": {
                "call_id": f"call-{purpose}",
                "served_provider": "p",
                "served_model": "m",
                "served_local": True,
                "served_reason": "",
            },
        }

    return fake


@pytest.mark.parametrize("mapped", [True, False])
def test_crew_l2_serve_checks_the_lake_before_serving_model_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mapped: bool
) -> None:
    from CortexOS.crew import freeroute, insights
    from CortexOS.execution import submit

    case = PHASE1B_CASES["cq_sales_top5_value"]
    # The model writes the declared canonical value, as the prompt tells it to.
    sql = vd.canonicalize_sql(case["sql"], _domains())
    assert "txn_type = 'outbound'" in sql
    lake = _phase1b_lake(tmp_path / "phase1b.duckdb", mapped=mapped)
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(
        freeroute,
        "arming",
        lambda: {"ok": True, "armed": True, "detail": "test", "live_5000_ci": False},
    )
    vd.clear_lake_cache()
    _bind()
    try:
        body = asyncio.run(
            insights.run_insights(
                case["question"],
                bridge=_MissBridge(),
                ask=True,
                generate=True,
                complete=_complete(sql),
            )
        )
    finally:
        warehouse.close_cached_connections()
        reset_session_registry_for_tests()
    if mapped:
        assert body["status"] == "CERTIFIED", body["answer"]
        assert body["badge"] == "L2_VALIDATED"
        assert body["values"] == case["oracle"]
        for row in case["oracle"]:
            assert f"sales_value_myr={row['sales_value_myr']}" in body["answer"]
    else:
        assert body["status"] == "ABSTAIN"
        assert body["answer_step"] == "value_domain"
        assert body["values"] == [] and body["rows"] == []
        assert body["badge"] == "abstain" and body["layer"] == "abstain"
        assert STALE in body["answer"]
        assert "sales_value_myr=" not in body["answer"]


# ── units: which SQL is checked, and what the scan counts ────────────────────


@pytest.mark.parametrize(
    ("sql", "hit"),
    [
        ("SELECT SUM(quantity_kg) FROM transactions WHERE txn_type = 'outbound'", True),
        ("SELECT txn_type, COUNT(*) FROM transactions GROUP BY 1", True),
        ("SELECT SUM(CASE txn_type WHEN 'outbound' THEN 1 END) FROM transactions", True),
        ("SELECT SUM(t.quantity_kg) FROM transactions AS t WHERE t.TXN_TYPE IN ('inbound')", True),
        (
            "WITH s AS (SELECT * FROM transactions) SELECT s.txn_type, COUNT(*) FROM s GROUP BY 1",
            True,
        ),
        ("SELECT * FROM transactions WHERE sku = 'SKU-1'", True),
        ("SELECT SUM(quantity_kg) FROM transactions", True),
        ("SELECT COUNT(*) AS n FROM inventory", False),
        ("SELECT category, COUNT(*) FROM inventory GROUP BY 1", False),
        ("SELECT txn_type FROM other_table", False),
        (
            "WITH transactions AS (SELECT 1 AS n) SELECT n FROM transactions",
            False,
        ),
        ("SELECT txn_type FROM transactions WHERE (", True),  # does not parse: fail closed
        (
            "SELECT SUM(quantity_kg * unit_cost_myr) AS revenue_myr FROM transactions "
            "JOIN (SELECT 'outbound' AS txn_type) f USING (txn_type)",
            True,
        ),
        (
            "SELECT SUM(quantity_kg) FROM transactions "
            "NATURAL JOIN (SELECT 'outbound' AS txn_type) f",
            True,
        ),
        (
            "SELECT SUM(quantity_kg) FROM transactions WHERE COLUMNS('txn_type') = 'outbound'",
            True,
        ),
        (
            "SELECT SUM(t.quantity_kg) FROM transactions t WHERE t['txn_type'] = 'outbound'",
            True,
        ),
        (
            "SELECT SUM(q * c) FROM transactions AS t(i, s, l, k, q, c, o, ts, r) WHERE k = 'outbound'",
            True,
        ),
    ],
)
def test_domains_in_sql_checks_every_declared_column_of_every_table_read(
    sql: str, hit: bool
) -> None:
    got = vd.domains_in_sql(sql, _domains())
    assert [d.key for d in got] == (["transactions.txn_type"] if hit else [])


def test_lake_drift_counts_rows_never_values() -> None:
    con = duckdb.connect(":memory:")
    try:
        con.execute("CREATE TABLE transactions (txn_id VARCHAR, txn_type VARCHAR)")
        con.executemany(
            "INSERT INTO transactions VALUES (?, ?)",
            [
                ("1", "outbound"),
                ("2", "OUT"),
                ("3", "OUT"),
                ("4", "IN"),
                ("5", "sale"),
                ("6", None),
                ("7", "WRITE_OFF"),
            ],
        )
        (drift,) = vd.lake_drift(con, _domains())
        assert drift == vd.LakeDrift("transactions.txn_type", unmapped_rows=3, undeclared_rows=1)
        assert drift.code == STALE
        assert "sale" not in drift.reason() and "OUT" not in drift.reason()
        con.execute("DELETE FROM transactions WHERE txn_id IN ('2', '3', '4', '5')")
        assert vd.lake_drift(con, _domains()) == ()
        sql = "SELECT COUNT(*) FROM transactions WHERE txn_type = 'outbound'"
        assert vd.stale_columns(con, sql, _domains()) == ()
    finally:
        con.close()
    empty = duckdb.connect(":memory:")
    try:
        assert vd.lake_drift(empty, _domains()) == ()  # no table: nothing to misread
    finally:
        empty.close()


# ── #410: five forms that filter txn_type without an exp.Column node ─────────
#
# Each form on a stale lake is a named abstain. With stale_columns forced to
# return nothing, the same HTTP call serves revenue_myr = 0.0. That second
# half is the must-fail: it is what goes red in the abstain assertion when the
# guard is removed, and what this test records as the served zero.


def _revenue_form(body: str) -> str:
    return (
        "SELECT ROUND(COALESCE(SUM(" + body + "), 0), 2) AS revenue_myr "
    )


JOIN_FORMS: dict[str, str] = {
    "using": (
        _revenue_form("quantity_kg * unit_cost_myr")
        + "FROM transactions JOIN (SELECT 'outbound' AS txn_type) f USING (txn_type)"
    ),
    "natural": (
        _revenue_form("quantity_kg * unit_cost_myr")
        + "FROM transactions NATURAL JOIN (SELECT 'outbound' AS txn_type) f"
    ),
    "columns": (
        _revenue_form("quantity_kg * unit_cost_myr")
        + "FROM transactions WHERE COLUMNS('txn_type') = 'outbound'"
    ),
    "subscript": (
        _revenue_form("t.quantity_kg * t.unit_cost_myr")
        + "FROM transactions t WHERE t['txn_type'] = 'outbound'"
    ),
    "alias_list": (
        _revenue_form("q * c")
        + "FROM transactions AS t(i, s, l, k, q, c, o, ts, r) WHERE k = 'outbound'"
    ),
}


def _assert_no_served_zero(body: dict[str, Any]) -> None:
    text = json.dumps(body)
    assert "0.0" not in text, text
    assert not re.search(r"\b(OUT|IN)\b", text), text


def _submit_form(client: TestClient, signing_key: Any, sql: str) -> Any:
    from cortex_contract.execution import PoolSpec, SubmitRequest

    req = SubmitRequest(
        pool=PoolSpec(id="pool-a"),
        plan={"kind": "sql"},
        body={"sql": sql},
        manifest=_signed(signing_key, predicates={"transactions": "1=1"}),
    )
    return client.post("/v1/contract/submit", json=req.model_dump(mode="json"))


@pytest.mark.parametrize("form", sorted(JOIN_FORMS))
def test_http_submit_join_form_abstains_and_must_fail_serves_zero(
    serve: Any,
    lake: Path,
    verifier: Any,  # noqa: F811
    issuer: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    form: str,
) -> None:
    """Client SQL on POST /v1/contract/submit. All five forms reach the seam."""
    _unmap(lake)
    client = serve(lake)
    sql = JOIN_FORMS[form]
    refused = _submit_form(client, issuer, sql)
    assert refused.status_code == 403, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "sql_gate_abstain"
    assert STALE in detail["message"] and NOT_MAPPED in detail["message"]
    _assert_no_served_zero(refused.json())

    monkeypatch.setattr(vd, "stale_columns", lambda *args, **kwargs: ())
    served = _submit_form(client, issuer, sql)
    assert served.status_code == 200, served.text
    body = served.json()
    assert body["ok"] is True
    assert body["output"]["rows"] == [{"revenue_myr": 0.0}]


def _queue_plan_sql(fake: Any, sql: str) -> None:
    fenced = f"```sql\n{sql};\n```"
    for _ in range(8):
        _model_reply(fake, fenced)


# subscript and alias_list are refused by the column guardrail (UNKNOWN_COLUMN)
# before execute, so they never become a served zero on this path. The three
# forms that pass that guardrail are the ones the lake check has to stop.
_PLAN_SQL_REACHES = ("columns", "natural", "using")
_PLAN_SQL_COLUMN_GATE = ("alias_list", "subscript")


@pytest.mark.parametrize("form", _PLAN_SQL_REACHES)
def test_http_plan_sql_join_form_abstains_and_must_fail_serves_zero(
    serve: Any,
    lake: Path,
    armed_openvault: Any,
    monkeypatch: pytest.MonkeyPatch,
    form: str,
) -> None:
    """POST /v1/contract/ask with CORTEX_PLAN_SQL=1. The model writes the form."""
    from CortexOS.dms import plan_sql_ask

    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    monkeypatch.delenv("CORTEX_PLAN_SQL_VOTE", raising=False)
    _unmap(lake)
    sql = JOIN_FORMS[form]
    _queue_plan_sql(armed_openvault, sql)
    client = serve(lake)

    def ask() -> dict[str, Any]:
        res = client.post(
            "/v1/contract/ask",
            json={
                "question": REVENUE_Q,
                "session_id": SESSION,
                "dms_payload": PLAN_SQL_PAYLOAD,
            },
        )
        assert res.status_code == 200, res.text
        return res.json()

    body = ask()
    assert body["provenance"]["badge"] == "abstain", body["answer"]
    assert body["provenance"]["layer"] == "abstain"
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert STALE in body["answer"] and NOT_MAPPED in body["answer"]
    _assert_no_served_zero(body)

    monkeypatch.setattr(vd, "stale_columns", lambda *args, **kwargs: ())
    _queue_plan_sql(armed_openvault, sql)
    served = ask()
    assert served["provenance"]["badge"] == "session", served["answer"]
    assert served["rows"] == [{"revenue_myr": 0.0}]
    assert "0.0" in served["answer"]


@pytest.mark.parametrize("form", _PLAN_SQL_COLUMN_GATE)
def test_http_plan_sql_column_gate_does_not_serve_the_join_form_zero(
    serve: Any,
    lake: Path,
    armed_openvault: Any,
    monkeypatch: pytest.MonkeyPatch,
    form: str,
) -> None:
    """These two forms never reach execute on plan+SQL: unknown-column refusal."""
    from CortexOS.dms import plan_sql_ask

    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    monkeypatch.delenv("CORTEX_PLAN_SQL_VOTE", raising=False)
    _unmap(lake)
    sql = JOIN_FORMS[form]
    _queue_plan_sql(armed_openvault, sql)
    client = serve(lake)

    def ask() -> dict[str, Any]:
        res = client.post(
            "/v1/contract/ask",
            json={
                "question": REVENUE_Q,
                "session_id": SESSION,
                "dms_payload": PLAN_SQL_PAYLOAD,
            },
        )
        assert res.status_code == 200, res.text
        return res.json()

    body = ask()
    assert body["provenance"]["badge"] == "reconfirm", body["answer"]
    assert body["rows"] in ([], None)
    _assert_no_served_zero(body)
    # The column refusal is on the gate step, not always in the answer sentence.
    assert "UNKNOWN_COLUMN" in json.dumps(body["assumptions"])

    monkeypatch.setattr(vd, "stale_columns", lambda *args, **kwargs: ())
    _queue_plan_sql(armed_openvault, sql)
    served = ask()
    assert served["provenance"]["badge"] == "reconfirm", served["answer"]
    assert served["rows"] in ([], None)
    _assert_no_served_zero(served)
    assert served["provenance"]["badge"] != "session"
    assert "UNKNOWN_COLUMN" in json.dumps(served["assumptions"])


def _climb_accepting(sql: str) -> Any:
    async def climb(intent: str, ranking: Any, **kwargs: Any) -> dict[str, Any]:
        del intent, ranking, kwargs
        return {
            "ok": True,
            "status": "ABSTAIN",
            "sql": sql,
            "valid": True,
            "stamp": {
                "call_id": "call-join-form",
                "served_provider": "p",
                "served_model": "m",
                "served_local": True,
                "served_reason": "",
            },
            "check": "test",
            "tables": ["transactions"],
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "stub", "model": "stub"},
            "refuse_reason": "",
            "note": "",
            "climb": {},
        }

    return climb


@pytest.mark.parametrize("form", sorted(JOIN_FORMS))
def test_http_l2_join_form_abstains_and_must_fail_serves_zero(
    serve: Any,
    lake: Path,
    monkeypatch: pytest.MonkeyPatch,
    form: str,
) -> None:
    """POST /v1/insights with DMS_L2_ENABLED. Generation is already accepted.

    The static measure check and the column guardrail refuse some of these
    forms before serve. This test starts where that acceptance ends: l2_serve
    executes the form. The lake check is what stops the zero.
    """
    from CortexOS.crew import cot_climb
    from CortexOS.crew.engine_bridge import LocalEngineBridge

    async def miss(self: Any, question: str) -> dict[str, Any]:
        del self, question
        return {
            "ok": True,
            "answer": "Engine abstained.",
            "badge": "abstain",
            "layer": "l1",
            "audit_id": None,
            "row_count": 0,
            "rows": [],
            "sql_used": None,
            "assumptions": "",
        }

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(cot_climb, "climb", _climb_accepting(JOIN_FORMS[form]))
    monkeypatch.setattr(LocalEngineBridge, "ask", miss)
    _unmap(lake)
    client = serve(lake)

    def post() -> dict[str, Any]:
        res = client.post(
            "/v1/insights",
            json={
                "intent": REVENUE_Q,
                "ask": True,
                "generate": True,
                "session_id": SESSION,
            },
        )
        assert res.status_code == 200, res.text
        return res.json()

    body = post()
    assert body["status"] == "ABSTAIN", body.get("answer")
    assert body["answer_step"] == "value_domain"
    assert body["badge"] == "abstain" and body["layer"] == "abstain"
    assert body["values"] == [] and body["rows"] == []
    assert STALE in body["answer"] and NOT_MAPPED in body["answer"]
    _assert_no_served_zero(body)

    monkeypatch.setattr(vd, "stale_columns", lambda *args, **kwargs: ())
    served = post()
    assert served["status"] == "CERTIFIED", served.get("answer")
    assert served["badge"] == "L2_VALIDATED"
    assert served["values"] == [{"revenue_myr": 0.0}]
    assert "revenue_myr=0.0" in served["answer"]


def test_statement_timeout_is_set_before_the_drift_scan(
    lake: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EXPLAIN and the value-domain scan run under the statement timeout."""
    from CortexOS.execution import submit, warehouse

    calls: list[str] = []
    real_get = warehouse.get_connection

    class _Spy:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def execute(self, sql: str, *args: Any, **kwargs: Any) -> Any:
            calls.append(str(sql))
            return self._inner.execute(sql, *args, **kwargs)

        def close(self) -> None:
            self._inner.close()

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    def wrapped(path: Any, **kwargs: Any) -> Any:
        return _Spy(real_get(path, **kwargs))

    monkeypatch.setattr(warehouse, "get_connection", wrapped)
    monkeypatch.setattr(submit, "get_connection", wrapped)
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    vd.clear_lake_cache()
    _bind()
    verified = get_session_registry().resolve(SESSION)
    try:
        submit.execute_sql(verified, REVENUE_SQL)
    finally:
        warehouse.close_cached_connections()
        reset_session_registry_for_tests()
    timeout_at = next(i for i, sql in enumerate(calls) if sql.startswith("SET statement_timeout"))
    scan_at = next(i for i, sql in enumerate(calls) if "FILTER (WHERE" in sql)
    assert timeout_at < scan_at, calls
