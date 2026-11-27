"""JOIN-GRANT-ZW: a character the gate and DuckDB split differently hides a table.

sqlglot keeps U+200B, U+2060 and U+FEFF inside an identifier and writes them
back unquoted; DuckDB reads them as whitespace. So
``FROM orders AS o<U+200B>CROSS<U+200B>JOIN<U+200B>users`` is one table to every
gate and a cross join to DuckDB: ``users`` is read with no predicate, no grant
and no join line. Found by the #408 adversary round; the manifest enforcer, the
join grant and the caller-catalog gate all served it on main.

The rule (``CortexOS.sql_codepoints``) refuses a class of characters before any
parse. The sweep below runs every code point through both tokenizers, so a
new disagreement fails here instead of serving.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timedelta, timezone

import duckdb
import pytest
import sqlglot
from sqlglot import exp
from sqlglot.dialects.duckdb import DuckDB

from CortexOS import sql_codepoints
from CortexOS.execution import manifest as manifest_mod
from CortexOS.execution.manifest import (
    Manifest,
    SqlNotAnalyzable,
    VerifiedManifest,
    enforce_manifest,
)
from CortexOS.insights import caller_sql, schema_context

ZW = ("​", "⁠", "﻿")
#: Each hides ``users`` from the parse; DuckDB reads it.
HIDDEN = {
    f"cross_join_{ord(z):04x}": f"SELECT * FROM orders AS o{z}CROSS{z}JOIN{z}users WHERE amount > 0"
    for z in ZW
}
HIDDEN["union_all_feff"] = (
    "SELECT amount FROM orders AS o﻿UNION﻿ALL﻿SELECT﻿tenant_id﻿FROM﻿users"
)
HIDDEN["positional_join_200b"] = (
    "SELECT * FROM orders AS o​POSITIONAL​JOIN​users"
)


@pytest.fixture(scope="module")
def verified() -> VerifiedManifest:
    now = datetime.now(timezone.utc)
    manifest = Manifest(
        session_id="sess-zw",
        org_id="acme",
        pool_id="pool-a",
        issuer_key_id="int-1",
        allowed_paths=[],
        row_predicates={"orders": "tenant_id = 'a'"},
        issued_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=5)).isoformat(),
        signature="not-checked-here",
    )
    return VerifiedManifest(manifest=manifest, issuer_kid="int-1", verified_at=now)


def _warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE orders (tenant_id VARCHAR, amount INTEGER)")
    con.execute("CREATE TABLE users (tenant_id VARCHAR, name VARCHAR)")
    con.execute("INSERT INTO orders VALUES ('a', 5), ('b', 7)")
    con.execute("INSERT INTO users VALUES ('b', 'other-tenant-user')")
    return con


# -- the bypass is real ---------------------------------------------------------


@pytest.mark.parametrize("name", list(HIDDEN))
def test_the_parse_sees_one_table_and_duckdb_reads_two(name: str) -> None:
    sql = HIDDEN[name]
    seen = {t.name for t in sqlglot.parse_one(sql, read="duckdb").find_all(exp.Table)}
    assert seen == {"orders"}
    con = _warehouse()
    con.execute(sql).fetchall()
    con.execute("ALTER TABLE users RENAME TO users_gone")
    with pytest.raises(duckdb.CatalogException):
        con.execute(sql).fetchall()


# -- every gate refuses it -------------------------------------------------------


@pytest.mark.parametrize("name", list(HIDDEN))
def test_must_fail_manifest_refuses_a_hidden_table(name: str, verified: VerifiedManifest) -> None:
    with pytest.raises(SqlNotAnalyzable) as caught:
        enforce_manifest(HIDDEN[name], verified)
    assert "U+" in str(caught.value)


@pytest.mark.parametrize("name", list(HIDDEN))
def test_must_fail_caller_catalog_gate_refuses_a_hidden_table(name: str) -> None:
    out = caller_sql.validate_caller_sql(HIDDEN[name], {"orders": ["tenant_id", "amount"]})
    assert out["ok"] is False and out["sql"] is None
    assert out["reason"].startswith("SQL carries U+")


@pytest.mark.parametrize("name", list(HIDDEN))
def test_must_fail_join_grant_refuses_a_hidden_table(name: str) -> None:
    schema = "SCHEMA\n- orders\n- tenant_id text\n- amount integer\n- users\n- tenant_id text\n"
    assert schema_context.ungranted_reason(HIDDEN[name], schema) == schema_context.UNANALYSABLE_JOIN


def test_guard_removed_manifest_serves_the_hidden_table_without_its_predicate(
    verified: VerifiedManifest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the check off, the enforcer passes it and DuckDB returns another tenant's row."""
    monkeypatch.setattr(manifest_mod, "hidden_codepoint_reason", lambda sql: None)
    served = enforce_manifest(HIDDEN["cross_join_200b"], verified)
    rows = _warehouse().execute(served).fetchall()
    assert ("a", 5, "b", "other-tenant-user") in rows


def test_guard_removed_caller_gate_passes_the_hidden_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(caller_sql, "hidden_codepoint_reason", lambda sql: None)
    out = caller_sql.validate_caller_sql(HIDDEN["cross_join_200b"], {"orders": ["tenant_id", "amount"]})
    assert out["ok"] is True and out["tables"] == ["orders"]


# -- what stays allowed ---------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT amount\nFROM orders\tWHERE amount > 0\r\n",
        "SELECT amount FROM orders WHERE tenant_id = 'café'",
        'SELECT amount AS "Äpfel" FROM orders',
        "SELECT\u00a0amount FROM orders",
        "SELECT\u3000amount FROM orders",
        "SELECT amount FROM orders WHERE tenant_id = '\U0001f4e6'",
        # Real data values: a ZWJ emoji sequence, Persian with ZWNJ, a soft
        # hyphen, a right-to-left mark, a Unicode 15 emoji, a private-use glyph.
        "SELECT amount FROM orders WHERE tenant_id = '\U0001f469\u200d\U0001f4bb'",
        "SELECT amount FROM orders WHERE tenant_id = 'می\u200cخواهم'",
        "SELECT amount FROM orders WHERE tenant_id = 'Lager\u00adbestand'",
        "SELECT amount FROM orders WHERE tenant_id = '\u200fשלום'",
        "SELECT amount FROM orders WHERE tenant_id = '\U0001fae8'",
        "SELECT amount FROM orders WHERE tenant_id = '\uf8ff'",
    ],
    ids=[
        "tab-lf-cr", "accent-in-literal", "non-ascii-alias", "nbsp", "ideographic-space",
        "emoji", "zwj-emoji", "zwnj-persian", "soft-hyphen", "rlm-hebrew", "unicode15-emoji",
        "private-use",
    ],
)
def test_must_pass_ordinary_sql_is_not_refused(sql: str, verified: VerifiedManifest) -> None:
    assert sql_codepoints.hidden_codepoint(sql) is None
    enforce_manifest(sql, verified)
    assert caller_sql.validate_caller_sql(sql, {"orders": ["tenant_id", "amount"]})["ok"] is True


@pytest.mark.parametrize("ch", ["\x00", "\x01", "\x0c", "\x1b", "\x7f", "\u200b", "\u2060", "\ufeff"])
def test_refused_set_is_controls_and_the_measured_split(ch: str) -> None:
    assert sql_codepoints.hidden_codepoint(f"SELECT 1 AS a{ch}b") == f"U+{ord(ch):04X}"


# -- the sweep -------------------------------------------------------------------


def _duckdb_aliases(con: duckdb.DuckDBPyConnection, statements: list[str]) -> list[list[str] | None]:
    """Each statement's select-list aliases as DuckDB's parser reads them (None: no parse)."""
    rows = con.execute(
        "SELECT i, json_serialize_sql(s) FROM "
        "(SELECT unnest(range(len(?))) AS i, unnest(?) AS s) ORDER BY i",
        [statements, statements],
    ).fetchall()
    out: list[list[str] | None] = []
    for _i, serialized in rows:
        parsed = json.loads(serialized)
        if parsed.get("error"):
            out.append(None)
        else:
            out.append([c.get("alias") for c in parsed["statements"][0]["node"]["select_list"]])
    return out


def _duckdb_splits(texts: list[str]) -> list[str]:
    """The ``a<ch>b`` texts DuckDB does not read back as one identifier.

    Parsed as ``SELECT 1 AS t1, 1 AS t2, ...`` 512 at a time; a chunk that does
    not come back as exactly its own aliases is decided one text at a time.
    """
    con = duckdb.connect(":memory:")
    chunks = [texts[i : i + 512] for i in range(0, len(texts), 512)]
    statements = ["SELECT " + ", ".join(f"1 AS {t}" for t in chunk) for chunk in chunks]
    split: list[str] = []
    for chunk, aliases in zip(chunks, _duckdb_aliases(con, statements), strict=True):
        if aliases == chunk:
            continue
        singles = _duckdb_aliases(con, [f"SELECT 1 AS {t}" for t in chunk])
        split.extend(t for t, got in zip(chunk, singles, strict=True) if got != [t])
    return split


def _sqlglot_keeps(ch: str, tokenizer: object) -> bool:
    try:
        tokens = tokenizer.tokenize(f"a{ch}b")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - sqlglot refusing it is not a hidden split
        return False
    return len(tokens) == 1 and tokens[0].text == f"a{ch}b"


def _sqlglot_kept(chars: list[str], tokenizer: object) -> list[str]:
    """The ``chars`` sqlglot keeps inside ``a<ch>b``, tokenized 512 at a time.

    A chunk whose tokens are exactly ``a<ch>b , a<ch>b , ...`` keeps all of
    them; any other chunk is decided one character at a time.
    """
    kept: list[str] = []
    for start in range(0, len(chars), 512):
        chunk = chars[start : start + 512]
        texts = [f"a{ch}b" for ch in chunk]
        try:
            tokens = tokenizer.tokenize(" , ".join(texts))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - decided one by one below
            tokens = []
        if [t.text for t in tokens[::2]] == texts and len(tokens) == 2 * len(texts) - 1:
            kept.extend(chunk)
        else:
            kept.extend(ch for ch in chunk if _sqlglot_keeps(ch, tokenizer))
    return kept


def test_the_sweep_sees_the_known_split() -> None:
    """The sweep below can fail: the three characters of the bypass are found by it."""
    tokenizer = DuckDB().tokenizer_class()
    texts = [f"a{z}b" for z in ZW if _sqlglot_keeps(z, tokenizer)]
    assert len(texts) == 3
    assert _duckdb_splits(texts) == texts


def test_no_allowed_code_point_is_split_differently_by_the_two_tokenizers() -> None:
    """Every code point the rule allows, where sqlglot keeps ``a<ch>b`` as one
    identifier, must be one identifier to DuckDB too.

    Equivalent to "every split code point is refused". On sqlglot 30.33 /
    DuckDB 1.5.6 the split set is 27 code points (24 controls, U+200B, U+2060,
    U+FEFF), all refused. An upgrade that adds one fails here.
    """
    tokenizer = DuckDB().tokenizer_class()
    allowed = [
        chr(cp)
        for cp in range(0x110000)
        if not 0xD800 <= cp <= 0xDFFF and sql_codepoints.hidden_codepoint(chr(cp)) is None
    ]
    kept = [f"a{ch}b" for ch in _sqlglot_kept(allowed, tokenizer)]
    assert len(kept) > 1_000_000, "the sweep must cover the identifier alphabet"
    split = [
        f"U+{ord(text[1]):04X} {unicodedata.category(text[1])}" for text in _duckdb_splits(kept)
    ]
    assert split == []
