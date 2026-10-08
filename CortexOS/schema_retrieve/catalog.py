"""Build a :class:`SchemaCatalog` from pack documents.

Sources: ``semantic_layer.yaml`` (tables, columns, joins, glossary, sensitive
columns), ``semantic/metrics.yaml`` (metric synonyms -> the tables their SQL
reads) and ``ontology/*.yaml`` (descriptions, primary keys, ``agent_visible``,
links). Pack files are read as data by path; nothing under ``packs`` is imported.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from CortexOS.schema_retrieve.core import ColumnDoc, JoinEdge, SchemaCatalog, TableDoc, Term

_FROM = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_.]*)", re.I)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tables_in_sql(sql: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for m in _FROM.findall(sql or "")))


def catalog_from_documents(
    *,
    semantic_layer: Mapping[str, Any] | None = None,
    metrics: Mapping[str, Any] | None = None,
    object_types: Sequence[Mapping[str, Any]] = (),
    link_types: Sequence[Mapping[str, Any]] = (),
    source: str = "catalog",
    pack_id: str | None = None,
) -> SchemaCatalog:
    layer = semantic_layer or {}
    sensitive = {str(c).lower() for c in layer.get("sensitive_columns") or []}
    cols: dict[str, dict[str, bool]] = {}
    descriptions: dict[str, str] = {}
    pks: dict[str, str] = {}

    for name, spec in (layer.get("tables") or {}).items():
        key = str(name).lower()
        cols.setdefault(key, {})
        for col in (spec or {}).get("columns") or []:
            cols[key][str(col)] = str(col).lower() not in sensitive
    for obj in object_types:
        key = str(obj.get("id") or "").lower()
        if not key:
            continue
        cols.setdefault(key, {})
        descriptions[key] = str(obj.get("description") or "")
        pks[key] = str(obj.get("primary_key") or "")
        for prop in obj.get("properties") or []:
            name = str(prop.get("name") or "")
            if not name:
                continue
            visible = bool(prop.get("agent_visible", True)) and name.lower() not in sensitive
            cols[key][name] = cols[key].get(name, True) and visible

    tables = {
        key: TableDoc(
            key=key,
            columns=tuple(ColumnDoc(n, visible=v) for n, v in columns.items()),
            description=descriptions.get(key, ""),
            primary_key=pks.get(key, ""),
            source=source,
        )
        for key, columns in cols.items()
    }

    joins: list[JoinEdge] = []
    for j in layer.get("joins") or []:
        joins.append(
            JoinEdge(
                str(j["from_table"]).lower(),
                str(j["from_column"]),
                str(j["to_table"]).lower(),
                str(j["to_column"]),
            )
        )
    for link in link_types:
        joins.append(
            JoinEdge(
                str(link["from_object"]).lower(),
                str(link["from_property"]),
                str(link["to_object"]).lower(),
                str(link["to_property"]),
            )
        )

    terms: list[Term] = []
    for g in layer.get("glossary") or []:
        phrase = str(g.get("term") or "").strip()
        if not phrase:
            continue
        table = str(g.get("table") or "").lower()
        column = str(g.get("maps_to") or "")
        if table:
            terms.append(Term(phrase, table, column if column in cols.get(table, {}) else ""))
            continue
        idents = {i for i in _IDENT.findall(str(g.get("definition") or "")) if "_" in i}
        for key in sorted(cols):
            if idents and idents <= set(cols[key]):
                terms.append(Term(phrase, key, sorted(idents)[0]))
    for metric in (metrics or {}).get("metrics") or []:
        metric_id = str(metric.get("id") or "")
        phrases = [metric_id.replace("_", " "), *[str(s) for s in metric.get("synonyms") or []]]
        for table in _tables_in_sql(str(metric.get("sql") or "")):
            if table not in cols:
                continue
            for phrase in dict.fromkeys(p for p in phrases if p.strip()):
                terms.append(Term(phrase, table, "", origin=f"metric {metric_id}"))

    return SchemaCatalog(
        tables=tables,
        joins=tuple(dict.fromkeys(joins)),
        terms=tuple(dict.fromkeys(terms)),
        source=source,
        pack_id=pack_id,
    )


_PACK_FILES = (
    "semantic_layer.yaml",
    "semantic/metrics.yaml",
    "ontology/object_types.yaml",
    "ontology/link_types.yaml",
)


def _mtimes(pack_dir: Path) -> tuple[int, ...]:
    out = []
    for rel in _PACK_FILES:
        try:
            out.append((pack_dir / rel).stat().st_mtime_ns)
        except OSError:
            out.append(0)
    return tuple(out)


def _yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=8)
def _load(pack_dir: str, mtimes: tuple[int, ...]) -> SchemaCatalog:
    del mtimes  # cache key only
    root = Path(pack_dir)
    return catalog_from_documents(
        semantic_layer=_yaml(root / "semantic_layer.yaml"),
        metrics=_yaml(root / "semantic" / "metrics.yaml"),
        object_types=_yaml(root / "ontology" / "object_types.yaml").get("object_types") or [],
        link_types=_yaml(root / "ontology" / "link_types.yaml").get("link_types") or [],
        source=f"pack:{root.name}",
    )


def load_pack_catalog(pack_dir: Path | str) -> SchemaCatalog:
    """Catalog for one pack directory, reloaded when any source file changes."""
    root = Path(pack_dir)
    return _load(str(root), _mtimes(root))
