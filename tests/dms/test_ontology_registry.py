"""O1 ontology registry — parity with semantic_layer.yaml, idempotent compile.

The ontology YAML is a TRANSCRIPTION of semantic_layer.yaml plus primary keys,
descriptions, and agent_visible flags. These tests lock the two files together
so they cannot silently drift (in either direction), lock the tool registry to
the F8 runner allowlist, and prove the SQLite compile is idempotent.
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import yaml

from packs.dms.ontology import registry

ROOT = Path(__file__).resolve().parents[2]
PACK_DIR = ROOT / "packs" / "dms"


def _semantic_layer() -> dict:
    with (PACK_DIR / "semantic_layer.yaml").open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_object_types_match_semantic_layer_tables():
    sem = _semantic_layer()
    objects = {o.id: o for o in registry.load_object_types(PACK_DIR)}
    assert set(objects) == set(sem["tables"]), "object type ids must equal semantic_layer tables"
    for table, spec in sem["tables"].items():
        got = [p.name for p in objects[table].properties]
        assert got == list(spec["columns"]), f"{table}: properties must match columns exactly"


def test_link_types_match_semantic_layer_joins():
    sem = _semantic_layer()
    links = registry.load_link_types(PACK_DIR)
    got = {(l.from_object, l.from_property, l.to_object, l.to_property) for l in links}
    want = {
        (j["from_table"], j["from_column"], j["to_table"], j["to_column"])
        for j in sem["joins"]
    }
    assert got == want, "link types must transcribe semantic_layer joins 1:1"
    assert len(links) == len(sem["joins"]), "no duplicate/missing link types"


def test_sensitive_columns_fold_into_agent_visible():
    """agent_visible:false <=> sensitive_columns, both directions (no drift)."""
    sem = _semantic_layer()
    sensitive = set(sem["sensitive_columns"])
    hidden = {
        p.name
        for o in registry.load_object_types(PACK_DIR)
        for p in o.properties
        if not p.agent_visible
    }
    assert hidden == sensitive


def test_primary_keys_are_declared_properties():
    for o in registry.load_object_types(PACK_DIR):
        names = {p.name for p in o.properties}
        assert o.primary_key in names, f"{o.id}: primary_key {o.primary_key!r} not a property"


def test_tool_action_types_drive_f8_allowlist():
    """O3: the F8 runner allowlist is DERIVED from these kind:tool entries."""
    from netie.execution.tool_runner import allowed_action_tools

    tools = {a.id for a in registry.load_action_types(PACK_DIR) if a.kind == "tool"}
    assert tools == set(allowed_action_tools())


def test_event_action_types_are_registered_literals():
    actions = {a.id: a for a in registry.load_action_types(PACK_DIR)}
    # ids are unique by construction of the dict; check count for silent dupes
    assert len(actions) == len(registry.load_action_types(PACK_DIR))
    for a in actions.values():
        if a.kind == "event":
            assert a.id == a.ledger_event_type, f"{a.id}: event id must equal its ledger literal"
    # spot-check known literals from packs/ so renames get caught here
    for literal in (
        "item.intake",
        "item.moved",
        "agent.published",
        "stream.flushed",
        "ingest.loaded",
        "pipeline.completed",
        "lakehouse.rows_appended",
        "brain.invoked",
        "action.tool_call",
        "action.tool_call_denied",
    ):
        assert literal in actions, f"ledger literal {literal!r} missing from action_types.yaml"


def test_export_pptx_requires_confirm():
    actions = {a.id: a for a in registry.load_action_types(PACK_DIR)}
    tool = actions["export_pptx"]
    assert tool.kind == "tool"
    assert tool.requires_confirm is True
    assert tool.required_role == "steward"
    assert tool.ledger_event_type == "action.tool_call"


def test_functions_resolve_to_importable_callables():
    for fn in registry.load_functions(PACK_DIR):
        mod = importlib.import_module(fn.module)
        target = getattr(mod, fn.callable, None)
        assert callable(target), f"{fn.id}: {fn.module}:{fn.callable} does not resolve"


def _dump(db: Path) -> dict[str, list[tuple]]:
    conn = sqlite3.connect(db)
    try:
        tables = (
            "ontology_object_types",
            "ontology_properties",
            "ontology_link_types",
            "ontology_action_types",
            "ontology_functions",
        )
        return {
            t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall())  # noqa: S608 — fixed names
            for t in tables
        }
    finally:
        conn.close()


def test_compile_to_sqlite_idempotent(tmp_path):
    db = tmp_path / "dms_ops.db"
    counts = registry.compile_to_sqlite(PACK_DIR, db)
    assert counts["object_types"] == len(registry.load_object_types(PACK_DIR))
    assert counts["link_types"] == len(registry.load_link_types(PACK_DIR))
    assert counts["action_types"] == len(registry.load_action_types(PACK_DIR))
    assert counts["functions"] == len(registry.load_functions(PACK_DIR))
    assert counts["properties"] == sum(
        len(o.properties) for o in registry.load_object_types(PACK_DIR)
    )
    first = _dump(db)
    counts2 = registry.compile_to_sqlite(PACK_DIR, db)
    assert counts2 == counts
    assert _dump(db) == first, "re-compile must be a no-op, not append"


def test_compile_coexists_with_ledger(tmp_path, monkeypatch):
    """Registry tables live in the SAME ops DB as the F1 ledger, and neither breaks the other."""
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    from packs.dms.audit import ledger

    ledger.append("test", "item.intake", {"sku": "TEST-1"}, db_path=db)
    registry.compile_to_sqlite(PACK_DIR, db)
    assert ledger.verify(db_path=db).ok, "ontology compile must not disturb the hash chain"
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT count(*) FROM ontology_action_types WHERE id = 'item.intake'"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 1
