"""O2 codebase knowledge map — static ast build + query CLI, deterministic.

Builds the codebase ontology into a tmp DB and asserts known ground truths:
a real module resolves, its tests are found by import, gate tokens map correctly,
and the build is idempotent + resilient to unparseable files.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from packs.dms.ontology import query
from scripts import build_codebase_ontology as builder

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    out = tmp_path_factory.mktemp("codebase_ontology") / "codebase_ontology.db"
    counts = builder.build(out)
    return out, counts


def test_build_counts_are_sane(db):
    _, counts = db
    assert counts["modules"] > 100, "expected the repo to have many modules"
    assert counts["functions"] > 100
    assert counts["gate_links"] > 0


def test_known_module_is_indexed(db):
    out, _ = db
    info = query.module_info("packs.dms.audit.ledger", db_path=out)
    assert info, "packs.dms.audit.ledger must be indexed"
    assert info["path"] == "packs/dms/audit/ledger.py"
    names = {f["name"] for f in info["functions"]}
    assert {"append", "verify", "default_db_path"} <= names


def test_covers_finds_ledger_tests_by_import(db):
    out, _ = db
    tests = query.covers("packs.dms.audit.ledger", db_path=out)
    # test_f1_ledger.py imports the ledger; the new O1 test imports it too.
    assert any(t.endswith("test_f1_ledger") for t in tests), tests
    assert any("ontology_registry" in t for t in tests), tests


def test_path_or_module_arg_equivalent(db):
    out, _ = db
    by_path = query.covers(query._path_to_module("packs/dms/audit/ledger.py"), db_path=out)
    by_mod = query.covers("packs.dms.audit.ledger", db_path=out)
    assert by_path == by_mod


def test_gate_lookup_maps_test_names(db):
    out, _ = db
    f7 = query.gate("F7", db_path=out)
    assert any("f7" in t for t in f7["tests"]), f7["tests"]
    assert f7["modules"], "F7 tests should cover some source modules"
    # this very test file carries the o2 token, so the gate map must find it
    o2 = query.gate("O2", db_path=out)
    assert any("o2_codebase_map" in t for t in o2["tests"]), o2["tests"]


def test_gate_token_extraction():
    assert builder.gate_tokens("test_f7_rbac") == {"F7"}
    assert builder.gate_tokens("test_b3_f7_remainder") == {"B3", "F7"}
    assert builder.gate_tokens("test_l0_lakehouse") == {"L0"}
    assert builder.gate_tokens("test_generative") == set()


def test_build_is_idempotent(db):
    out, counts = db
    counts2 = builder.build(out)
    assert counts2 == counts


def test_unparseable_file_is_recorded_not_fatal(tmp_path, monkeypatch):
    """A syntactically broken file (e.g. a parallel in-progress edit) must not crash the build."""
    fake_root = tmp_path
    pkg = fake_root / "packs"
    pkg.mkdir()
    (pkg / "good.py").write_text('"""Good module."""\ndef f():\n    return 1\n', encoding="utf-8")
    (pkg / "broken.py").write_text("def oops(:\n", encoding="utf-8")  # syntax error
    monkeypatch.setattr(builder, "ROOT", fake_root)
    monkeypatch.setattr(builder, "SOURCE_DIRS", ("packs",))
    out = fake_root / "cb.db"
    counts = builder.build(out)
    assert counts["unparseable"] == 1
    conn = sqlite3.connect(out)
    try:
        broken = conn.execute("SELECT parse_ok FROM code_module WHERE module = 'packs.broken'").fetchone()
        good = conn.execute("SELECT parse_ok FROM code_module WHERE module = 'packs.good'").fetchone()
    finally:
        conn.close()
    assert broken == (0,)
    assert good == (1,)
