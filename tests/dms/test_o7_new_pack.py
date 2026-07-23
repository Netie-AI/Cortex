"""O7 — new-pack generator (the FDE payoff).

Proves an FDE-handed ontology trio becomes a runnable pack: generated files
derive from the trio without drift, the DDL applies, the engine SDK serves the
new pack pack-agnostically (PII stripped), and audit reuses the F1 ledger — all
without a line of pack-specific engine code.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import new_pack

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def crm_pack(tmp_path):
    """Scaffold the CRM demo into a temp packs root (never touches the repo)."""
    root = tmp_path / "packs"
    return new_pack.scaffold_demo("crm", packs_root=root), root


def test_scaffold_creates_full_pack_shape(crm_pack):
    pack, _ = crm_pack
    for rel in [
        "ontology/object_types.yaml",
        "ontology/link_types.yaml",
        "ontology/action_types.yaml",
        "ontology/functions.yaml",
        "sql/001_crm_v0.sql",
        "semantic_layer.yaml",
        "compliance/crm_rules_v1.yaml",
        "audit/__init__.py",
        "__init__.py",
    ]:
        assert (pack / rel).is_file(), f"missing {rel}"


def test_generated_files_derive_from_the_trio_without_drift(crm_pack):
    from CortexOS.ontology.registry import load_link_types, load_object_types

    pack, _ = crm_pack
    objects = load_object_types(pack)
    ids = {o.id for o in objects}
    assert ids == {"account", "contact", "opportunity"}

    # semantic_layer tables/joins/sensitive_columns match the ontology exactly
    import yaml

    sem = yaml.safe_load((pack / "semantic_layer.yaml").read_text(encoding="utf-8"))
    assert set(sem["tables"]) == ids
    hidden = {p.name for o in objects for p in o.properties if not p.agent_visible}
    assert set(sem["sensitive_columns"]) == hidden == {"owner_email", "email", "phone"}
    assert len(sem["joins"]) == len(load_link_types(pack)) == 2


def test_generated_ddl_applies(crm_pack):
    pack, _ = crm_pack
    ddl = (pack / "sql" / "001_crm_v0.sql").read_text(encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl)  # must be valid SQL
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"account", "contact", "opportunity"} <= tables


def test_engine_sdk_serves_generated_pack_pack_agnostically(crm_pack):
    from CortexOS.agent_sdk import list_object_types, list_action_types

    pack, _ = crm_pack
    objs = {o.id: o for o in list_object_types(pack_dir=pack)}
    # PII (owner_email/email/phone) stripped from the agent-facing schema
    assert "owner_email" not in {p.name for p in objs["account"].properties}
    assert {p.name for p in objs["contact"].properties}.isdisjoint({"email", "phone"})
    # the demo tool + events are discoverable from the pack's own registry
    tools = {a.id for a in list_action_types(kind="tool", pack_dir=pack)}
    assert tools == {"crm.export"}


def test_audit_reuses_the_shared_ledger(crm_pack, tmp_path, monkeypatch):
    """The generated audit/__init__.py re-exports the F1 ledger; a write chains."""
    pack, _ = crm_pack
    db = tmp_path / "crm_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)

    # load the generated module by path (it isn't on sys.path under packs.crm)
    import importlib.util

    spec = importlib.util.spec_from_file_location("crm_audit_gen", pack / "audit" / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.append("crm_user", "account.created", {"account_id": "A-1"}, db_path=db)
    assert mod.verify(db_path=db).ok


def test_name_validation_and_overwrite(tmp_path):
    from scripts.new_pack import PackExistsError

    root = tmp_path / "packs"
    # an invalid (non lower_snake) name is refused
    with pytest.raises(ValueError):
        new_pack.scaffold_pack(
            "BadName",
            object_types_yaml="object_types: []",
            link_types_yaml="link_types: []",
            action_types_yaml="action_types: []",
            packs_root=root,
        )
    new_pack.scaffold_demo("crm", packs_root=root)  # first succeeds
    with pytest.raises(PackExistsError):  # second without overwrite is refused
        new_pack.scaffold_demo("crm", packs_root=root)
    new_pack.scaffold_demo("crm", packs_root=root, overwrite=True)  # overwrite works


def test_cli_demo_smoke(tmp_path):
    root = tmp_path / "packs"
    res = subprocess.run(
        [sys.executable, "-m", "scripts.new_pack", "--demo", "crm", "--packs-root", str(root)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert (root / "crm" / "sql" / "001_crm_v0.sql").is_file()


def test_committed_crm_demo_pack_loads():
    """The packs/crm/ scaffold shipped in the repo stays loadable (regression)."""
    from CortexOS.agent_sdk import list_object_types

    crm = ROOT / "packs" / "crm"
    if not crm.exists():
        pytest.skip("packs/crm not present in this checkout")
    ids = {o.id for o in list_object_types(pack_dir=crm)}
    assert ids == {"account", "contact", "opportunity"}
