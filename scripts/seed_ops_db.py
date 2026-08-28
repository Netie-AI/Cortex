"""Deterministic seed for the DMS ops database (SQLite).

``packs/data/dms_ops.db`` used to be tracked in git, but nothing in it is
source: it is the runtime store the engine appends to as it runs. The F1 audit
ledger (``packs/dms/audit/ledger.py``), the V0 warehouse tables
(``packs/dms/vision/warehouse_store.py``), learned query skills
(``packs/dms/semantic/query_skills.py``), captured task skills
(``packs/dms/skills/capture.py``), compliance task events
(``packs/dms/tasks/gate.py``) and chat threads (``packs/dms/chat/threads.py``)
all write into it — even a plain ``pytest`` run bumps ``support_count`` on every
query skill it matches — so the tracked file was re-dirtied by every local run
(57 KB -> 212 KB inside one session). It is generated now; this builds it.

Built from committed source data only:

``schema``
    each owning module's own ``init_*_schema``, so the DDL stays single-sourced
    and this script never restates it.
``ontology_*``
    ``packs/<pack>/ontology/*.yaml``, compiled by
    ``CortexOS.ontology.registry.compile_to_sqlite``.
``dms_locations``
    :data:`DEMO_LOCATIONS` — the same tree ``CortexOS.dms.seed_demo`` builds,
    but with uuid5 ids and a fixed timestamp so two seeds are byte-identical.

Every other table is left empty on purpose. The ledger is an append-only hash
chain that has to start at genesis, and the skill tables are a learned cache the
engine repopulates on first use; seeding either would invent history rather than
restore it.

Idempotent — existing rows are left alone, so it is safe to run on a live DB::

    python -m scripts.seed_ops_db                 # default path, keep existing rows
    python -m scripts.seed_ops_db --force         # delete the file and rebuild
    python -m scripts.seed_ops_db --db /tmp/o.db  # explicit target
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Matches query_skills._sqlite_path(). NOTE: the ledger and the other five
# ops-DB consumers default to data/dms_ops.db instead — with no DMS_OPS_DB set,
# query skills and the ledger land in two different files. Seed the path the
# pack actually writes to; unifying the two defaults is a separate change.
DEFAULT_DB = ROOT / "packs" / "data" / "dms_ops.db"

# Ids are derived from the location code, so re-seeding produces the same rows
# and a rebuilt file can be compared against an old one. The qr tokens are
# therefore public demo values, not secrets: a real deployment creates its
# locations through the warehouse API, which mints uuid4 tokens per install.
_SEED_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://cortex.local/dms/ops-db/seed/v1")
SEED_CREATED_AT = "2026-01-01T00:00:00+00:00"

# code, kind, parent code, capacity_volume — mirrors CortexOS.dms.seed_demo.
DEMO_LOCATIONS: tuple[tuple[str, str, str | None, float | None], ...] = (
    ("Z-DEMO", "zone", None, None),
    ("R-D01", "rack", "Z-DEMO", None),
    ("B-D01-A", "bin", "R-D01", 50.0),
    ("B-D01-B", "bin", "R-D01", 50.0),
)

# Reported by seed(); the ops DB's full table surface across all six writers.
OPS_TABLES = (
    "dms_audit_ledger",
    "dms_locations",
    "dms_items",
    "dms_movements",
    "dms_threads",
    "dms_messages",
    "dms_task_events",
    "dms_skills",
    "dms_query_skills",
    "ontology_object_types",
    "ontology_properties",
    "ontology_link_types",
    "ontology_action_types",
    "ontology_functions",
)


def _seed_id(kind: str, code: str) -> str:
    return str(uuid.uuid5(_SEED_NS, f"{kind}:{code}"))


def resolve_db_path(db_path: Path | str | None = None) -> Path:
    """Explicit path, else DMS_OPS_DB / SQLITE_DB_PATH, else the repo default."""
    if db_path is not None:
        return Path(db_path)
    env = os.environ.get("DMS_OPS_DB") or os.environ.get("SQLITE_DB_PATH")
    return Path(env) if env else DEFAULT_DB


def create_schema(con: sqlite3.Connection) -> None:
    """Create every ops-DB table, each through the module that owns its DDL."""
    from packs.dms.chat.threads import init_chat_schema
    from packs.dms.semantic.query_skills import init_query_skills_schema
    from packs.dms.skills.capture import init_skills_schema
    from packs.dms.vision.warehouse_store import init_warehouse_schema

    # Each of these calls init_ledger_schema first, so the ledger table exists
    # regardless of ordering. init_skills_schema also covers dms_task_events.
    init_warehouse_schema(con)
    init_chat_schema(con)
    init_skills_schema(con)
    init_query_skills_schema(con)


def seed_locations(con: sqlite3.Connection) -> int:
    """Insert any missing demo location. Returns the number of rows written."""
    written = 0
    for code, kind, parent_code, capacity in DEMO_LOCATIONS:
        if con.execute("SELECT 1 FROM dms_locations WHERE code = ?", (code,)).fetchone():
            continue
        con.execute(
            "INSERT INTO dms_locations "
            "(id, parent_id, kind, code, qr_token, capacity_volume, tenant_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'default', ?)",
            (
                _seed_id("location", code),
                _seed_id("location", parent_code) if parent_code else None,
                kind,
                code,
                _seed_id("qr", code),
                capacity,
                SEED_CREATED_AT,
            ),
        )
        written += 1
    con.commit()
    return written


def table_counts(con: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in OPS_TABLES:
        try:
            counts[table] = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error:
            continue
    return counts


def seed(
    db_path: Path | str | None = None,
    *,
    force: bool = False,
    pack_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Build the ops DB at ``db_path``. Idempotent unless ``force`` is set."""
    from CortexOS.ontology.registry import compile_to_sqlite

    path = resolve_db_path(db_path)
    if force and path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Compiled first: compile_to_sqlite opens its own connection, and holding a
    # second write connection to the same file across it risks a lock wait.
    # Only the dms pack ships ontology YAML today, so an active pack without an
    # ontology/ dir is a skip, not a failure — the rest of the seed still applies.
    try:
        ontology: dict[str, Any] = compile_to_sqlite(pack_dir, db_path=path)
    except FileNotFoundError:
        ontology = {"skipped": "pack has no ontology/ directory"}

    con = sqlite3.connect(str(path))
    try:
        create_schema(con)
        locations = seed_locations(con)
        counts = table_counts(con)
    finally:
        con.close()

    return {
        "path": str(path),
        "locations_written": locations,
        "ontology": ontology,
        "tables": counts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None, help=f"target SQLite file (default: {DEFAULT_DB})")
    parser.add_argument("--pack-dir", default=None, help="pack whose ontology YAML to compile")
    parser.add_argument(
        "--force",
        action="store_true",
        # Plain ASCII: argparse help is printed to a cp1252 console on Windows.
        help="delete the file first (discards ledger history and learned skills)",
    )
    args = parser.parse_args(argv)

    result = seed(args.db, force=args.force, pack_dir=args.pack_dir)
    print(f"ops DB seeded at {result['path']}")
    print(f"  demo locations written: {result['locations_written']}")
    for table, count in result["tables"].items():
        print(f"  {table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
