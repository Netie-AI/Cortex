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
class DeclaredValue:
    """One canonical value of a property and the source spellings that mean it.

    ``sources`` are the spellings a source system writes (``OUT``); a lake build
    stores ``canonical`` (``outbound``) instead. See ``CortexOS.ontology.value_domain``.
    """

    canonical: str
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Property:
    name: str
    type: str
    agent_visible: bool
    values: tuple[DeclaredValue, ...] = ()


@dataclass(frozen=True, slots=True)
class DeclaredMeasure:
    """A measure an object declares: what the number means, as data.

    ``expression`` is one aggregate over the object's columns; every value of
    the measure counts only rows where ``filter_column = filter_value`` (a
    declared canonical value of that column); ``grain`` is the property that
    identifies one summed row; ``metrics`` names the metric entries that
    compute it. See ``CortexOS.ontology.measures``.
    """

    name: str
    description: str
    expression: str
    filter_column: str
    filter_value: str
    grain: str
    metrics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ObjectType:
    id: str
    description: str
    primary_key: str
    properties: tuple[Property, ...]
    measures: tuple[DeclaredMeasure, ...] = ()


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


def _declared_values(where: str, raw: Any) -> tuple[DeclaredValue, ...]:
    """Parse a property's ``values:`` list. Refuse anything a lake build could read two ways."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{where}: values must be a list")
    out: list[DeclaredValue] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{where}: each value must be a mapping with canonical")
        canonical = item.get("canonical")
        if not isinstance(canonical, str) or not canonical:
            raise ValueError(f"{where}: canonical must be a non-empty string")
        sources = item.get("from", [])
        if not isinstance(sources, list) or not all(
            isinstance(s, str) and s for s in sources
        ):
            raise ValueError(f"{where}: from must be a list of non-empty strings")
        out.append(DeclaredValue(canonical=canonical, sources=tuple(sources)))
    canon = [v.canonical for v in out]
    if len(set(canon)) != len(canon):
        raise ValueError(f"{where}: a canonical value is declared twice")
    seen: set[str] = set()
    for v in out:
        for src in v.sources:
            if src in seen:
                raise ValueError(f"{where}: source spelling {src!r} maps to two values")
            if src in canon:
                raise ValueError(f"{where}: source spelling {src!r} is also a canonical value")
            seen.add(src)
    return tuple(out)


def _declared_measures(
    obj_id: str, props: tuple[Property, ...], raw: Any
) -> tuple[DeclaredMeasure, ...]:
    """Parse an object's ``measures:`` list. Refuse a filter the lake could not match."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{obj_id}: measures must be a list")
    by_name = {p.name: p for p in props}
    out: list[DeclaredMeasure] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{obj_id}: each measure must be a mapping")
        name = item.get("name")
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError(f"{obj_id}: measure name must be an identifier")
        where = f"{obj_id}.{name}"
        expression = item.get("expression")
        if not isinstance(expression, str) or not expression.strip():
            raise ValueError(f"{where}: expression must be a non-empty string")
        flt = item.get("filter")
        if not isinstance(flt, dict) or set(flt) != {"column", "equals"}:
            raise ValueError(f"{where}: filter must be a mapping with column and equals")
        column, value = flt["column"], flt["equals"]
        prop = by_name.get(column) if isinstance(column, str) else None
        if prop is None:
            raise ValueError(f"{where}: filter column {column!r} is not a property")
        canonical = [v.canonical for v in prop.values]
        if not canonical:
            raise ValueError(f"{where}: filter column {column!r} declares no values")
        if value not in canonical:
            raise ValueError(f"{where}: filter value {value!r} is not a declared value of {column}")
        grain = item.get("grain")
        if not isinstance(grain, str) or grain not in by_name:
            raise ValueError(f"{where}: grain {grain!r} is not a property")
        metrics = item.get("metrics", [])
        if not isinstance(metrics, list) or not all(isinstance(m, str) and m for m in metrics):
            raise ValueError(f"{where}: metrics must be a list of metric ids")
        out.append(
            DeclaredMeasure(
                name=name,
                description=str(item.get("description", "")),
                expression=expression.strip(),
                filter_column=column,
                filter_value=value,
                grain=str(grain),
                metrics=tuple(metrics),
            )
        )
    names = [m.name for m in out]
    if len(set(names)) != len(names):
        raise ValueError(f"{obj_id}: a measure is declared twice")
    return tuple(out)


def load_object_types(pack_dir: Path | str | None = None) -> list[ObjectType]:
    data = _read_yaml(_ontology_dir(pack_dir) / "object_types.yaml")
    out: list[ObjectType] = []
    for row in data.get("object_types") or []:
        props = tuple(
            Property(
                name=str(p["name"]),
                type=str(p.get("type", "string")),
                agent_visible=bool(p.get("agent_visible", True)),
                values=_declared_values(f"{row.get('id')}.{p.get('name')}", p.get("values")),
            )
            for p in row.get("properties") or []
        )
        out.append(
            ObjectType(
                id=str(row["id"]),
                description=str(row.get("description", "")),
                primary_key=str(row["primary_key"]),
                properties=props,
                measures=_declared_measures(str(row["id"]), props, row.get("measures")),
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
