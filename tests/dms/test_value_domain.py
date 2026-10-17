"""VALUE-NORMALIZE-CX-01 (#387): one declared txn_type value domain, applied at every lake build.

The DMS ontology declares the canonical ``transactions.txn_type`` values and the
source spellings that map to them (``OUT`` -> ``outbound``, ``IN`` -> ``inbound``).
Cortex applies that declaration when it builds a lake, the model sees the
canonical values in its generation context, and gold files keep their source
spellings byte for byte: gold SQL is run through the same declared mapping
before it is executed against the mapped lake.

These tests assert rows (CLAUDE.md section 8): every gold query with a
canonical SQL gives the same rows on the mapped lake as it gave on the
unmapped lake, for the items the mapping touches and for the ones it does not.
"""

from __future__ import annotations

import csv
import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

from bench import accuracy
from bench.accuracy import GoldenItem, load_golden, score_answer
from bench.corpus import load_seeds
from bench.heldout import load_heldout
from CortexOS.dms import warehouse_db
from CortexOS.ontology import value_domain as vd
from CortexOS.ontology.registry import load_object_types

ROOT = Path(__file__).resolve().parents[2]
DMS_PACK = ROOT / "packs" / "dms"
GOLD_FILES = (
    ROOT / "bench" / "golden" / "dms_golden_v1.yaml",
    ROOT / "bench" / "heldout" / "c7_heldout_v1.yaml",
    ROOT / "bench" / "corpus" / "seeds_v1.yaml",
)


def _txn_domain() -> vd.ValueDomain:
    domains = vd.load_value_domains(DMS_PACK)
    hits = [d for d in domains if (d.table, d.column) == ("transactions", "txn_type")]
    assert len(hits) == 1, domains
    return hits[0]


# ── the declaration ─────────────────────────────────────────────────────────


def test_txn_type_domain_is_declared_once_in_the_ontology() -> None:
    dom = _txn_domain()
    assert dom.mapping() == {"OUT": "outbound", "IN": "inbound"}
    assert dom.canonical[:2] == ("outbound", "inbound")
    # Read through the registry: the property carries the declaration.
    txn = next(o for o in load_object_types(DMS_PACK) if o.id == "transactions")
    prop = next(p for p in txn.properties if p.name == "txn_type")
    assert [v.canonical for v in prop.values] == list(dom.canonical)
    # No other column declares a domain today.
    assert [(d.table, d.column) for d in vd.load_value_domains(DMS_PACK)] == [
        ("transactions", "txn_type")
    ]


def _pack_with(tmp_path: Path, values: list[dict[str, Any]]) -> Path:
    onto = tmp_path / "ontology"
    onto.mkdir(parents=True)
    doc = {
        "object_types": [
            {
                "id": "transactions",
                "primary_key": "txn_id",
                "properties": [
                    {"name": "txn_id", "type": "string"},
                    {"name": "txn_type", "type": "string", "values": values},
                ],
            }
        ]
    }
    (onto / "object_types.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "values",
    [
        [{"canonical": "outbound", "from": ["OUT"]}, {"canonical": "sale", "from": ["OUT"]}],
        [{"canonical": "outbound", "from": ["IN"]}, {"canonical": "IN"}],
        [{"canonical": "outbound"}, {"canonical": "outbound"}],
        [{"canonical": "", "from": ["OUT"]}],
        [{"canonical": "outbound", "from": "OUT"}],
    ],
)
def test_ambiguous_declaration_is_refused(tmp_path: Path, values: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError):
        load_object_types(_pack_with(tmp_path, values))


# ── lake build ──────────────────────────────────────────────────────────────


def _source_lake(path: Path) -> None:
    """The same build as load_inventory_csv, minus the declared mapping."""
    con = duckdb.connect(str(path))
    try:
        for table, fname in warehouse_db.TABLE_FILES.items():
            warehouse_db._load_table(con, table, warehouse_db.SAMPLES / fname)
        warehouse_db._cast_types(con)
    finally:
        con.close()


@pytest.fixture(scope="module")
def lakes(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("value_domain_lakes")
    source = base / "source.duckdb"
    mapped = base / "mapped.duckdb"
    _source_lake(source)
    warehouse_db.load_inventory_csv(db_path=mapped)
    return {"source": source, "mapped": mapped}


def _counts(path: Path) -> Counter[str]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        rows = con.execute("SELECT txn_type, COUNT(*) FROM transactions GROUP BY 1").fetchall()
    finally:
        con.close()
    return Counter({str(k): int(n) for k, n in rows})


def test_lake_build_stores_the_declared_canonical_values(lakes: dict[str, Path]) -> None:
    source, mapped = _counts(lakes["source"]), _counts(lakes["mapped"])
    assert source["OUT"] > 0 and source["IN"] > 0  # the CSVs keep source spellings
    rename = _txn_domain().mapping()
    want = Counter({rename.get(k, k): n for k, n in source.items()})
    assert mapped == want
    assert "OUT" not in mapped and "IN" not in mapped
    assert mapped["outbound"] == source["OUT"] and mapped["inbound"] == source["IN"]
    assert sum(mapped.values()) == sum(source.values())


def test_mapping_is_idempotent_and_reports_rows(lakes: dict[str, Path], tmp_path: Path) -> None:
    lake = tmp_path / "again.duckdb"
    _source_lake(lake)
    domains = vd.load_value_domains(DMS_PACK)
    con = duckdb.connect(str(lake))
    try:
        source = _counts(lakes["source"])
        assert vd.source_rows(con, domains) == source["OUT"] + source["IN"]
        first = vd.apply_to_lake(con, domains)
        assert first == {"transactions.txn_type": source["OUT"] + source["IN"]}
        assert vd.source_rows(con, domains) == 0
        assert vd.apply_to_lake(con, domains) == {"transactions.txn_type": 0}
    finally:
        con.close()


def test_missing_table_is_skipped_not_invented(tmp_path: Path) -> None:
    con = duckdb.connect(str(tmp_path / "empty.duckdb"))
    try:
        assert vd.apply_to_lake(con, vd.load_value_domains(DMS_PACK)) == {}
        assert vd.source_rows(con, vd.load_value_domains(DMS_PACK)) == 0
    finally:
        con.close()


def test_stale_unmapped_lake_is_rebuilt_before_gold_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lake built before the declaration would turn 'outbound' SQL into a silent empty."""
    stale = tmp_path / "stale.duckdb"
    _source_lake(stale)
    monkeypatch.setattr(warehouse_db, "DEFAULT_DB", stale)
    accuracy._ensure_db_loaded()
    counts = _counts(stale)
    assert "OUT" not in counts and counts["outbound"] > 0


@pytest.mark.parametrize("force_fallback", [False, True])
def test_lakehouse_silver_and_synced_warehouse_store_canonical_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, force_fallback: bool
) -> None:
    """Silver is built through the declaration; bronze keeps what the source wrote."""
    from packs.dms.lakehouse import catalog
    from scripts.lakehouse_migrate import migrate_all

    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("DMS_WAREHOUSE_DB", str(tmp_path / "dms_demo.duckdb"))
    if force_fallback:
        monkeypatch.setenv("DMS_LAKEHOUSE_FORCE_FALLBACK", "1")
    else:
        monkeypatch.delenv("DMS_LAKEHOUSE_FORCE_FALLBACK", raising=False)
    catalog.reset_mode_cache()
    try:
        result = migrate_all()
        con = catalog.connect(read_only=True)
        try:
            silver = dict(
                con.execute(
                    "SELECT txn_type, COUNT(*) FROM lake.silver.transactions GROUP BY 1"
                ).fetchall()
            )
            bronze = dict(
                con.execute(
                    "SELECT txn_type, COUNT(*) FROM lake.bronze.transactions_raw GROUP BY 1"
                ).fetchall()
            )
            gold = con.execute(
                "SELECT SUM(transaction_count) FROM lake.gold.sales_by_sku"
            ).fetchone()
        finally:
            con.close()
    finally:
        catalog.reset_mode_cache()
    assert result["warehouse_sync"].get("ok") is True, result["warehouse_sync"]
    assert "OUT" not in silver and "IN" not in silver
    assert silver["outbound"] > 0 and silver["inbound"] > 0
    assert bronze.get("OUT", 0) > 0  # raw layer is the source, unmapped
    assert gold is not None and int(gold[0]) == silver["outbound"]
    assert _counts(tmp_path / "dms_demo.duckdb")["outbound"] == silver["outbound"]


# ── gold through the same declared mapping ──────────────────────────────────


def _gold_sql() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for g in load_golden(GOLD_FILES[0]):
        if g.canonical_sql:
            out.append((f"golden:{g.id}", g.canonical_sql))
    for h in load_heldout(GOLD_FILES[1]):
        if h.canonical_sql:
            out.append((f"heldout:{h.id}", h.canonical_sql))
    for s in load_seeds(GOLD_FILES[2]):
        if s.canonical_sql:
            out.append((f"seeds:{s.id}", s.canonical_sql))
    return out


GOLD = _gold_sql()


def _rows_on(path: Path, sql: str) -> list[tuple[Any, ...]] | str:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    except Exception as exc:  # noqa: BLE001 - a broken gold query must stay broken
        return f"error:{type(exc).__name__}"
    finally:
        con.close()


def _key(rows: list[tuple[Any, ...]]) -> Counter[tuple[str, ...]]:
    return Counter(tuple(repr(v) for v in r) for r in rows)


@pytest.fixture
def on_mapped_lake(lakes: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(warehouse_db, "DEFAULT_DB", lakes["mapped"])
    return lakes["mapped"]


@pytest.mark.parametrize(("gid", "sql"), GOLD, ids=[g for g, _ in GOLD])
def test_gold_rows_on_mapped_lake_equal_gold_rows_on_source_lake(
    lakes: dict[str, Path], on_mapped_lake: Path, gid: str, sql: str
) -> None:
    want = _rows_on(lakes["source"], sql)
    try:
        got: list[tuple[Any, ...]] | str = [tuple(r.values()) for r in accuracy._run_canonical(sql)]
    except Exception as exc:  # noqa: BLE001
        got = f"error:{type(exc).__name__}"
    if isinstance(want, str):
        assert got == want, gid
        return
    assert isinstance(got, list), (gid, got)
    assert _key(got) == _key(want), gid


def test_gold_files_keep_source_spellings_and_the_affected_set_is_exact() -> None:
    domains = vd.load_value_domains(DMS_PACK)
    touched = {gid for gid, sql in GOLD if vd.canonicalize_sql(sql, domains) != sql}
    mentions = {gid for gid, sql in GOLD if "txn_type" in sql and ("'OUT'" in sql or "'IN'" in sql)}
    assert touched == mentions
    # Not vacuous: the sales gold is among them, and most gold is untouched.
    assert {"golden:sales_top5_value", "golden:sales_top3_volume"} <= touched
    assert len(touched) >= 20 and len(GOLD) - len(touched) >= 40
    for path in GOLD_FILES:
        assert "txn_type = 'OUT'" in path.read_text(encoding="utf-8")


def _golden(gid: str) -> GoldenItem:
    return next(g for g in load_golden(GOLD_FILES[0]) if g.id == gid)


def _certified_sql(cid: str) -> str:
    doc = yaml.safe_load((DMS_PACK / "semantic" / "certified_queries.yaml").read_text("utf-8"))
    return next(str(c["sql"]) for c in doc["certified"] if c["id"] == cid)


def _dict_rows(path: Path, sql: str) -> list[dict[str, Any]]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        rel = con.execute(sql)
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, r, strict=True)) for r in rel.fetchall()]
    finally:
        con.close()


def test_affected_gold_scores_the_canonical_answer_correct(on_mapped_lake: Path) -> None:
    item = _golden("sales_top5_value")
    rows = _dict_rows(on_mapped_lake, _certified_sql("cq_sales_top5_value"))
    assert len(rows) == 5
    res = score_answer(item, {"route": "metric", "rows": rows, "sql_used": "x"})
    assert res.outcome == "correct", res.detail


def test_affected_gold_scores_a_silent_empty_wrong(on_mapped_lake: Path) -> None:
    """Source-spelled SQL on the mapped lake returns nothing; gold does not score it correct."""
    item = _golden("sales_top5_value")
    rows = _dict_rows(on_mapped_lake, str(item.canonical_sql))
    assert rows == []
    res = score_answer(item, {"route": "metric", "rows": rows, "sql_used": "x"})
    assert res.outcome == "wrong"


def test_unaffected_gold_still_scores_correct(on_mapped_lake: Path) -> None:
    item = _golden("sku_count")
    rows = _dict_rows(on_mapped_lake, str(item.canonical_sql))
    res = score_answer(item, {"route": "metric", "rows": rows, "sql_used": "x"})
    assert res.outcome == "correct", res.detail
    assert vd.canonicalize_sql(str(item.canonical_sql), vd.load_value_domains(DMS_PACK)) == (
        item.canonical_sql
    )


# ── the gold rewrite reads parsed SQL, not words ────────────────────────────


@pytest.mark.parametrize(
    ("sql", "want"),
    [
        (
            "SELECT sku FROM transactions WHERE txn_type = 'OUT'",
            "SELECT sku FROM transactions WHERE txn_type = 'outbound'",
        ),
        (
            "SELECT t.sku FROM transactions AS t WHERE t.txn_type <> 'IN'",
            "SELECT t.sku FROM transactions AS t WHERE t.txn_type <> 'inbound'",
        ),
        (
            "SELECT sku FROM transactions WHERE txn_type IN ('OUT', 'ADJUST')",
            "SELECT sku FROM transactions WHERE txn_type IN ('outbound', 'ADJUST')",
        ),
        (
            "SELECT SUM(CASE WHEN 'OUT' = txn_type THEN 1 ELSE 0 END) FROM transactions",
            "SELECT SUM(CASE WHEN 'outbound' = txn_type THEN 1 ELSE 0 END) FROM transactions",
        ),
    ],
)
def test_canonicalize_rewrites_comparisons_on_the_declared_column(sql: str, want: str) -> None:
    assert vd.canonicalize_sql(sql, vd.load_value_domains(DMS_PACK)) == want


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT supplier_id FROM suppliers WHERE country = 'IN'",
        "SELECT sku FROM transactions WHERE reference_doc = 'OUT'",
        "SELECT sku FROM transactions WHERE txn_type = 'outbound'",
        "SELECT COUNT(*) FROM inventory",
    ],
)
def test_canonicalize_leaves_other_literals_byte_identical(sql: str) -> None:
    assert vd.canonicalize_sql(sql, vd.load_value_domains(DMS_PACK)) == sql


def test_canonicalize_fails_closed_on_a_predicate_it_cannot_map() -> None:
    with pytest.raises(vd.UnmappedLiteral):
        vd.canonicalize_sql(
            "SELECT sku FROM transactions WHERE txn_type LIKE 'OUT'",
            vd.load_value_domains(DMS_PACK),
        )


# ── the model sees the canonical values ─────────────────────────────────────


def test_l2_schema_block_carries_the_declared_values() -> None:
    from packs.dms.generative import schema_retrieval

    schema = schema_retrieval.retrieve("outbound transactions by sku", top_k=6)
    assert "transactions" in schema["tables"]
    block = schema_retrieval.schema_prompt_block(schema)
    assert "transactions.txn_type values: 'outbound', 'inbound'" in block
    assert "'OUT'" not in block


# ── the built lake holds the declared domain, and nothing is guessed ────────


SALES_CERTIFIED = ("cq_sales_top5_value", "cq_top3_category_sales")


def _csv_rows(samples: Path, fname: str) -> list[dict[str, str]]:
    with (samples / fname).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _sales_oracle(samples: Path, cid: str) -> list[tuple[str, float]]:
    """Rows the certified sales query must return, computed from the CSVs in Python.

    Independent of the ontology YAML and of the SQL: a sale is a row the source
    wrote as ``OUT``.
    """
    txns = [r for r in _csv_rows(samples, "transactions_clean.csv") if r["txn_type"] == "OUT"]
    if cid == "cq_sales_top5_value":
        key = {r["sku"]: r["sku"] for r in txns}
        limit = 5
    else:
        inv = _csv_rows(samples, "inventory_clean.csv")
        cats: dict[str, set[str]] = {}
        for r in inv:
            cats.setdefault(r["sku"], set()).add(r["category"])
        assert all(len(c) == 1 for c in cats.values())  # the join cannot fan out
        key = {sku: next(iter(c)) for sku, c in cats.items()}
        limit = 3
    total: dict[str, float] = {}
    for r in txns:
        if r["sku"] in key:
            k = key[r["sku"]]
            total[k] = total.get(k, 0.0) + float(r["quantity_kg"]) * float(r["unit_cost_myr"])
    ranked = sorted(total.items(), key=lambda kv: (-round(kv[1], 2), kv[0]))
    return [(k, round(v, 2)) for k, v in ranked[:limit]]


def _assert_rows(got: list[tuple[Any, ...]] | str, want: list[tuple[str, float]]) -> None:
    assert isinstance(got, list), got
    assert [r[0] for r in got] == [k for k, _ in want]
    assert [float(r[1]) for r in got] == pytest.approx([v for _, v in want], abs=0.011)


def test_built_lake_holds_exactly_the_declared_canonical_values(lakes: dict[str, Path]) -> None:
    domains = vd.load_value_domains(DMS_PACK)
    con = duckdb.connect(str(lakes["mapped"]), read_only=True)
    try:
        stored = {r[0] for r in con.execute("SELECT DISTINCT txn_type FROM transactions").fetchall()}
        assert stored == set(_txn_domain().canonical)
        assert vd.source_rows(con, domains) == 0
        assert vd.undeclared_values(con, domains) == {}
    finally:
        con.close()
    # The source lake holds spellings the declaration maps, and nothing else.
    con = duckdb.connect(str(lakes["source"]), read_only=True)
    try:
        assert vd.undeclared_values(con, domains) == {}
    finally:
        con.close()


@pytest.mark.parametrize("cid", SALES_CERTIFIED)
def test_certified_sales_rows_on_the_built_lake_equal_the_csv_oracle(
    lakes: dict[str, Path], cid: str
) -> None:
    want = _sales_oracle(warehouse_db.SAMPLES, cid)
    assert len(want) == (5 if cid == "cq_sales_top5_value" else 3)
    sql = _certified_sql(cid)
    _assert_rows(_rows_on(lakes["mapped"], sql), want)
    # Without the declared mapping the same certified SQL is a silent empty.
    assert _rows_on(lakes["source"], sql) == []


def _samples_with(tmp_path: Path, txn_id: str, spelling: str) -> Path:
    """A copy of data/samples where one transaction row carries ``spelling``."""
    samples = tmp_path / "samples"
    samples.mkdir()
    for fname in warehouse_db.TABLE_FILES.values():
        shutil.copy(warehouse_db.SAMPLES / fname, samples / fname)
    rows = _csv_rows(samples, "transactions_clean.csv")
    hit = [r for r in rows if r["txn_id"] == txn_id]
    assert len(hit) == 1 and hit[0]["txn_type"] == "OUT"
    hit[0]["txn_type"] = spelling
    with (samples / "transactions_clean.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return samples


@pytest.mark.parametrize("spelling", ["Outbound", "out", "OUTBOUND", "SALE"])
def test_undeclared_source_spelling_is_named_not_silently_mapped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    spelling: str,
) -> None:
    """Must-fail: only the declared ``from`` spellings map. Nothing folds case or guesses."""
    txn_id = "TXN-200000"
    samples = _samples_with(tmp_path, txn_id, spelling)
    monkeypatch.setattr(warehouse_db, "SAMPLES", samples)
    lake = tmp_path / "lake.duckdb"
    with caplog.at_level(logging.WARNING, logger=warehouse_db.__name__):
        warehouse_db.load_inventory_csv(db_path=lake)

    domains = vd.load_value_domains(DMS_PACK)
    con = duckdb.connect(str(lake), read_only=True)
    try:
        stored = con.execute(
            "SELECT txn_type FROM transactions WHERE txn_id = ?", [txn_id]
        ).fetchone()
        assert stored == (spelling,)  # stored as written, not turned into 'outbound'
        assert vd.undeclared_values(con, domains) == {"transactions.txn_type": {spelling: 1}}
        assert vd.source_rows(con, domains) == 0
    finally:
        con.close()
    named = [r.getMessage() for r in caplog.records if "value_not_declared" in r.getMessage()]
    assert named == [f"value_not_declared:transactions.txn_type {spelling!r}=1 "
                     "(declare it in the pack ontology)"]
    # Rows: the row is not counted as a sale, exactly as the oracle without it.
    _assert_rows(
        _rows_on(lake, _certified_sql("cq_sales_top5_value")),
        _sales_oracle(samples, "cq_sales_top5_value"),
    )
    shipped = _csv_rows(ROOT / "data" / "samples", "transactions_clean.csv")
    assert _counts(lake)["outbound"] == sum(r["txn_type"] == "OUT" for r in shipped) - 1


def test_clean_build_names_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Control for the must-fail above: the shipped samples raise no warning."""
    with caplog.at_level(logging.WARNING, logger=warehouse_db.__name__):
        warehouse_db.load_inventory_csv(db_path=tmp_path / "clean.duckdb")
    assert not [r for r in caplog.records if "value_not_declared" in r.getMessage()]
