"""NAME-ECHO-CX-01 unit cases for CortexOS.api.response_scrub.

Which names an attempt references (parsed SQL, not words), how they are matched
(the DuckDB fold), which names are kept (grant, caller's own words, row keys)
and which fields of an Answer may change (free text only).
"""

from __future__ import annotations

from types import SimpleNamespace

from cortex_contract.answer import Answer, Badge, PlanSqlReconfirm, Provenance

from CortexOS.api.response_scrub import (
    WITHHELD,
    NameScrub,
    build_scrub,
    error_identifiers,
    scrub_answer,
    scrub_http_detail,
    sql_identifiers,
)
from CortexOS.sql_attempts import AttemptRecord, note_error, note_sql, recording


def _grant(*tables: str) -> SimpleNamespace:
    """A stand-in grant: the scrub reads only ``row_predicates`` from it."""
    return SimpleNamespace(row_predicates={t: "TRUE" for t in tables})


def _record(*sql: str, errors: tuple[str, ...] = ()) -> AttemptRecord:
    return AttemptRecord(sql=list(sql), errors=list(errors))


# -- references ---------------------------------------------------------------------


def test_sql_identifiers_are_tables_schemas_and_columns_not_labels() -> None:
    sql = (
        "WITH totals AS (SELECT sku, SUM(qty) AS total FROM vault.stock s GROUP BY sku) "
        "SELECT t.sku, t.total FROM totals t JOIN ghost_tbl g ON g.ref = t.sku ORDER BY total"
    )
    names = sql_identifiers(sql)
    assert {"vault", "stock", "sku", "qty", "ghost_tbl", "ref", "total"} <= names
    # Labels the statement defines are not references.
    assert not names & {"totals", "s", "t", "g"}


def test_order_by_an_output_alias_is_not_a_reference() -> None:
    assert "n" not in sql_identifiers(
        "SELECT txn_type, COUNT(*) AS n FROM tx GROUP BY 1 ORDER BY n"
    )
    # The same name in the select list is a reference.
    assert "zz" in sql_identifiers("SELECT zz AS zz FROM tx ORDER BY zz")


def test_unparseable_sql_falls_back_to_identifier_tokens() -> None:
    names = sql_identifiers('SELECT zz_a FROM "Zz B" WHERE (')
    assert {"zz_a", "Zz B"} <= names
    assert "SELECT" not in names and "WHERE" not in names


def test_error_identifiers_are_double_quoted_and_split_at_dots() -> None:
    text = (
        'BINDER_ERROR: Referenced column "zz_col" not found! Candidate bindings: '
        '"payroll.salary", "1" and \'?\''
    )
    assert error_identifiers(text) == {"zz_col", "payroll", "salary"}


# -- matching -------------------------------------------------------------------------


def test_match_folds_ascii_case_only_and_respects_word_edges() -> None:
    scrub = NameScrub(frozenset({"zz_vault", "äpfel"}))
    assert scrub.text("UNKNOWN_TABLE:ZZ_Vault, zz_vault.x") == (
        f"UNKNOWN_TABLE:{WITHHELD}, {WITHHELD}.x"
    )
    assert scrub.text("zz_vaults and my_zz_vault stay") == "zz_vaults and my_zz_vault stay"
    # Only ASCII letters fold, as DuckDB binds: "ÄPFEL" is not "äpfel".
    assert scrub.text("Äpfel äpfel") == f"Äpfel {WITHHELD}"


def test_granted_tables_and_their_columns_are_kept(tmp_path, monkeypatch) -> None:
    import duckdb

    from CortexOS.execution import submit

    lake = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(lake))
    con.execute("CREATE TABLE inventory (SKU VARCHAR, qty INTEGER)")
    con.execute("CREATE TABLE payroll (salary INTEGER)")
    con.close()
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    record = _record("SELECT sku, qty, salary, zz_ghost FROM Inventory JOIN zz_vault USING (sku)")
    scrub = build_scrub(record, verified=_grant("main.inventory"))
    # sku and qty are columns of the granted table; salary is a column of an
    # ungranted one, zz_* exist nowhere.
    assert scrub.withheld == frozenset({"salary", "zz_ghost", "zz_vault"})


def test_no_grant_keeps_nothing_but_the_callers_words() -> None:
    record = _record("SELECT zz_ghost FROM zz_vault")
    scrub = build_scrub(
        record, verified=None, caller_texts=["show me ZZ_VAULT please"], caller_names=["x"]
    )
    assert scrub.withheld == frozenset({"zz_ghost"})


def test_error_text_names_are_withheld_unless_granted() -> None:
    record = _record(errors=('Candidate bindings: "zz_secret.salary", "main.inventory"',))
    scrub = build_scrub(record, verified=_grant("main.inventory"))
    assert scrub.withheld == frozenset({"zz_secret", "salary"})


def test_recording_is_per_request_and_noop_outside() -> None:
    note_sql("SELECT a FROM outside")
    with recording() as outer:
        note_sql("SELECT a FROM t1")
        with recording() as inner:
            note_error("boom")
        note_error('"x"')
    assert outer.sql == ["SELECT a FROM t1"] and outer.errors == ['"x"']
    assert inner.sql == [] and inner.errors == ["boom"]


# -- fields ------------------------------------------------------------------------------


def _answer(**kw) -> Answer:
    base = {
        "answer": "Not answered: UNKNOWN_TABLE:zz_vault.",
        "audit_id": "a",
        "route": "refused",
        "provenance": Provenance(layer="refused", badge=Badge.ABSTAIN, assumptions="zz_vault"),
        "assumptions": ["UNKNOWN_TABLE:zz_vault", "plan: read inventory"],
        "suggestions": ["How many zz_vault rows?"],
        "served_reason": "UNKNOWN_TABLE:zz_vault",
        "reconfirm": PlanSqlReconfirm(why="no zz_vault", closest_question="sum zz_vault"),
    }
    base.update(kw)
    return Answer(**base)


def test_scrub_answer_changes_free_text_only() -> None:
    record = _record("SELECT * FROM zz_vault")
    out = scrub_answer(_answer(), record, verified=_grant("inventory"))
    assert out.answer == f"Not answered: UNKNOWN_TABLE:{WITHHELD}."
    assert out.assumptions == [f"UNKNOWN_TABLE:{WITHHELD}", "plan: read inventory"]
    assert out.suggestions == [f"How many {WITHHELD} rows?"]
    assert out.served_reason == f"UNKNOWN_TABLE:{WITHHELD}"
    assert out.provenance.assumptions == WITHHELD
    assert out.reconfirm is not None
    assert out.reconfirm.why == f"no {WITHHELD}"
    assert out.reconfirm.closest_question == f"sum {WITHHELD}"
    assert out.provenance.badge == Badge.ABSTAIN and out.route == "refused"


def test_served_answer_text_rows_sql_and_sources_are_untouched() -> None:
    rows = [{"zz_vault": "zz_vault", "n": 1}]
    sources = [{"ref_id": "inventory", "kind": "table"}]
    served = _answer(
        answer="Result: zz_vault = zz_vault",
        sql_used="SELECT zz_vault, 1 AS n FROM inventory",
        rows=rows,
        contributing_sources=sources,
        assumptions=["L2 SQL over zz_ghost"],
    )
    record = _record(served.sql_used or "", "SELECT zz_ghost FROM inventory")
    out = scrub_answer(served, record, verified=_grant("inventory"))
    assert out.answer == served.answer
    assert out.sql_used == served.sql_used
    assert out.rows == rows
    assert out.contributing_sources == served.contributing_sources
    # A row key is already on the wire; it is kept. zz_ghost is not.
    assert out.assumptions == [f"L2 SQL over {WITHHELD}"]


def test_nothing_to_withhold_returns_the_same_answer() -> None:
    answer = _answer()
    assert scrub_answer(answer, _record(), verified=None) is answer


def test_http_detail_keeps_code_and_scrubs_message() -> None:
    record = _record("SELECT zz_ghost FROM zz_vault")
    detail = {"code": "zz_vault", "message": "table 'zz_vault' is not named by this manifest"}
    out = scrub_http_detail(detail, record, verified=None)
    assert out == {
        "code": "zz_vault",
        "message": f"table '{WITHHELD}' is not named by this manifest",
    }
    assert scrub_http_detail("zz_ghost failed", record, verified=None) == f"{WITHHELD} failed"
