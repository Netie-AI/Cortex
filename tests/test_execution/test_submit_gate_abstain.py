"""dms#231 F2: an EXPLAIN-refused SQL on /v1/contract/submit is a named refusal.

The generate path hands DMS SQL that DMS submits here. When the lake lacks a
column the model cited (last_audit_date), the EXPLAIN gate refuses correctly --
but SqlGateAbstain escaped submit_request and surfaced as HTTP 500. It must be
a QueryResult refusal with a named status, never a 5xx.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cortex_contract.execution import PoolSpec, SubmitRequest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from CortexOS.execution import submit as submit_mod
from tests.test_execution.test_submit_c4 import _signed, issuer, verifier  # noqa: F401

MISSING_COL_SQL = (
    "SELECT supplier_id FROM suppliers "
    "WHERE CAST(last_audit_date AS DATE) < CURRENT_DATE - INTERVAL 90 DAY"
)


@pytest.fixture()
def lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import duckdb

    db = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE suppliers (supplier_id VARCHAR, risk_score DOUBLE)")
    con.execute("INSERT INTO suppliers VALUES ('S1', 0.5)")
    con.close()
    monkeypatch.setattr(submit_mod, "DEFAULT_DB", db)
    return db


def _request(issuer: Ed25519PrivateKey, sql: str) -> SubmitRequest:  # noqa: F811
    return SubmitRequest(
        pool=PoolSpec(id="pool-a"),
        plan={"kind": "sql"},
        body={"sql": sql},
        manifest=_signed(issuer, predicates={"suppliers": "1=1"}),
    )


def test_explain_refusal_is_a_named_result_not_an_exception(
    verifier, issuer, lake  # noqa: F811
) -> None:
    result = submit_mod.submit_request(_request(issuer, MISSING_COL_SQL))
    assert result.ok is False
    assert result.status == submit_mod.SQL_GATE_ABSTAIN
    assert "last_audit_date" in (result.error or "")
    assert result.output is None

    ok = submit_mod.submit_request(_request(issuer, "SELECT supplier_id FROM suppliers"))
    assert ok.ok is True
    assert ok.output["rows"] == [{"supplier_id": "S1"}]


def test_explain_refusal_over_http_is_not_a_5xx(
    verifier, issuer, lake, monkeypatch, tmp_path  # noqa: F811
) -> None:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app

    client = TestClient(create_app(), raise_server_exceptions=False, headers={"X-API-Key": "dms-demo-admin-key"})
    req = _request(issuer, MISSING_COL_SQL)
    res = client.post("/v1/contract/submit", json=req.model_dump(mode="json"))
    assert res.status_code < 500, res.text
    assert res.status_code == 403
    detail = res.json()["detail"]
    assert detail["code"] == "sql_gate_abstain"
    assert "last_audit_date" in detail["message"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CAST(code AS INTEGER) AS n FROM suppliers",
        "SELECT CAST(risk_score * 1e40 AS INTEGER) AS n FROM suppliers",
    ],
)
def test_runtime_engine_error_over_http_is_not_a_5xx(
    verifier, issuer, lake, monkeypatch, tmp_path, sql  # noqa: F811
) -> None:
    """SQL that passes EXPLAIN but fails at run time (bad cast, overflow)."""
    import duckdb

    con = duckdb.connect(str(lake))
    con.execute("ALTER TABLE suppliers ADD COLUMN code VARCHAR")
    con.execute("UPDATE suppliers SET code = 'abc'")
    con.close()
    result = submit_mod.submit_request(_request(issuer, sql))
    assert result.ok is False
    assert result.status == submit_mod.SQL_RUNTIME_ERROR
    assert result.output is None

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app

    client = TestClient(create_app(), raise_server_exceptions=False, headers={"X-API-Key": "dms-demo-admin-key"})
    res = client.post("/v1/contract/submit", json=_request(issuer, sql).model_dump(mode="json"))
    assert res.status_code == 403, res.text
    assert res.json()["detail"]["code"] == "sql_runtime_error"


def _bad_cast_lake(lake: Path) -> None:
    """A column whose cell fails a cast only when the query runs, not at EXPLAIN."""
    import duckdb

    con = duckdb.connect(str(lake))
    con.execute("ALTER TABLE suppliers ADD COLUMN code VARCHAR")
    con.execute("UPDATE suppliers SET code = 'abc'")
    con.close()


CANARY = "SECRET-LOC-9"
BAD_CAST_SQL = "SELECT * FROM suppliers WHERE CAST(code AS INT) > 0"


def _assert_redacted(text: str) -> None:
    # No predicate literal (or a fragment of it), no caret context, no cell value.
    assert CANARY not in text and "OC-9" not in text, text
    assert "LINE 1:" not in text and "'abc'" not in text, text


def test_must_fail_runtime_engine_error_carries_no_raw_engine_text(
    verifier, issuer, lake, monkeypatch, tmp_path  # noqa: F811
) -> None:
    """DB-ERROR-TEXT-CX-01: a run-time engine error is redacted on the wire.

    DuckDB's run-time error text can carry ``LINE 1:`` context cut from the
    post-enforce SQL, which quotes the grant's row-predicate literal. The
    caller gets a stable code and redacted message; the raw text stays in the
    run record.
    """
    from CortexOS.execution.telemetry import recent_runs

    _bad_cast_lake(lake)
    req = SubmitRequest(
        pool=PoolSpec(id="pool-a"),
        plan={"kind": "sql"},
        body={"sql": BAD_CAST_SQL},
        manifest=_signed(issuer, predicates={"suppliers": f"supplier_id <> '{CANARY}'"}),
    )

    result = submit_mod.submit_request(req)
    assert result.ok is False
    assert result.status == submit_mod.SQL_RUNTIME_ERROR
    assert (result.error or "").startswith("CONVERSION_ERROR: ")
    _assert_redacted(result.error or "")
    run = next(r for r in recent_runs() if r["run_id"] == result.run_id)
    assert CANARY in (run["error"] or "")

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app

    client = TestClient(create_app(), raise_server_exceptions=False, headers={"X-API-Key": "dms-demo-admin-key"})
    res = client.post("/v1/contract/submit", json=req.model_dump(mode="json"))
    assert res.status_code == 403, res.text
    detail = res.json()["detail"]
    assert detail["code"] == "sql_runtime_error"
    assert detail["message"].startswith("CONVERSION_ERROR: ")
    _assert_redacted(res.text)


def test_gate_abstain_keeps_raw_engine_text_in_the_run_record(
    verifier, issuer, lake  # noqa: F811
) -> None:
    """The EXPLAIN refusal shows redacted text; the run record keeps the raw text."""
    from CortexOS.execution.telemetry import recent_runs

    req = SubmitRequest(
        pool=PoolSpec(id="pool-a"),
        plan={"kind": "sql"},
        body={"sql": "SELECT * FROM suppliers WHERE supplier_id > 5"},
        manifest=_signed(issuer, predicates={"suppliers": f"supplier_id <> '{CANARY}'"}),
    )
    result = submit_mod.submit_request(req)
    assert result.status == submit_mod.SQL_GATE_ABSTAIN
    _assert_redacted(result.error or "")
    run = next(r for r in recent_runs() if r["run_id"] == result.run_id)
    assert CANARY in (run["error"] or "")


def test_must_fail_execute_sql_runtime_error_is_redacted_at_the_seam(
    verifier, issuer, lake  # noqa: F811
) -> None:
    """Every execute_sql caller (L2 serve, answer engine, submit) reads one seam.

    The exception keeps its class so callers that branch on it are unchanged.
    """
    import duckdb

    _bad_cast_lake(lake)
    verified = submit_mod.verify_and_check_pool(
        _signed(issuer, predicates={"suppliers": f"supplier_id <> '{CANARY}'"}), "pool-a"
    )
    with pytest.raises(duckdb.ConversionException) as caught:
        submit_mod.execute_sql(verified, BAD_CAST_SQL, explain_gate=True)
    shown = f"{type(caught.value).__name__}: {caught.value}"
    assert shown.startswith("ConversionException: CONVERSION_ERROR: ")
    _assert_redacted(shown)
    assert CANARY in getattr(caught.value, "raw_detail", "")

    # Positive control: an ordinary granted query still returns its rows.
    rows, _, _ = submit_mod.execute_sql(verified, "SELECT supplier_id FROM suppliers")
    assert rows == [{"supplier_id": "S1"}]
