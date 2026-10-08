"""SCHEMA-RETRIEVE (#306): grant- and Space-bounded schema retrieval before planning.

Must-fails (each has a guard-removed proof in test_schema_retrieve_guard_mutations.py):

1. a table outside the grant is never returned — not as a candidate, a chosen
   table, a join-path bridge, a reranked pick, or a memory-only table;
2. Space B never sees Space A's tables, from the real #290 store or from a
   memory port that leaks every Space;
3. a scored-pack id never causes a memory write (the #290 store's guard).

No model is called here; the reranker cases use an in-process stub.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.answer import Answer, Provenance
from cortex_contract.schema_retrieval import SchemaRetrieval

from CortexOS.memory.space_memory import (
    SCORED_PACK_WRITE,
    Actor,
    MemoryEntry,
    MemoryStamp,
    SpaceMemory,
)
from CortexOS.schema_retrieve import (
    Bounds,
    ColumnDoc,
    DeterministicSchemaRetriever,
    GrantScope,
    JoinEdge,
    Rerank,
    SchemaCatalog,
    TableDoc,
)
from CortexOS.schema_retrieve.catalog import load_pack_catalog

ROOT = Path(__file__).resolve().parents[2]
STEWARD = Actor("steward-alice", "steward")
DELAYED_Q = "which suppliers have delayed shipments and what inventory do they hold"


@pytest.fixture(scope="module")
def dms_catalog() -> SchemaCatalog:
    return load_pack_catalog(ROOT / "packs" / "dms")


def _names(stamp: SchemaRetrieval) -> set[str]:
    """Every table name the stamp exposes, anywhere."""
    names = {c.table for c in stamp.served_candidates}
    names |= {t.table for t in stamp.served_chosen}
    for path in stamp.served_join_paths:
        names |= set(path.tables)
        for clause in path.on:
            names |= {side.strip().split(".")[0] for side in clause.split("=")}
    return names


def _grant(space: str, *tables: str) -> GrantScope:
    return GrantScope(space_id=space, tables=frozenset(tables))


def _write_table(mem: SpaceMemory, space: str, key: str, columns: list[str]) -> str:
    entry, _ = mem.write(
        space_id=space,
        kind="table",
        key=key,
        body={"columns": columns, "keys": [columns[0]], "joins": [], "row_count": 12},
        source="schema:connected_db/" + key,
        actor="cortex:schema-scan",
    )
    return entry.id


# -- must-fail 1: a table outside the grant is never returned -----------------


def test_must_fail_table_outside_grant_never_returned(dms_catalog: SchemaCatalog) -> None:
    retriever = DeterministicSchemaRetriever(dms_catalog)
    result = retriever.retrieve(DELAYED_Q, grant=_grant("space-a", "transactions", "inventory"))
    stamp = result.stamp

    assert stamp.served_chosen, "control: the granted inventory table matches"
    assert _names(stamp) <= {"transactions", "inventory"}
    raw = stamp.model_dump_json()
    for ungranted in ("suppliers", "shipments", "locations", "alerts"):
        assert ungranted not in raw, f"{ungranted} leaked into the stamp: {raw}"
    assert set(result.reduced_schema()["tables"]) <= {"transactions", "inventory"}
    assert {t.key for t in result.tables} <= {"transactions", "inventory"}


def test_must_fail_table_outside_grant_never_a_join_bridge() -> None:
    catalog = SchemaCatalog(
        tables={
            "orders": TableDoc("orders", (ColumnDoc("order_id"), ColumnDoc("customer_id"))),
            "customers": TableDoc("customers", (ColumnDoc("customer_id"), ColumnDoc("region_id"))),
            "regions": TableDoc("regions", (ColumnDoc("region_id"), ColumnDoc("region_name"))),
        },
        joins=(
            JoinEdge("orders", "customer_id", "customers", "customer_id"),
            JoinEdge("customers", "region_id", "regions", "region_id"),
        ),
    )
    retriever = DeterministicSchemaRetriever(catalog)

    full = retriever.retrieve(
        "orders by region", grant=_grant("s", "orders", "customers", "regions")
    )
    assert "customers" in _names(full.stamp), "control: customers bridges orders and regions"

    cut = retriever.retrieve("orders by region", grant=_grant("s", "orders", "regions"))
    assert _names(cut.stamp) <= {"orders", "regions"}
    assert "customers" not in cut.stamp.model_dump_json()
    assert cut.stamp.served_join_paths == []


def test_must_fail_out_of_grant_memory_table_never_returned() -> None:
    mem = SpaceMemory()
    _write_table(mem, "space-a", "payroll", ["employee_id", "salary_myr"])
    retriever = DeterministicSchemaRetriever(SchemaCatalog(tables={}), memory=mem)

    granted = retriever.retrieve("salary by employee", grant=_grant("space-a", "payroll"))
    assert [t.table for t in granted.stamp.served_chosen] == ["payroll"], "control"

    stamp = retriever.retrieve("salary by employee", grant=_grant("space-a", "orders")).stamp
    assert "payroll" not in stamp.model_dump_json()
    assert stamp.served_memory_ids_read == []


def test_must_fail_reranker_cannot_add_a_table_outside_the_grant(
    dms_catalog: SchemaCatalog,
) -> None:
    class Hostile:
        seen: list[str] = []

        def rerank(self, question: str, candidates: Sequence[TableDoc]) -> Rerank:
            self.seen.extend(t.key for t in candidates)
            return Rerank(("suppliers", "shipments", "inventory"), True, "stub_rerank")

    hostile = Hostile()
    retriever = DeterministicSchemaRetriever(dms_catalog, reranker=hostile)
    stamp = retriever.retrieve(DELAYED_Q, grant=_grant("s", "inventory", "transactions")).stamp

    assert set(hostile.seen) <= {"inventory", "transactions"}, "reranker saw an ungranted table"
    assert stamp.served_method == "stub_rerank"
    assert _names(stamp) <= {"inventory", "transactions"}
    assert "suppliers" not in stamp.model_dump_json()
    assert "shipments" not in stamp.model_dump_json()


# -- must-fail 2: Space B never sees Space A's tables --------------------------


def test_must_fail_space_b_never_sees_space_a_tables() -> None:
    mem = SpaceMemory()
    a_table = _write_table(mem, "space-a", "fx_rates", ["currency", "rate_myr"])
    a_formula, _ = mem.write(
        space_id="space-a",
        kind="formula",
        key="fx_exposure",
        body={"definition": "SUM(rate_myr)", "tables": ["fx_rates"]},
        source="steward:steward-alice",
        actor=STEWARD.actor_id,
    )
    retriever = DeterministicSchemaRetriever(SchemaCatalog(tables={}), memory=mem)
    question = "fx exposure: rate by currency"

    seen_a = retriever.retrieve(question, grant=_grant("space-a", "fx_rates")).stamp
    assert [t.table for t in seen_a.served_chosen] == ["fx_rates"], "control: A sees its table"
    assert set(seen_a.served_memory_ids_read) == {a_table, a_formula.id}

    seen_b = retriever.retrieve(question, grant=_grant("space-b", "fx_rates")).stamp
    assert seen_b.served_space_id == "space-b"
    assert seen_b.served_chosen == [] and seen_b.served_candidates == []
    assert seen_b.served_memory_ids_read == []
    assert "fx_rates" not in seen_b.model_dump_json()


class _LeakyMemory:
    """A memory port that ignores the Space it is asked for (a store bug)."""

    def __init__(self, store: SpaceMemory, spaces: Sequence[str]) -> None:
        self.store = store
        self.spaces = spaces

    def read(
        self,
        *,
        space_id: str,
        kind: str,
        actor: str,
        key: str | None = None,
        scored_pack_id: str | None = None,
    ) -> tuple[list[MemoryEntry], MemoryStamp]:
        out: list[MemoryEntry] = []
        stamp: Any = None
        for space in self.spaces:
            entries, stamp = self.store.read(space_id=space, kind=kind, actor=actor, key=key)
            out.extend(entries)
        return out, stamp

    def write(self, **kwargs: Any) -> tuple[MemoryEntry, MemoryStamp]:
        return self.store.write(**kwargs)


def test_must_fail_space_b_never_sees_space_a_tables_from_a_leaky_port() -> None:
    mem = SpaceMemory()
    a_table = _write_table(mem, "space-a", "fx_rates", ["currency", "rate_myr"])
    leaky = _LeakyMemory(mem, ["space-a", "space-b"])
    retriever = DeterministicSchemaRetriever(SchemaCatalog(tables={}), memory=leaky)

    stamp = retriever.retrieve("rate by currency", grant=_grant("space-b", "fx_rates")).stamp
    assert a_table not in stamp.served_memory_ids_read
    assert stamp.served_chosen == []
    assert "fx_rates" not in stamp.model_dump_json()


def test_space_is_the_grants_never_a_caller_string() -> None:
    class Manifest:
        space_id = " space-b "
        row_predicates = {"Orders": "TRUE", "bronze.Schools": "region = 'x'"}

    grant = GrantScope.from_manifest(Manifest())
    assert grant.space_id == "space-b"
    assert grant.tables == frozenset({"orders", "bronze.schools"})


# -- must-fail 3: a scored-pack id never causes a memory write ---------------


def test_must_fail_scored_pack_id_never_writes_memory(dms_catalog: SchemaCatalog) -> None:
    mem = SpaceMemory()
    retriever = DeterministicSchemaRetriever(dms_catalog, memory=mem, remember=True)
    stamp = retriever.retrieve(
        "how many skus in inventory",
        grant=_grant("space-a", "inventory"),
        scored_pack_id="curated_ceo",
    ).stamp

    assert [t.table for t in stamp.served_chosen] == ["inventory"]
    assert stamp.served_memory_ids_written == []
    assert stamp.served_memory_refusals == [SCORED_PACK_WRITE]
    assert mem.steward_view(space_id="space-a", steward=STEWARD)[0] == []


def test_must_fail_scored_pack_catalog_source_never_writes_memory() -> None:
    """A BIRD schema retrieved outside a declared scored round still never lands."""
    catalog = SchemaCatalog(
        tables={"schools": TableDoc("schools", (ColumnDoc("cds_code"), ColumnDoc("county")))},
        source="bird_minidev:california_schools",
    )
    mem = SpaceMemory()
    retriever = DeterministicSchemaRetriever(catalog, memory=mem, remember=True)
    stamp = retriever.retrieve("schools per county", grant=_grant("space-a", "schools")).stamp

    assert [t.table for t in stamp.served_chosen] == ["schools"]
    assert stamp.served_memory_ids_written == []
    assert stamp.served_memory_refusals == [SCORED_PACK_WRITE]
    assert mem.steward_view(space_id="space-a", steward=STEWARD)[0] == []


def test_remember_learns_chosen_catalog_tables_once(dms_catalog: SchemaCatalog) -> None:
    mem = SpaceMemory()
    retriever = DeterministicSchemaRetriever(dms_catalog, memory=mem, remember=True)
    grant = _grant("space-a", "inventory")

    first = retriever.retrieve("how many skus in inventory", grant=grant).stamp
    (entry_id,) = first.served_memory_ids_written
    assert first.served_memory_refusals == []
    (entry,) = mem.steward_view(space_id="space-a", steward=STEWARD)[0]
    assert entry.id == entry_id and entry.kind == "table" and entry.key == "inventory"
    assert entry.source == "schema_retrieve:pack:dms"
    assert "sku" in entry.body["columns"]

    second = retriever.retrieve("how many skus in inventory", grant=grant).stamp
    assert second.served_memory_ids_written == []
    assert second.served_memory_ids_read == [entry_id]
    assert mem.steward_view(space_id="space-a", steward=STEWARD)[0][0].version == 1


def test_remember_off_writes_nothing(dms_catalog: SchemaCatalog) -> None:
    mem = SpaceMemory()
    retriever = DeterministicSchemaRetriever(dms_catalog, memory=mem)
    stamp = retriever.retrieve("how many skus", grant=_grant("space-a", "inventory")).stamp
    assert stamp.served_memory_ids_written == [] and stamp.served_memory_refusals == []
    assert mem.steward_view(space_id="space-a", steward=STEWARD)[0] == []


# -- ontology, warehouses, memory -------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "how many skus do we have",
        "what is the sku count",
        "how many items per category",
        "list products and their category",
    ],
)
def test_sku_synonyms_reach_inventory(dms_catalog: SchemaCatalog, question: str) -> None:
    stamp = (
        DeterministicSchemaRetriever(dms_catalog)
        .retrieve(question, grant=_grant("s", *dms_catalog.tables))
        .stamp
    )
    top = stamp.served_chosen[0]
    assert top.table == "inventory", [t.table for t in stamp.served_chosen]
    assert "sku" in {c.name for c in top.columns}


def test_sku_id_and_sku_count_identifiers() -> None:
    catalog = SchemaCatalog(
        tables={
            "stock": TableDoc("stock", (ColumnDoc("sku_id"), ColumnDoc("qty"))),
            "sku_summary": TableDoc("sku_summary", (ColumnDoc("sku_count"), ColumnDoc("as_of"))),
            "carriers": TableDoc("carriers", (ColumnDoc("carrier_id"), ColumnDoc("name"))),
        }
    )
    retriever = DeterministicSchemaRetriever(catalog)
    grant = _grant("s", "stock", "sku_summary", "carriers")

    by_id = retriever.retrieve("qty for sku_id 42", grant=grant).stamp
    assert by_id.served_chosen[0].table == "stock"
    assert any("sku_id" in r for r in by_id.served_chosen[0].columns[0].reasons)

    by_count = retriever.retrieve("latest sku count", grant=grant).stamp
    assert by_count.served_chosen[0].table == "sku_summary"
    assert by_count.served_chosen[0].columns[0].name == "sku_count"

    by_item = retriever.retrieve("items in stock", grant=grant).stamp
    assert "carriers" not in {t.table for t in by_item.served_chosen}
    assert any(
        "ontology synonym 'item'" in r
        for t in by_item.served_chosen
        for c in t.columns
        for r in c.reasons
    )


def test_multi_warehouse_names_pick_their_warehouse() -> None:
    cols = (ColumnDoc("sku"), ColumnDoc("quantity_kg"))
    catalog = SchemaCatalog(
        tables={
            "wh_a.inventory": TableDoc("wh_a.inventory", cols),
            "wh_b.inventory": TableDoc("wh_b.inventory", cols),
            "warehouse_c_stock": TableDoc("warehouse_c_stock", cols),
        }
    )
    retriever = DeterministicSchemaRetriever(catalog)
    grant = _grant("s", "wh_a.inventory", "wh_b.inventory", "warehouse_c_stock")

    for question, expected in [
        ("stock in WH-A", "wh_a.inventory"),
        ("inventory at warehouse b", "wh_b.inventory"),
        ("Warehouse C quantity", "warehouse_c_stock"),
        ("wh_a sku quantity", "wh_a.inventory"),
    ]:
        top = retriever.retrieve(question, grant=grant).stamp.served_chosen[0]
        assert top.table == expected, (question, top)
        assert any(r.startswith("names warehouse WH-") for r in top.reasons)


def test_warehouse_glossary_maps_to_location_code(dms_catalog: SchemaCatalog) -> None:
    stamp = (
        DeterministicSchemaRetriever(dms_catalog)
        .retrieve("stock on hand in warehouse A", grant=_grant("s", *dms_catalog.tables))
        .stamp
    )
    chosen = {t.table: t for t in stamp.served_chosen}
    assert {"locations", "inventory"} <= set(chosen)
    assert "location_code" in {c.name for c in chosen["locations"].columns}
    assert "quantity_kg" in {c.name for c in chosen["inventory"].columns}
    assert any(
        p.tables in (["locations", "inventory"], ["inventory", "locations"])
        for p in stamp.served_join_paths
    )


def test_formula_memory_points_at_its_columns() -> None:
    mem = SpaceMemory()
    formula, _ = mem.write(
        space_id="s",
        kind="formula",
        key="gross_margin",
        body={"definition": "SUM(revenue_myr - cost_myr) / SUM(revenue_myr)"},
        source="steward:steward-alice",
        actor=STEWARD.actor_id,
    )
    catalog = SchemaCatalog(
        tables={
            "sales": TableDoc("sales", (ColumnDoc("revenue_myr"), ColumnDoc("cost_myr"))),
            "staff": TableDoc("staff", (ColumnDoc("staff_id"),)),
        }
    )
    stamp = (
        DeterministicSchemaRetriever(catalog, memory=mem)
        .retrieve("gross margin this year", grant=_grant("s", "sales", "staff"))
        .stamp
    )
    assert [t.table for t in stamp.served_chosen] == ["sales"]
    assert stamp.served_memory_ids_read == [formula.id]
    assert "formula memory 'gross_margin'" in stamp.served_chosen[0].reasons


def test_sensitive_columns_are_never_returned(dms_catalog: SchemaCatalog) -> None:
    result = DeterministicSchemaRetriever(dms_catalog).retrieve(
        "supplier email and phone and contact person", grant=_grant("s", "suppliers")
    )
    (table,) = result.stamp.served_chosen
    names = {c.name for c in table.columns}
    assert not names & {"email", "phone", "contact_person"}
    assert not set(result.reduced_schema()["tables"]["suppliers"]["columns"]) & {
        "email",
        "phone",
        "contact_person",
    }


# -- grant matching, bounds, stamps ----------------------------------------


@pytest.mark.parametrize(
    "grant_keys, ref",
    [
        ({"orders"}, "orders"),
        ({"orders"}, "main.orders"),
        ({"orders"}, "other.orders"),
        ({"bronze.schools"}, "bronze.schools"),
        ({"bronze.schools"}, "schools"),
        ({"bronze.schools"}, "silver.schools"),
        ({"main.orders"}, "orders"),
        ({"a.b.c"}, "a.b.c"),
    ],
)
def test_grant_matching_agrees_with_the_manifest_enforcer(grant_keys: set[str], ref: str) -> None:
    from sqlglot import exp

    from CortexOS.execution.manifest import _grant_key

    parts = ref.split(".")
    table = exp.to_table(ref) if len(parts) <= 2 else None
    enforcer = table is not None and _grant_key(table, grant_keys) is not None
    assert GrantScope("s", frozenset(grant_keys)).admits(ref) is enforcer


def test_large_schema_is_bounded_and_grant_cut() -> None:
    tables = {
        f"t{i:04d}_order_line": TableDoc(
            f"t{i:04d}_order_line",
            tuple(ColumnDoc(f"order_col_{j}") for j in range(40)),
        )
        for i in range(3000)
    }
    joins = tuple(
        JoinEdge(f"t{i:04d}_order_line", "order_col_0", f"t{i + 1:04d}_order_line", "order_col_0")
        for i in range(2999)
    )
    catalog = SchemaCatalog(tables=tables, joins=joins)
    bounds = Bounds()
    retriever = DeterministicSchemaRetriever(catalog, bounds=bounds)

    every = retriever.retrieve("order lines by order", grant=_grant("s", *tables)).stamp
    assert len(every.served_candidates) == bounds.max_candidates
    assert len(every.served_chosen) <= bounds.max_tables * bounds.max_join_hops
    assert all(len(t.columns) <= bounds.max_columns for t in every.served_chosen)
    assert every.served_bounds == bounds.as_dict()

    granted = {"t0007_order_line", "t2500_order_line"}
    cut = retriever.retrieve("order lines by order", grant=_grant("s", *granted)).stamp
    assert _names(cut) <= granted


def test_stamp_carries_candidates_scores_and_chosen(dms_catalog: SchemaCatalog) -> None:
    result = DeterministicSchemaRetriever(dms_catalog, actor="test:actor").retrieve(
        DELAYED_Q, grant=_grant("space-a", *dms_catalog.tables)
    )
    stamp = result.stamp
    assert stamp.served_op == "schema_retrieve"
    assert stamp.served_method == "deterministic"
    assert stamp.served_space_id == "space-a" and stamp.served_by == "test:actor"
    assert stamp.served_at and stamp.served_provider is None and stamp.served_model is None
    scores = [c.score for c in stamp.served_candidates]
    assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)
    candidates = {c.table for c in stamp.served_candidates}
    for table in stamp.served_chosen:
        assert table.reasons, table
        assert table.table in candidates or any(r.startswith("bridge") for r in table.reasons)
    assert stamp.served_join_paths and all(p.on and p.reason for p in stamp.served_join_paths)
    assert SchemaRetrieval.model_validate_json(stamp.model_dump_json()) == stamp


def test_retrieval_is_deterministic(dms_catalog: SchemaCatalog) -> None:
    retriever = DeterministicSchemaRetriever(dms_catalog)
    grant = _grant("s", *dms_catalog.tables)
    one = retriever.retrieve(DELAYED_Q, grant=grant).stamp.model_dump(exclude={"served_at"})
    two = retriever.retrieve(DELAYED_Q, grant=grant).stamp.model_dump(exclude={"served_at"})
    assert one == two


def test_no_match_is_stamped_not_guessed(dms_catalog: SchemaCatalog) -> None:
    stamp = (
        DeterministicSchemaRetriever(dms_catalog)
        .retrieve("zzz qqq", grant=_grant("s", *dms_catalog.tables))
        .stamp
    )
    assert stamp.served_chosen == [] and stamp.served_candidates == []
    assert stamp.served_reason == "no granted table matched the question"


def test_reduced_schema_drops_into_the_l2_prompt_block(dms_catalog: SchemaCatalog) -> None:
    from packs.dms.generative.schema_retrieval import schema_prompt_block

    result = DeterministicSchemaRetriever(dms_catalog).retrieve(
        DELAYED_Q, grant=_grant("s", "suppliers", "shipments")
    )
    block = schema_prompt_block(result.reduced_schema())
    assert "- shipments(" in block and "- suppliers(" in block
    assert "shipments.supplier_id = suppliers.supplier_id" in block
    assert "inventory" not in block


# -- contract: absent unless retrieval ran ----------------------------------


def _answer(**extra: Any) -> Answer:
    return Answer(
        answer="ok",
        audit_id="a1",
        route="metric",
        provenance=Provenance(layer="metric", badge="governed_metric"),
        **extra,
    )


def test_answer_without_retrieval_serialises_without_the_key() -> None:
    plain = json.loads(_answer().model_dump_json())
    assert "schema_retrieval" not in plain
    assert "schema_retrieval" not in _answer().model_dump()
    assert set(plain) == set(Answer.model_fields) - {"schema_retrieval"}


def test_answer_with_retrieval_round_trips(dms_catalog: SchemaCatalog) -> None:
    stamp = (
        DeterministicSchemaRetriever(dms_catalog)
        .retrieve("how many skus", grant=_grant("s", "inventory"))
        .stamp
    )
    answer = _answer(schema_retrieval=stamp.model_dump())
    body = json.loads(answer.model_dump_json())
    assert body["schema_retrieval"]["served_chosen"][0]["table"] == "inventory"
    assert Answer.model_validate(body).schema_retrieval == stamp
