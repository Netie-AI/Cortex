"""The ops DB is generated, not tracked — these pin the seed that generates it."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.seed_ops_db import DEMO_LOCATIONS, OPS_TABLES, seed

ROOT = Path(__file__).resolve().parents[2]
# conftest sets PACK=ruma; the ontology YAML under test belongs to the dms pack.
DMS_PACK = ROOT / "packs" / "dms"


@pytest.fixture
def seeded(tmp_path):
    db = tmp_path / "dms_ops.db"
    result = seed(db, pack_dir=DMS_PACK)
    return db, result


def _rows(db: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_seed_creates_every_ops_table(seeded):
    db, result = seeded
    assert set(result["tables"]) == set(OPS_TABLES)


def test_seed_writes_the_demo_location_tree(seeded):
    db, result = seeded
    assert result["locations_written"] == len(DEMO_LOCATIONS)
    codes = [r[0] for r in _rows(db, "SELECT code FROM dms_locations ORDER BY code")]
    assert codes == sorted(code for code, *_ in DEMO_LOCATIONS)

    # Parent links resolve, so the tree is usable and not just four loose rows.
    tree = _rows(
        db,
        "SELECT child.code, parent.code FROM dms_locations child "
        "LEFT JOIN dms_locations parent ON parent.id = child.parent_id "
        "ORDER BY child.code",
    )
    assert dict(tree) == {
        "B-D01-A": "R-D01",
        "B-D01-B": "R-D01",
        "R-D01": "Z-DEMO",
        "Z-DEMO": None,
    }


def test_seed_is_deterministic(tmp_path):
    """Two independent seeds must agree on ids, qr tokens and timestamps."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    seed(a, pack_dir=DMS_PACK)
    seed(b, pack_dir=DMS_PACK)
    query = "SELECT * FROM dms_locations ORDER BY code"
    assert _rows(a, query) == _rows(b, query)


def test_seed_is_idempotent(seeded):
    db, _ = seeded
    again = seed(db, pack_dir=DMS_PACK)
    assert again["locations_written"] == 0
    assert again["tables"]["dms_locations"] == len(DEMO_LOCATIONS)


def test_seed_leaves_runtime_tables_empty(seeded):
    """The ledger is an append-only chain and the skill tables are a learned
    cache — a seed that pre-filled either would be inventing history."""
    _, result = seeded
    for table in ("dms_audit_ledger", "dms_skills", "dms_query_skills", "dms_task_events"):
        assert result["tables"][table] == 0


def test_ontology_is_compiled_from_committed_yaml(seeded):
    from CortexOS.ontology.registry import load_object_types

    _, result = seeded
    expected = len(load_object_types(DMS_PACK))
    assert expected > 0
    assert result["tables"]["ontology_object_types"] == expected


def test_seed_skips_ontology_for_a_pack_without_yaml(tmp_path):
    """packs/ruma and packs/crm ship no ontology/ dir — that is a skip, not a crash."""
    result = seed(tmp_path / "dms_ops.db", pack_dir=tmp_path / "no_such_pack")
    assert "skipped" in result["ontology"]
    assert result["locations_written"] == len(DEMO_LOCATIONS)
