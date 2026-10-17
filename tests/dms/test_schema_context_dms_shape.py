"""schema_context in the shape DMS actually renders.

The fixtures under ``tests/dms/fixtures/`` are the literal output of the DMS
SCHEMA-RETRIEVE renderer for its own CRM and finance fixtures. Each header
records the DMS commit and blob it was rendered from. Cortex does not import
DMS; the bytes are recorded once and verified against the header sha256.

Review of #351 (MAJOR): the old parser read none of the CRM render's tables,
so POST /v1/insights answered 422 for DMS's own prompt. Model calls here are
the FreeRoute ``complete`` seam. No network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import sqlglot
from fastapi.testclient import TestClient
from sqlglot import exp

from CortexOS.insights import schema_context as schema_mod
from CortexOS.insights.caller_sql import validate_caller_sql
from packs.dms.security.rate_limit import reset_limiter

FIXTURES = Path(__file__).parent / "fixtures"
HEADERS = {
    "Authorization": "Bearer ov_schema_ctx_test",
    "X-API-Key": "dms-demo-admin-key",
}
# The CRM render was built for this question (DMS _crm_prompt).
INTENT = "which accounts are midmarket"
ANSWER_VALIDATED = (
    "Validated SQL via FreeRoute (caller catalog: tables and declared columns). "
    "Numbers not certified: static sqlglot guardrail: not EXPLAINed, "
    "not manifest-enforced, not executed."
)
CRM_CATALOG = {
    "account": ["account_id", "status"],
    "contact": [
        "account_id",
        "contact_id",
        "customer_name",
        "date_of_birth",
        "email",
        "memo",
        "passport_no",
        "phone",
    ],
}
FINANCE_CATALOG = {
    "invoice": ["balance_amt", "invoice_id", "status"],
    "payment": ["invoice_id", "payment_id"],
}
SQL_MIDMARKET_CONTACTS = (
    "SELECT a.account_id, COUNT(c.contact_id) AS contacts "
    "FROM account a JOIN contact c ON c.account_id = a.account_id "
    "WHERE lower(a.status) = 'midmarket' "
    "GROUP BY a.account_id ORDER BY a.account_id"
)
# Synthetic lake in the fixture's shape: (account_id, status).
LAKE_ACCOUNTS = [(1, "MIDMARKET"), (2, "ENTERPRISE"), (3, "MIDMARKET"), (4, "SMB")]
# (contact_id, account_id)
LAKE_CONTACTS = [(10, 1), (11, 1), (12, 2), (13, 3), (14, 4), (15, 1)]


def _load(name: str) -> str:
    """Fixture body after ``---``, verified against the header sha256."""
    raw = (FIXTURES / name).read_text(encoding="utf-8").replace("\r\n", "\n")
    header, sep, body = raw.partition("\n---\n")
    assert sep, f"{name}: no --- separator"
    assert body.endswith("\n")
    body = body[:-1]
    recorded = [
        line.split(":", 1)[1].strip()
        for line in header.splitlines()
        if line.startswith("# body sha256:")
    ]
    assert recorded == [hashlib.sha256(body.encode("utf-8")).hexdigest()], name
    return body


CRM = _load("dms_schema_context_crm.txt")
FINANCE = _load("dms_schema_context_finance.txt")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(tmp_path / "routes.db"))
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.delenv(schema_mod.ROW_CAP_ENV, raising=False)
    reset_limiter(per_minute=600)
    from CortexOS.api.app import create_app

    return TestClient(create_app(), headers={"X-API-Key": "dms-demo-admin-key"})


def _bind(monkeypatch: pytest.MonkeyPatch, sql_for) -> list[tuple[str, str]]:
    """Arm FreeRoute and stub ``complete``. Returns the prompts it was sent."""
    from CortexOS.crew import freeroute as fr

    prompts: list[tuple[str, str]] = []

    async def complete(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, kwargs
        prompts.append((purpose, prompt))
        if purpose == "think":
            return {"ok": True, "text": "use the supplied schema", "prompt_tokens": 2,
                    "completion_tokens": 1, "total_tokens": 3}
        return {"ok": True, "text": sql_for(prompt), "prompt_tokens": 5,
                "completion_tokens": 4, "total_tokens": 9}

    armed = {"ok": True, "armed": True, "vault_live": True, "local_keys": False,
             "cloud_keys": False, "detail": "test-armed", "live_5000_ci": False}
    monkeypatch.setattr(fr, "arming", lambda: armed)
    monkeypatch.setattr(fr, "complete", complete)
    return prompts


def _ask(api: TestClient, schema: str) -> tuple[int, dict[str, Any]]:
    response = api.post(
        "/v1/insights",
        json={"intent": INTENT, "ask": False, "generate": True, "schema_context": schema},
        headers=HEADERS,
    )
    return response.status_code, response.json()


def _generate_count(prompts: list[tuple[str, str]]) -> int:
    return sum(1 for purpose, _prompt in prompts if purpose == "generative_ask")


def _crm_lake():
    """The caller Space's CRM tables, in the fixture's shape. Rows from LAKE_*."""
    import duckdb

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE account (account_id BIGINT, status VARCHAR)")
    con.execute(
        "CREATE TABLE contact (account_id BIGINT, contact_id BIGINT, customer_name VARCHAR, "
        "date_of_birth DATE, email VARCHAR, memo VARCHAR, passport_no VARCHAR, phone VARCHAR)"
    )
    con.executemany("INSERT INTO account VALUES (?, ?)", LAKE_ACCOUNTS)
    con.executemany(
        "INSERT INTO contact (contact_id, account_id) VALUES (?, ?)", LAKE_CONTACTS
    )
    return con


def _oracle_midmarket_contacts() -> list[tuple[int, int]]:
    midmarket = {aid for aid, status in LAKE_ACCOUNTS if status.lower() == "midmarket"}
    counts: dict[int, int] = {}
    for _cid, aid in LAKE_CONTACTS:
        if aid in midmarket:
            counts[aid] = counts.get(aid, 0) + 1
    return sorted(counts.items())


# --- the recorded DMS render parses --------------------------------------


def test_dms_crm_render_parses_to_its_tables_and_columns() -> None:
    assert schema_mod.problem(CRM) is None
    catalog = schema_mod.parse_schema_context(CRM)
    assert catalog.tables == CRM_CATALOG
    assert catalog.joins == frozenset({("account", "contact")})
    assert schema_mod.parsed_catalog(CRM) == CRM_CATALOG
    # Not tables: description words, type words, the filter-hint value.
    for word in ("customer", "organisation", "person", "bigint", "varchar", "midmarket"):
        assert word not in catalog.tables


def test_dms_finance_render_with_measures_parses() -> None:
    """The MEASURES line (aliases, ``-- description``) and the filter hint declare nothing."""
    assert schema_mod.problem(FINANCE) is None
    catalog = schema_mod.parse_schema_context(FINANCE)
    assert catalog.tables == FINANCE_CATALOG
    assert catalog.joins == frozenset({("invoice", "payment")})
    for word in ("open_receivable", "f", "amount", "owed", "unpaid"):
        assert word not in catalog.tables


# --- answer path: DMS's own prompt through POST /v1/insights -------------


def test_insights_answers_with_the_dms_crm_render(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 with validated SQL. The SQL, run on the CRM lake, returns the oracle rows."""
    prompts = _bind(monkeypatch, lambda _prompt: SQL_MIDMARKET_CONTACTS)
    status, body = _ask(api, CRM)
    assert status == 200, body
    # The envelope the customer reads: abstain prose, no number, no green badge.
    assert body["status"] == "ABSTAIN", body.get("answer")
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["answer"] == ANSWER_VALIDATED
    assert body["usage"] == {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}
    sql = str(body.get("sql_used") or "")
    assert (body.get("generative") or {}).get("sql") == sql
    assert schema_mod.schema_idents(sql) >= {"account", "contact", "status"}
    # Rows: the returned SQL on the lake gives the oracle, computed in Python.
    rows = sorted(_crm_lake().execute(sql).fetchall())
    assert rows == _oracle_midmarket_contacts() == [(1, 3), (3, 1)]
    # One model SQL call, and it was offered DMS's string verbatim.
    assert _generate_count(prompts) == 1
    sql_prompt = next(p for purpose, p in prompts if purpose == "generative_ask")
    fenced = sql_prompt.split(schema_mod.UNTRUSTED_BEGIN, 1)[1]
    assert CRM in fenced.split(schema_mod.UNTRUSTED_END, 1)[0]


# --- must-fail: SQL outside the string is refused -------------------------


@pytest.mark.parametrize(
    ("sql", "answer"),
    [
        (
            # DMS dropped this ungranted table from the render.
            "SELECT note_body FROM restricted_note WHERE note_body IS NOT NULL",
            "schema_context:ungranted_table:restricted_note",
        ),
        (
            # A word in contact's description is not a table: the parsed
            # catalog gate refuses it.
            "SELECT 1 AS n FROM person WHERE 1 = 1",
            "sql reads a table outside the caller catalog: "
            "table person is not in the caller catalog",
        ),
        (
            # A type word is not a table either.
            "SELECT 1 AS n FROM varchar WHERE 1 = 1",
            "sql reads a table outside the caller catalog: "
            "table varchar is not in the caller catalog",
        ),
    ],
    ids=["table_not_rendered", "description_word", "type_word"],
)
def test_must_fail_table_outside_the_dms_render_is_refused(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str, answer: str
) -> None:
    prompts = _bind(monkeypatch, lambda _prompt: sql)
    status, body = _ask(api, CRM)
    assert status == 200, body
    assert body["status"] == "REFUSE"
    assert body["answer"] == answer
    assert body["values"] == []
    assert not body.get("sql_used")
    assert not (body.get("generative") or {}).get("sql")
    assert sql not in json.dumps(body)
    assert _generate_count(prompts) == 1


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        (
            "SELECT account_id, credit_limit FROM account WHERE status = 'MIDMARKET'",
            "column credit_limit is not declared for account",
        ),
        (
            # customer_name is contact's. The old parser could file a column
            # under the wrong table; this one must not.
            "SELECT customer_name FROM account WHERE account_id = 1",
            "column customer_name is not declared for account",
        ),
    ],
    ids=["column_not_rendered", "column_of_the_other_table"],
)
def test_must_fail_column_outside_the_dms_render_is_refused(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str, needle: str
) -> None:
    from CortexOS.crew.cot_climb import HORIZON

    prompts = _bind(monkeypatch, lambda _prompt: sql)
    status, body = _ask(api, CRM)
    assert status == 200, body
    assert body["status"] == "REFUSE", body.get("answer")
    assert needle in str(body.get("answer") or "")
    assert body["values"] == []
    assert not body.get("sql_used")
    assert sql not in json.dumps(body)
    # Retried by name inside the horizon; the refused SQL never went back.
    assert _generate_count(prompts) == HORIZON
    assert all(sql not in prompt for _purpose, prompt in prompts)


def test_must_fail_join_named_only_in_a_description_is_refused(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """contact's description says 'person at an account'. That is not a JOINS line."""
    no_joins = CRM.split("\nJOINS\n", 1)[0] + "\nFILTER HINTS\n- account.status = midmarket"
    assert "description=person at an account" in no_joins
    assert schema_mod.parse_schema_context(no_joins).joins == frozenset()
    prompts = _bind(monkeypatch, lambda _prompt: SQL_MIDMARKET_CONTACTS)
    status, body = _ask(api, no_joins)
    assert status == 200, body
    assert body["status"] == "REFUSE"
    assert body["answer"] == "schema_context:ungranted_join:account+contact"
    assert not body.get("sql_used")
    assert _generate_count(prompts) == 1


def test_dms_over_budget_fallback_is_a_named_abstain_with_no_model_call(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DMS sends ``DIALECT: x`` + ``SCHEMA`` when the shortlist does not fit."""
    prompts = _bind(monkeypatch, lambda _prompt: SQL_MIDMARKET_CONTACTS)
    status, body = _ask(api, "DIALECT: mysql\nSCHEMA")
    assert status == 200, body
    assert body["status"] == "ABSTAIN"
    assert body["badge"] == "abstain"
    assert body["values"] == []
    assert body["refuse_reason"] == schema_mod.SCHEMA_CONTEXT_EMPTY
    assert body["answer"] == (
        "Abstained (schema_context_empty): schema_context declares no table; "
        "nothing to generate against. No number invented."
    )
    assert prompts == []


# --- parser: the review's shapes and fail-closed lines -------------------


def test_review_shapes_description_and_foreign_key_lines() -> None:
    """#351 review 1(b) and 1(c): no merge into the previous table, no dropped column."""
    schema = "\n".join(
        [
            "SCHEMA",
            "- customers",
            "- id integer",
            "- region text",
            "- orders reason=score:0.40 description=customer orders",
            "- amount decimal description=net of orders.discount",
            "- order_id integer",
            "- customer_id integer foreign_key=customers.id",
        ]
    )
    parsed = schema_mod.parsed_catalog(schema)
    assert parsed == {
        "customers": ["id", "region"],
        "orders": ["amount", "order_id", "customer_id"],
    }
    assert not validate_caller_sql("SELECT sum(amount) FROM customers", parsed)["ok"]
    assert validate_caller_sql("SELECT sum(amount) FROM orders WHERE order_id > 0", parsed)["ok"]


_ADJACENT = "\n".join(
    [
        "SCHEMA",
        "- customers",
        "- id integer",
        "- region text",
        "- orders reason=score:0.40 description=customer orders",
        "- amount decimal description=net of orders.discount",
        "- order_id integer",
        "- customer_id integer foreign_key=customers.id",
    ]
)


@pytest.mark.parametrize(
    ("sql", "needle"),
    [
        (
            "SELECT sum(amount) AS n FROM customers WHERE id > 0",
            "column amount is not declared for customers",
        ),
        (
            "SELECT region FROM orders WHERE order_id > 0",
            "column region is not declared for orders",
        ),
    ],
    ids=["described_tables_column_on_plain_table", "plain_tables_column_on_described_table"],
)
def test_must_fail_undeclared_column_on_adjacent_described_table(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, sql: str, needle: str
) -> None:
    """#400 (b) on the answer path: a described table after a plain one is its own table."""
    from CortexOS.crew.cot_climb import HORIZON

    prompts = _bind(monkeypatch, lambda _prompt: sql)
    status, body = _ask(api, _ADJACENT)
    assert status == 200, body
    assert body["status"] == "REFUSE", body.get("answer")
    assert needle in str(body.get("answer") or "")
    assert body["values"] == []
    assert not body.get("sql_used")
    assert sql not in json.dumps(body)
    assert _generate_count(prompts) == HORIZON


def test_hand_written_schema_still_parses() -> None:
    """The bare-head shape the earlier tests use keeps its catalog."""
    schema = "\n".join(
        [
            "DIALECT: generic",
            "SCHEMA",
            "- alpha_ledger",
            "- qty integer samples=zzsc01alpha",
            "- beta_entry",
            "- ref_code integer",
            "JOINS",
            "- alpha_ledger.id = beta_entry.ref_code (ledger_entry)",
        ]
    )
    catalog = schema_mod.parse_schema_context(schema)
    assert catalog.tables == {"alpha_ledger": ["qty", "id"], "beta_entry": ["ref_code"]}
    assert catalog.joins == frozenset({("alpha_ledger", "beta_entry")})


ACCOUNT_HEAD = "- account reason=score:0.35 description=customer organisation"


def _with_line(after: str, line: str) -> tuple[str, int]:
    """CRM with ``line`` inserted after the first line equal to ``after``."""
    lines = CRM.split("\n")
    at = lines.index(after) + 1
    lines.insert(at, line)
    return "\n".join(lines), at + 1


@pytest.mark.parametrize(
    ("after", "line", "section", "why"),
    [
        ("SCHEMA", "zzbad free text", "SCHEMA", "expected a '- ' entry"),
        ("SCHEMA", "ZZBAD", "SCHEMA", "expected a '- ' entry"),
        ("SCHEMA", "- zzbad_col bigint", "SCHEMA", "column line before any table head"),
        (ACCOUNT_HEAD, "- 9zzbad bigint", "SCHEMA", "column name is not an identifier"),
        (ACCOUNT_HEAD, "- account.zzbad bigint", "SCHEMA", "column name is not an identifier"),
        ("SCHEMA", "- zzbad reason=score:0.10 zzbogus=1", "SCHEMA", "table head"),
        ("SCHEMA", "- a.b.zzbad reason=score:0.10", "SCHEMA", "table name"),
        (
            # A spaced table name must not become a column of the table above.
            ACCOUNT_HEAD,
            "- zzbad table reason=score:0.10 description=spaced name",
            "SCHEMA",
            "table name",
        ),
        ("JOINS", "- zzbad.id = account.account_id (x)", "JOINS", "join"),
        ("JOINS", "- contact.account_id zzbad", "JOINS", "join"),
        ("FILTER HINTS", "- account.zzbad = midmarket", "FILTER HINTS", "filter hint"),
        ("FILTER HINTS", "- zzbad", "FILTER HINTS", "filter hint"),
        ("DIALECT: mysql", "DIALECT: zzbad", "preamble", "DIALECT"),
    ],
)
def test_must_fail_unparseable_line_is_named_by_number(
    after: str, line: str, section: str, why: str
) -> None:
    schema, number = _with_line(after, line)
    found = schema_mod.problem(schema)
    assert found is not None
    code, detail = found
    assert code == schema_mod.SCHEMA_CONTEXT_UNPARSED
    assert detail.startswith(f"schema_context line {number} ({section}): ")
    assert why in detail
    # The line is named, never quoted.
    assert "zzbad" not in detail.lower()
    assert schema_mod.parsed_catalog(schema) == {}
    assert schema_mod.ungranted_reason("SELECT status FROM account LIMIT 1", schema) == (
        schema_mod.UNPARSED_REASON
    )


def test_must_fail_unparseable_line_is_a_422_naming_the_line(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    schema, number = _with_line("SCHEMA", "zzbad free text")
    prompts = _bind(monkeypatch, lambda _prompt: SQL_MIDMARKET_CONTACTS)
    status, body = _ask(api, schema)
    assert status == 422, body
    assert body["refuse_reason"] == schema_mod.SCHEMA_CONTEXT_UNPARSED
    assert f"schema_context line {number} (SCHEMA)" in body["detail"]
    assert "zzbad" not in json.dumps(body)
    assert prompts == []


def test_must_fail_hint_text_cannot_open_a_second_schema_section() -> None:
    """A hint value carries question text. A newline in it must not declare a table."""
    schema = CRM + "\nSCHEMA\n- zzsecret\n- zzcol bigint"
    number = CRM.count("\n") + 2
    found = schema_mod.problem(schema)
    assert found == (
        schema_mod.SCHEMA_CONTEXT_UNPARSED,
        f"schema_context line {number} (FILTER HINTS): section label repeated or out of order",
    )
    assert "zzsecret" not in schema_mod.parsed_catalog(schema)
    assert schema_mod.parsed_catalog(schema) == {}


def test_must_fail_table_head_without_columns_is_named() -> None:
    schema = "SCHEMA\n- account reason=score:0.35 description=customer organisation\n- contact\n- id int"
    assert schema_mod.problem(schema) == (
        schema_mod.SCHEMA_CONTEXT_UNPARSED,
        "schema_context line 2 (SCHEMA): table declares no column",
    )


def test_must_fail_narrowing_folds_ascii_only() -> None:
    """DuckDB folds ASCII only. 'äpfel' is not the granted 'Äpfel'."""
    parsed = schema_mod.parsed_catalog("SCHEMA\n- äpfel\n- menge integer")
    assert parsed == {"äpfel": ["menge"]}
    assert schema_mod.narrow_catalog({"Äpfel": ["menge"]}, parsed) == {}
    same = schema_mod.parsed_catalog("SCHEMA\n- ÄPFEL\n- MENGE integer")
    assert schema_mod.narrow_catalog({"Äpfel": ["menge"]}, same) == {"Äpfel": ["menge"]}


# --- sqlglot>=25: the FROM clause key ------------------------------------


def _old_sqlglot_tree(sql: str) -> exp.Expression:
    """Parse, then key every FROM clause ``from`` as sqlglot 25 does."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    for sel in list(tree.find_all(exp.Select)):
        node = sel.args.pop("from_", None)
        if node is not None:
            sel.args["from"] = node
    return tree


@pytest.mark.parametrize("rekey", [False, True], ids=["from_", "from"])
def test_from_clause_read_on_either_sqlglot_key(
    monkeypatch: pytest.MonkeyPatch, rekey: bool
) -> None:
    def statements(sql: str) -> list[exp.Expression]:
        if rekey:
            return [_old_sqlglot_tree(sql)]
        return [sqlglot.parse_one(sql, read="duckdb")]

    monkeypatch.setattr(schema_mod, "_statements", statements)
    scan = "SELECT qty FROM alpha_ledger"
    tree = statements(scan)[0]
    assert isinstance(tree, exp.Select)
    assert schema_mod._own_tables(tree, set()) == ["alpha_ledger"]
    assert schema_mod.brute_force_reason(scan) == schema_mod.BRUTE_FORCE_SCAN
    join = (
        "SELECT a.qty FROM alpha_ledger a JOIN beta_entry b ON a.id = b.ref_code "
        "WHERE a.qty > 1"
    )
    assert schema_mod._join_pairs(statements(join)[0]) == [("alpha_ledger", "beta_entry")]
    no_join = "SCHEMA\n- alpha_ledger\n- qty integer\n- beta_entry\n- ref_code integer"
    assert schema_mod.ungranted_reason(join, no_join) == (
        "schema_context:ungranted_join:alpha_ledger+beta_entry"
    )


def test_drillthrough_reads_from_on_either_sqlglot_key() -> None:
    from CortexOS.execution.drillthrough import _table_aliases

    sql = "SELECT a.qty FROM alpha_ledger a JOIN beta_entry b ON a.id = b.ref_code"
    new = sqlglot.parse_one(sql, read="duckdb")
    old = _old_sqlglot_tree(sql)
    assert isinstance(new, exp.Select) and isinstance(old, exp.Select)
    assert _table_aliases(new)[0] == ("a", "alpha_ledger")
    assert _table_aliases(old) == _table_aliases(new)
