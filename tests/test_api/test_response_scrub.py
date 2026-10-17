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


def _render(why: str, closest: str) -> str:
    return f"Reconfirm: {why} Closest question: {closest} Yes runs that question."


def test_closest_question_with_a_withheld_word_is_replaced_whole() -> None:
    """Yes resends closest_question; it never carries a withheld marker."""
    record = _record("SELECT total FROM inventory")
    rec = PlanSqlReconfirm(why="no total column", closest_question="what is the total sku")
    answer = _answer(answer=_render(rec.why, rec.closest_question), reconfirm=rec)
    asked: list[int] = []

    def fallback() -> str:
        asked.append(1)
        return "What are the distinct sku values in inventory?"

    out = scrub_answer(
        answer,
        record,
        verified=_grant("inventory"),
        closest_fallback=fallback,
        reconfirm_text=_render,
    )
    assert out.reconfirm is not None
    assert out.reconfirm.closest_question == "What are the distinct sku values in inventory?"
    assert out.reconfirm.why == f"no {WITHHELD} column"
    # Re-rendered from the scrubbed why and the replacement question.
    assert out.answer == _render(f"no {WITHHELD} column", out.reconfirm.closest_question)
    assert asked == [1]


def test_untouched_closest_question_does_not_build_a_fallback() -> None:
    record = _record("SELECT total FROM inventory")
    rec = PlanSqlReconfirm(why="no total column", closest_question="how many sku in inventory")

    def fallback() -> str:
        raise AssertionError("not needed")

    out = scrub_answer(
        _answer(answer=_render(rec.why, rec.closest_question), reconfirm=rec),
        record,
        verified=_grant("inventory"),
        closest_fallback=fallback,
        reconfirm_text=_render,
    )
    assert out.reconfirm is not None
    assert out.reconfirm.closest_question == "how many sku in inventory"


def test_a_fallback_that_would_be_scrubbed_is_not_used() -> None:
    record = _record("SELECT total FROM inventory")
    rec = PlanSqlReconfirm(why="w", closest_question="the total")
    out = scrub_answer(
        _answer(answer="other text about total", reconfirm=rec),
        record,
        verified=_grant("inventory"),
        closest_fallback=lambda: "sum the total",
        reconfirm_text=_render,
    )
    assert out.reconfirm is not None
    assert out.reconfirm.closest_question == f"the {WITHHELD}"
    # Not the reconfirm rendering, so the answer is scrubbed as free text.
    assert out.answer == f"other text about {WITHHELD}"


# -- every form a name can take in model SQL (review of 13fa80f) -------------------------
#
# Each case is SQL a model can write and the names in it that no grant covers.
# Whatever wording a refusal uses, it quotes a name as the SQL spells it, so the
# scrub built from the SQL alone must leave none of those names in the SQL text
# itself, nor in the refusal strings the review reported.

NAME_FORMS: dict[str, tuple[str, set[str]]] = {
    "four_part_table": (
        "SELECT * FROM zz_cat.zz_db.zz_vault.zz_band",
        {"zz_cat", "zz_db", "zz_vault", "zz_band"},
    ),
    "table_function": ("SELECT * FROM zz_vault()", {"zz_vault"}),
    "table_function_with_column_argument": ("SELECT * FROM zz_vault(sku)", {"zz_vault"}),
    "schema_qualified_table_function": ("SELECT * FROM zz_s.zz_vault(1)", {"zz_s", "zz_vault"}),
    "join_table_function": ("SELECT sku FROM inventory JOIN zz_vault() ON TRUE", {"zz_vault"}),
    "lateral_table_function": (
        "SELECT sku FROM inventory, LATERAL zz_vault(inventory.sku)",
        {"zz_vault"},
    ),
    "table_function_star": ("SELECT zz_vault.* FROM zz_vault()", {"zz_vault"}),
    "scalar_function": ("SELECT zz_vault(sku) FROM inventory", {"zz_vault"}),
    "qualified_scalar_function": (
        "SELECT zz_s.zz_t.zz_vault(sku) FROM inventory",
        {"zz_s", "zz_t", "zz_vault"},
    ),
    "five_part_column": (
        "SELECT zz_cat.zz_db.zz_tbl.zz_col.zz_field.zz_leaf FROM inventory",
        {"zz_cat", "zz_db", "zz_tbl", "zz_col", "zz_field", "zz_leaf"},
    ),
    "using_column": (
        "SELECT sku FROM inventory JOIN inventory AS o USING (zz_col)",
        {"zz_col"},
    ),
    "user_defined_type": (
        "SELECT CAST(sku AS zz_type), sku::zz_type2 FROM inventory",
        {"zz_type", "zz_type2"},
    ),
    "quoted_names": ('SELECT "zz Col" FROM "zz_s"."Zz Vault"', {"zz Col", "zz_s", "Zz Vault"}),
    "quoted_path": ("SELECT * FROM 'zz_vault.parquet'", {"zz_vault.parquet"}),
    "exclude_and_columns": (
        "SELECT * EXCLUDE (zz_ex), COLUMNS(zz_cols) FROM inventory",
        {"zz_ex", "zz_cols"},
    ),
    "pivot": ("PIVOT zz_vault ON zz_col USING sum(zz_val)", {"zz_vault", "zz_col", "zz_val"}),
    "insert_columns": ("INSERT INTO zz_vault (zz_col) VALUES (1)", {"zz_vault", "zz_col"}),
    "describe": ("DESCRIBE zz_vault", {"zz_vault"}),
    "command_call": ("CALL zz_vault()", {"zz_vault"}),
    "command_show": ("SHOW zz_vault", {"zz_vault"}),
    "subquery_and_window": (
        "SELECT sku FROM inventory WHERE sku IN (SELECT zz_sq FROM zz_sub) "
        "QUALIFY row_number() OVER (PARTITION BY zz_part) = 1",
        {"zz_sq", "zz_sub", "zz_part"},
    ),
}


def test_every_name_form_is_a_referenced_name() -> None:
    for form, (sql, names) in NAME_FORMS.items():
        assert names <= sql_identifiers(sql), (form, names - sql_identifiers(sql))


def test_every_name_form_is_withheld_wherever_a_refusal_quotes_it() -> None:
    for form, (sql, names) in NAME_FORMS.items():
        scrub = build_scrub(_record(sql), verified=_grant("inventory"))
        quoted = [
            sql,
            *(f"UNKNOWN_TABLE:{n}, TABLE_FUNCTION:LATERAL:{n}, {n}() '{n}'" for n in names),
        ]
        for text in quoted:
            out = scrub.text(text)
            for name in names:
                assert name.lower() not in out.lower(), (form, name, out)


def test_review_probes_are_withheld() -> None:
    """The two blocking probes of the review, with the refusal text it reported."""
    four = "SELECT * FROM zz_cat.zz_db.zz_payroll_vault.zz_salary_band"
    scrub = build_scrub(_record(four), verified=_grant("transactions"))
    assert scrub.text("UNKNOWN_TABLE:zz_cat.zz_db.zz_payroll_vault.zz_salary_band") == (
        f"UNKNOWN_TABLE:{WITHHELD}.{WITHHELD}.{WITHHELD}.{WITHHELD}"
    )
    function = "SELECT * FROM zz_payroll_vault()"
    scrub = build_scrub(_record(function), verified=_grant("transactions"))
    assert scrub.text("(refused: TABLE_FUNCTION:zz_payroll_vault)") == (
        f"(refused: TABLE_FUNCTION:{WITHHELD})"
    )
    assert scrub.text("TABLE_FUNCTION:LATERAL:zz_payroll_vault") == (
        f"TABLE_FUNCTION:LATERAL:{WITHHELD}"
    )


def test_names_the_statement_defines_are_still_not_references() -> None:
    sql = (
        "WITH cte_t AS (SELECT 1 AS one) "
        "SELECT list_filter([1], lambda lam_x: lam_x > 0) AS kept_alias, "
        "{'struct_key': 1} AS s, sum(qty) OVER win_w AS running "
        "FROM read_csv('f.csv', option_name := true) AS rel(col_label), cte_t "
        "PIVOT (sum(qty) FOR sku IN ('pivot_value')) "
        "WINDOW win_w AS (ORDER BY sku)"
    )
    names = sql_identifiers(sql)
    assert {"qty", "sku"} <= names
    assert not names & {
        "cte_t",
        "lam_x",
        "kept_alias",
        "struct_key",
        "s",
        "running",
        "win_w",
        "option_name",
        "rel",
        "col_label",
        "pivot_value",
        "one",
    }, names


def test_engine_function_and_type_names_are_not_withheld(tmp_path, monkeypatch) -> None:
    """A built-in the model calls (sqlglot does not know ``hash`` or ``bar``) is not a data name."""
    import duckdb

    from CortexOS.execution import submit

    lake = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(lake))
    con.execute("CREATE TABLE inventory (sku VARCHAR, qty INTEGER)")
    con.close()
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)
    sql = (
        "SELECT hash(sku), bar(qty, 0, 10), strip_accents(sku), CAST(qty AS HUGEINT) "
        "FROM main.inventory, range(3)"
    )
    assert build_scrub(_record(sql), verified=_grant("inventory")).withheld == frozenset()
    # An invented function next to them is still withheld, and so is a column
    # that happens to share a built-in's name: only a name in call position is
    # an engine name.
    sql = "SELECT hash(sku), zz_fn(qty), bar FROM inventory"
    assert build_scrub(_record(sql), verified=_grant("inventory")).withheld == frozenset(
        {"zz_fn", "bar"}
    )


def test_default_schema_of_a_bare_grant_is_kept() -> None:
    record = _record("SELECT * FROM main.zz_vault JOIN other.inventory ON TRUE")
    assert build_scrub(record, verified=_grant("inventory")).withheld == frozenset(
        {"zz_vault", "other"}
    )
    # A qualified grant keeps its own schema, not the default one.
    assert build_scrub(record, verified=_grant("other.inventory")).withheld == frozenset(
        {"zz_vault", "main"}
    )


def test_engine_catalog_errors_name_unquoted_objects() -> None:
    text = (
        "EXPLAIN_FAILED:CATALOG_ERROR: Scalar Function with name zz_fn does not exist! "
        'Did you mean "atan2"? / Sequence with name zz_seq does not exist! / '
        'Table with name "zz_s.zz_t" does not exist because schema "zz_s" does not exist.'
    )
    assert error_identifiers(text) == {"zz_fn", "atan2", "zz_seq", "zz_s", "zz_t"}


def test_ansi_highlight_in_a_parse_error_does_not_hide_a_name() -> None:
    scrub = NameScrub(frozenset({"zz_ghost_col"}))
    text = "PARSE_ERROR:Invalid expression. SELECT \x1b[4mzz_ghost_col\x1b[0m FROM"
    out = scrub.text(text)
    assert "zz_ghost_col" not in out
    assert out == f"PARSE_ERROR:Invalid expression. SELECT {WITHHELD} FROM"


# -- review of 96f0bb8 ----------------------------------------------------------------


def _schema_lake(tmp_path) -> tuple[object, object]:
    """A lake with ``main.inventory`` and a same-named ``hr.inventory``, plus an
    attached second catalog that has its own ``main.inventory``."""
    import duckdb

    other = tmp_path / "other.duckdb"
    con = duckdb.connect(str(other))
    con.execute("CREATE TABLE inventory (zz_other_catalog_col INTEGER)")
    con.close()
    lake = tmp_path / "lake.duckdb"
    con = duckdb.connect(str(lake))
    con.execute("CREATE TABLE inventory (sku VARCHAR, qty INTEGER)")
    con.execute("CREATE SCHEMA hr")
    con.execute("CREATE TABLE hr.inventory (zz_hr_secret_col INTEGER, sku VARCHAR)")
    con.close()
    return lake, other


def test_a_bare_grant_key_covers_only_the_default_schema_table(tmp_path, monkeypatch) -> None:
    """A bare key grants ``main.<table>`` only (manifest ``_grant_key``), so the
    columns of a same-named table in another schema are not granted names."""
    from CortexOS.execution import submit
    from CortexOS.execution.submit import granted_column_names

    lake, _ = _schema_lake(tmp_path)
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)

    assert granted_column_names(_grant("inventory")) == frozenset({"sku", "qty"})
    assert granted_column_names(_grant("INVENTORY")) == frozenset({"sku", "qty"})
    assert granted_column_names(_grant("main.inventory")) == frozenset({"sku", "qty"})
    # A qualified key names its own schema only.
    assert granted_column_names(_grant("hr.inventory")) == frozenset({"zz_hr_secret_col", "sku"})

    for sql in (
        "SELECT zz_hr_secret_col FROM hr.inventory",
        "SELECT zz_hr_secret_col FROM inventory",
    ):
        scrub = build_scrub(_record(sql), verified=_grant("inventory"))
        assert "zz_hr_secret_col" in scrub.withheld, (sql, scrub.withheld)
        out = scrub.text("step gate: (refused: UNKNOWN_COLUMN:zz_hr_secret_col)")
        assert "zz_hr_secret_col" not in out, out
    # Positive control: the granted table's columns are still kept.
    scrub = build_scrub(_record("SELECT sku, qty FROM inventory"), verified=_grant("inventory"))
    assert scrub.withheld == frozenset()


def test_tables_of_an_attached_catalog_are_not_the_granted_table(tmp_path, monkeypatch) -> None:
    """information_schema spans every attached catalog; a grant names the default one."""
    import duckdb

    from CortexOS.execution import submit, warehouse
    from CortexOS.execution.submit import granted_column_names

    lake, other = _schema_lake(tmp_path)
    monkeypatch.setattr(submit, "DEFAULT_DB", lake)

    def attached(path, **kwargs):
        # A fresh connection, so the ATTACH never reaches a cached one.
        del kwargs
        con = duckdb.connect(str(warehouse.warehouse_path(path)))
        con.execute(f"ATTACH '{other}' AS zz_other (READ_ONLY)")
        return con

    monkeypatch.setattr(submit, "get_connection", attached)
    assert granted_column_names(_grant("inventory")) == frozenset({"sku", "qty"})


LONG_TABLE = "zz_payroll_vault_" + "x" * 120
LONG_COLUMN = "zz_ghost_col_" + "y" * 200


def test_a_name_longer_than_128_characters_is_withheld() -> None:
    assert len(LONG_TABLE) > 128 and len(LONG_COLUMN) > 128
    sql = f"SELECT txn_type, {LONG_COLUMN} FROM {LONG_TABLE}"
    record = _record(sql, errors=(f"Table with name {LONG_TABLE} does not exist!",))
    scrub = build_scrub(record, verified=_grant("transactions"))
    assert {LONG_TABLE.lower(), LONG_COLUMN.lower()} <= scrub.withheld
    for text in (
        f"PLAN_SQL_GATE_REFUSED: UNKNOWN_TABLE:{LONG_TABLE}",
        f"UNKNOWN_COLUMN:{LONG_COLUMN.upper()}",
        f'Referenced column "{LONG_COLUMN}" not found',
    ):
        out = scrub.text(text)
        assert LONG_TABLE.lower() not in out.lower() and LONG_COLUMN.lower() not in out.lower()
        assert WITHHELD in out
    # Read from error text alone, quoted or as a catalog-error object.
    assert LONG_TABLE in error_identifiers(f"Table with name {LONG_TABLE} does not exist!")
    assert LONG_COLUMN in error_identifiers(f'Referenced column "{LONG_COLUMN}" not found')
    detail = scrub_http_detail(
        {"code": "X", "message": f"refused: Table with name {LONG_TABLE} does not exist!"},
        _record(),
        verified=_grant("transactions"),
    )
    assert LONG_TABLE not in str(detail), detail
