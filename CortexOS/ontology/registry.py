"""Ontology registry loader + SQLite compiler — engine-canonical, pack-agnostic.

Source of truth is per-pack YAML (``packs/<pack>/ontology/*.yaml``), same
precedent as ``semantic_layer.yaml``. The compiler writes ``ontology_*`` tables
into the SAME ops DB the F1 ledger lives in so registry rows are joinable
against ledger events. Plain functions + stdlib sqlite3, same style as
``packs/dms/audit/ledger.py`` (which is the shared, pack-agnostic ledger spine).

Pack resolution: every loader takes an explicit ``pack_dir``; when omitted, the
active pack comes from the ``PACK`` env var (default ``dms``), resolved to
``<repo>/packs/<pack>``. One engine, N packs — metadata is data, not code
(docs/ontology/PALANTIR_AIP_RESEARCH.md §7).
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def default_pack() -> str:
    return (os.environ.get("PACK") or "dms").strip() or "dms"


def pack_dir_for(pack: str | None = None) -> Path:
    return ROOT / "packs" / (pack or default_pack())


@dataclass(frozen=True, slots=True)
class Property:
    name: str
    type: str
    agent_visible: bool


@dataclass(frozen=True, slots=True)
class ObjectType:
    id: str
    description: str
    primary_key: str
    properties: tuple[Property, ...]


@dataclass(frozen=True, slots=True)
class LinkType:
    id: str
    from_object: str
    from_property: str
    to_object: str
    to_property: str
    cardinality: str


@dataclass(frozen=True, slots=True)
class ActionType:
    id: str
    kind: str  # "tool" = invocable governed action; "event" = registered ledger event
    description: str
    ledger_event_type: str
    object_type: str | None = None
    required_role: str | None = None
    requires_confirm: bool = False
    params: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FunctionDef:
    id: str
    description: str
    module: str
    callable: str


def _ontology_dir(pack_dir: Path | str | None = None) -> Path:
    base = Path(pack_dir) if pack_dir is not None else pack_dir_for()
    return base / "ontology"


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    return data


def load_object_types(pack_dir: Path | str | None = None) -> list[ObjectType]:
    data = _read_yaml(_ontology_dir(pack_dir) / "object_types.yaml")
    out: list[ObjectType] = []
    for row in data.get("object_types") or []:
        props = tuple(
            Property(
                name=str(p["name"]),
                type=str(p.get("type", "string")),
                agent_visible=bool(p.get("agent_visible", True)),
            )
            for p in row.get("properties") or []
        )
        out.append(
            ObjectType(
                id=str(row["id"]),
                description=str(row.get("description", "")),
                primary_key=str(row["primary_key"]),
                properties=props,
            )
        )
    return out


def load_link_types(pack_dir: Path | str | None = None) -> list[LinkType]:
    data = _read_yaml(_ontology_dir(pack_dir) / "link_types.yaml")
    return [
        LinkType(
            id=str(row["id"]),
            from_object=str(row["from_object"]),
            from_property=str(row["from_property"]),
            to_object=str(row["to_object"]),
            to_property=str(row["to_property"]),
            cardinality=str(row.get("cardinality", "many_to_one")),
        )
        for row in data.get("link_types") or []
    ]


def load_action_types(pack_dir: Path | str | None = None) -> list[ActionType]:
    data = _read_yaml(_ontology_dir(pack_dir) / "action_types.yaml")
    out: list[ActionType] = []
    for row in data.get("action_types") or []:
        out.append(
            ActionType(
                id=str(row["id"]),
                kind=str(row.get("kind", "event")),
                description=str(row.get("description", "")),
                ledger_event_type=str(row["ledger_event_type"]),
                object_type=row.get("object_type"),
                required_role=row.get("required_role"),
                requires_confirm=bool(row.get("requires_confirm", False)),
                params=tuple(str(p) for p in row.get("params") or []),
            )
        )
    return out


def load_functions(pack_dir: Path | str | None = None) -> list[FunctionDef]:
    data = _read_yaml(_ontology_dir(pack_dir) / "functions.yaml")
    return [
        FunctionDef(
            id=str(row["id"]),
            description=str(row.get("description", "")),
            module=str(row["module"]),
            callable=str(row["callable"]),
        )
        for row in data.get("functions") or []
    ]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ontology_object_types (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    primary_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ontology_properties (
    object_type_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'string',
    agent_visible INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (object_type_id, name)
);
CREATE TABLE IF NOT EXISTS ontology_link_types (
    id TEXT PRIMARY KEY,
    from_object TEXT NOT NULL,
    from_property TEXT NOT NULL,
    to_object TEXT NOT NULL,
    to_property TEXT NOT NULL,
    cardinality TEXT NOT NULL DEFAULT 'many_to_one'
);
CREATE TABLE IF NOT EXISTS ontology_action_types (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'event',
    description TEXT NOT NULL DEFAULT '',
    ledger_event_type TEXT NOT NULL,
    object_type TEXT,
    required_role TEXT,
    requires_confirm INTEGER NOT NULL DEFAULT 0,
    params TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS ontology_functions (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    module TEXT NOT NULL,
    callable TEXT NOT NULL
);
"""


def compile_to_sqlite(
    pack_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> dict[str, int]:
    """Compile the pack's ontology YAML into ontology_* tables. Idempotent.

    Full replace per run (delete + insert in one transaction) so re-compiling
    on pack load never leaves stale rows. Returns row counts per table.
    """
    from packs.dms.audit.ledger import default_db_path  # shared pack-agnostic ops-DB path

    objects = load_object_types(pack_dir)
    links = load_link_types(pack_dir)
    actions = load_action_types(pack_dir)
    functions = load_functions(pack_dir)

    path = Path(db_path) if db_path is not None else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        with conn:
            conn.execute("DELETE FROM ontology_object_types")
            conn.execute("DELETE FROM ontology_properties")
            conn.execute("DELETE FROM ontology_link_types")
            conn.execute("DELETE FROM ontology_action_types")
            conn.execute("DELETE FROM ontology_functions")
            conn.executemany(
                "INSERT INTO ontology_object_types (id, description, primary_key) VALUES (?, ?, ?)",
                [(o.id, o.description, o.primary_key) for o in objects],
            )
            conn.executemany(
                "INSERT INTO ontology_properties "
                "(object_type_id, name, type, agent_visible, position) VALUES (?, ?, ?, ?, ?)",
                [
                    (o.id, p.name, p.type, int(p.agent_visible), i)
                    for o in objects
                    for i, p in enumerate(o.properties)
                ],
            )
            conn.executemany(
                "INSERT INTO ontology_link_types "
                "(id, from_object, from_property, to_object, to_property, cardinality) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        link.id,
                        link.from_object,
                        link.from_property,
                        link.to_object,
                        link.to_property,
                        link.cardinality,
                    )
                    for link in links
                ],
            )
            conn.executemany(
                "INSERT INTO ontology_action_types "
                "(id, kind, description, ledger_event_type, object_type, required_role, "
                "requires_confirm, params) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        a.id,
                        a.kind,
                        a.description,
                        a.ledger_event_type,
                        a.object_type,
                        a.required_role,
                        int(a.requires_confirm),
                        json.dumps(list(a.params)),
                    )
                    for a in actions
                ],
            )
            conn.executemany(
                "INSERT INTO ontology_functions (id, description, module, callable) "
                "VALUES (?, ?, ?, ?)",
                [(f.id, f.description, f.module, f.callable) for f in functions],
            )
    finally:
        conn.close()
    return {
        "object_types": len(objects),
        "properties": sum(len(o.properties) for o in objects),
        "link_types": len(links),
        "action_types": len(actions),
        "functions": len(functions),
    }
