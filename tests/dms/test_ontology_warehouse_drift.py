"""The packs/dms ontology + semantic layer must describe the warehouse Cortex seeds.

Root-cause class (live prove on aa98768): ``object_types.yaml`` and
``semantic_layer.yaml`` are the column lists every static SQL check and every
model prompt is built from, and nothing tied them to a real warehouse. When they
name a column the executing warehouse lacks, model SQL using it passes Cortex's
check and fails the binder downstream.

This loads every table.column the ontology and semantic layer reference and
asserts it exists in the warehouse Cortex's own seed builds
(``CortexOS.dms.warehouse_db.load_inventory_csv`` over ``data/samples``). The
DMS serving warehouse is a different, narrower schema; that disagreement is a
cross-repo residual, not something this test decides.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from CortexOS.dms.warehouse_db import load_inventory_csv
from CortexOS.execution.schema_check import check_columns, warehouse_schema
from CortexOS.execution.warehouse import close_cached_connections

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "packs" / "dms"


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def seeded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, frozenset[str]]]:
    db = tmp_path_factory.mktemp("seed") / "cortex_seed.duckdb"
    load_inventory_csv(db_path=db)
    close_cached_connections()
    schema = warehouse_schema(db)
    close_cached_connections()
    assert schema, "Cortex seed produced no warehouse tables"
    yield schema


def _ontology_refs() -> list[tuple[str, str, str]]:
    """(where, table, column) for every column the ontology names."""
    refs: list[tuple[str, str, str]] = []
    for obj in _yaml(PACK / "ontology" / "object_types.yaml")["object_types"]:
        refs.append(("object_types.primary_key", obj["id"], obj["primary_key"]))
        for prop in obj["properties"]:
            refs.append(("object_types.properties", obj["id"], prop["name"]))
    for link in _yaml(PACK / "ontology" / "link_types.yaml")["link_types"]:
        refs.append((f"link_types.{link['id']}", link["from_object"], link["from_property"]))
        refs.append((f"link_types.{link['id']}", link["to_object"], link["to_property"]))
    return refs


def _semantic_refs() -> list[tuple[str, str, str]]:
    sem = _yaml(PACK / "semantic_layer.yaml")
    refs: list[tuple[str, str, str]] = []
    for table, spec in (sem.get("tables") or {}).items():
        for col in spec.get("columns") or []:
            refs.append(("semantic.tables", table, col))
    for join in sem.get("joins") or []:
        refs.append(("semantic.joins", join["from_table"], join["from_column"]))
        refs.append(("semantic.joins", join["to_table"], join["to_column"]))
    for term in sem.get("glossary") or []:
        if term.get("maps_to") and term.get("table"):
            refs.append((f"semantic.glossary[{term['term']}]", term["table"], term["maps_to"]))
    return refs


def _missing(refs: list[tuple[str, str, str]], schema: dict[str, frozenset[str]]) -> list[str]:
    out = []
    for where, table, col in refs:
        if table not in schema:
            out.append(f"{where}: table {table} (not in warehouse)")
        elif col.lower() not in schema[table]:
            out.append(f"{where}: {table}.{col}")
    return out


def test_every_ontology_column_exists_in_the_seeded_warehouse(seeded) -> None:
    refs = _ontology_refs()
    assert len(refs) > 60  # the loader really read the files
    assert _missing(refs, seeded) == []


def test_every_semantic_layer_column_exists_in_the_seeded_warehouse(seeded) -> None:
    refs = _semantic_refs()
    assert len(refs) > 60
    assert _missing(refs, seeded) == []


def test_sensitive_columns_name_real_columns(seeded) -> None:
    sem = _yaml(PACK / "semantic_layer.yaml")
    every = set().union(*seeded.values())
    assert [c for c in sem.get("sensitive_columns") or [] if c not in every] == []


def test_certified_queries_bind_against_the_seeded_warehouse(seeded) -> None:
    """The verified-query layer reads the same columns; hold it to the same schema."""
    bad = []
    for row in _yaml(PACK / "semantic" / "certified_queries.yaml")["certified"]:
        result = check_columns(row["sql"], seeded)
        if not result.ok:
            bad.append(f"{row['id']}: {result.violations}")
    assert bad == []


def test_the_drift_check_can_fail(seeded) -> None:
    """A gate that cannot fail is not a gate: a drifted ref must be reported."""
    drifted = [("probe", "transactions", "timestamp_utc"), ("probe", "nosuch", "x")]
    assert _missing(drifted, seeded) == [
        "probe: transactions.timestamp_utc",
        "probe: table nosuch (not in warehouse)",
    ]
