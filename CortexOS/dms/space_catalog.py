"""Chooser context from the asking Space's manifest catalog.

``CORTEX_SPACE_CATALOG`` (and the DMS producer flag ``DMS_SPACE_CATALOG``)
default off. Off, nothing here is bound and the demo catalog stays the
chooser's context. On, a manifest is the chooser's context only when
``space_catalog_sig`` verifies under the same issuer key that signed the
manifest. A missing catalog, or a signature that does not verify, keeps
the demo catalog (the flag-off path). The catalog is not part of
``canonical_manifest_bytes``. Before a verified catalog is bound, every
table it names is intersected with the signed ``row_predicates`` keys.
The SQL gate's tables and columns come from that grant and the warehouse
schema, not from catalog names or keys.

A Space with no approved measures does not reconfirm from the demo catalog.
The ask returns ``space_catalog_handoff`` ``generative`` so DMS runs its generative path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any

ENABLED_ENV = "CORTEX_SPACE_CATALOG"
PRODUCER_ENV = "DMS_SPACE_CATALOG"

# The model named something that is not an object or measure of this catalog.
# A code, not a rewritten sentence.
CLARIFY_OUTSIDE_CATALOG = "outside_catalog"

_TOKEN = re.compile(r"[a-z0-9_]+")


def space_catalog_enabled() -> bool:
    """True only when a caller set the switch to ``1``. Default off."""
    for name in (ENABLED_ENV, PRODUCER_ENV):
        if (os.environ.get(name) or "").strip() == "1":
            return True
    return False


@dataclass(frozen=True, slots=True)
class CatalogObject:
    name: str
    relation: str
    key: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogLink:
    name: str
    from_object: str
    from_columns: tuple[str, ...]
    to_object: str
    to_columns: tuple[str, ...]
    cardinality: str


@dataclass(frozen=True, slots=True)
class CatalogMeasure:
    name: str
    description: str
    grain: str
    expression: str


@dataclass(frozen=True, slots=True)
class CatalogDefinition:
    name: str
    grain: str
    text: str


@dataclass(frozen=True, slots=True)
class BoundCatalog:
    """One Space's approved ontology. Never the demo pack."""

    objects: tuple[CatalogObject, ...]
    links: tuple[CatalogLink, ...]
    measures: tuple[CatalogMeasure, ...]
    definitions: tuple[CatalogDefinition, ...]
    granted_tables: tuple[str, ...]
    # Bare names the signature granted. Empty means this catalog was not clipped.
    signed: frozenset[str] = frozenset()
    # Column names of those tables in the warehouse. Empty means the catalog
    # could not be read; the gate then uses a sentinel, not a catalog string.
    schema_columns: tuple[str, ...] = ()

    def measure_ids(self) -> set[str]:
        return {item.name for item in self.measures if item.name}

    def measure(self, metric_id: str) -> CatalogMeasure | None:
        for item in self.measures:
            if item.name == metric_id:
                return item
        return None

    def definition(self, metric_id: str) -> CatalogDefinition | None:
        for item in self.definitions:
            if item.name == metric_id:
                return item
        return None

    def definition_sql(self, metric_id: str) -> str:
        measure = self.measure(metric_id)
        if measure is not None and measure.expression.strip():
            return measure.expression.strip()
        item = self.definition(metric_id)
        return (item.text or "").strip() if item is not None else ""

    def entity_names(self) -> frozenset[str]:
        """Object and measure names this catalog declares. Not prose."""
        names: set[str] = set()
        for obj in self.objects:
            for raw in (obj.name, obj.relation):
                if raw:
                    names.add(raw.casefold())
        for measure in self.measures:
            if measure.name:
                names.add(measure.name.casefold())
        for link in self.links:
            for raw in (link.from_object, link.to_object):
                if raw:
                    names.add(raw.casefold())
        for raw in self.granted_tables:
            bare = _bare_table(raw)
            if bare:
                names.add(bare)
        return frozenset(names)


_BOUND: ContextVar[BoundCatalog | None] = ContextVar("cortex_space_catalog", default=None)


def current_space_catalog() -> BoundCatalog | None:
    """The bound Space catalog, or None when the demo catalog is in force."""
    return _BOUND.get()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _view_from_raw(raw: Any) -> BoundCatalog | None:
    objects = tuple(
        CatalogObject(
            name=_text(getattr(item, "name", None)),
            relation=_text(getattr(item, "relation", None)),
            key=_strings(getattr(item, "key", None)),
        )
        for item in (getattr(raw, "objects", None) or [])
        if _text(getattr(item, "name", None))
    )
    links = tuple(
        CatalogLink(
            name=_text(getattr(item, "name", None)),
            from_object=_text(getattr(item, "from_object", None)),
            from_columns=_strings(getattr(item, "from_columns", None)),
            to_object=_text(getattr(item, "to_object", None)),
            to_columns=_strings(getattr(item, "to_columns", None)),
            cardinality=_text(getattr(item, "cardinality", None)),
        )
        for item in (getattr(raw, "links", None) or [])
        if _text(getattr(item, "name", None))
    )
    measures = tuple(
        CatalogMeasure(
            name=_text(getattr(item, "name", None)),
            description=_text(getattr(item, "description", None)),
            grain=_text(getattr(item, "grain", None)),
            expression=_text(getattr(item, "expression", None)),
        )
        for item in (getattr(raw, "measures", None) or [])
        if _text(getattr(item, "name", None))
    )
    definitions = tuple(
        CatalogDefinition(
            name=_text(getattr(item, "name", None)),
            grain=_text(getattr(item, "grain", None)),
            text=_text(getattr(item, "text", None)),
        )
        for item in (getattr(raw, "definitions", None) or [])
        if _text(getattr(item, "name", None))
    )
    granted = _strings(getattr(raw, "granted_tables", None))
    return BoundCatalog(
        objects=objects,
        links=links,
        measures=measures,
        definitions=definitions,
        granted_tables=granted,
    )


def _bare_table(name: str) -> str:
    return str(name or "").split(".")[-1].strip().lower()


def _signed_tables(manifest: Any) -> set[str]:
    """Bare table names the signature grants. The catalog cannot add to this."""
    raw = getattr(manifest, "row_predicates", None) or {}
    if not isinstance(raw, dict):
        return set()
    return {bare for key in raw if (bare := _bare_table(str(key)))}


def _sql_tables(sql: str) -> set[str] | None:
    """Tables in ``sql``, or None when non-empty text does not parse.

    Empty text has no tables. A parse failure is not "no tables": the caller
    drops the measure or definition instead of showing the raw text.
    """
    text = (sql or "").strip()
    if not text:
        return set()
    import sqlglot
    from sqlglot.errors import SqlglotError

    try:
        tree = sqlglot.parse_one(text, read="duckdb")
    except SqlglotError:
        return None
    # Only a SELECT is runnable here. A parsed alias or fragment is not
    # "no tables"; showing it would keep an unsigned name.
    if not isinstance(tree, sqlglot.exp.Select):
        return None
    names: set[str] = set()
    for table in tree.find_all(sqlglot.exp.Table):
        bare = _bare_table(table.name or "")
        if bare:
            names.add(bare)
    return names


def _object_is_signed(obj: CatalogObject, signed: set[str]) -> bool:
    """Keep an object only when every name it carries is a signed table.

    ``name`` and ``relation`` are both table pointers. One signed side does
    not admit the other.
    """
    pointers = [bare for bare in (_bare_table(obj.name), _bare_table(obj.relation)) if bare]
    return bool(pointers) and all(bare in signed for bare in pointers)


def _end_is_signed(end: str, signed: set[str]) -> bool:
    """A link end or a grain is signed only when that bare name was granted."""
    return _bare_table(end) in signed


def _clip_to_signed(catalog: BoundCatalog, signed: set[str]) -> BoundCatalog:
    """Drop every catalog part that names a table the signature did not grant.

    The result stays a bound catalog even when nothing remains. Falling
    through to the demo catalog would be a wider leak than an empty Space.
    """
    granted = tuple(name for name in catalog.granted_tables if _bare_table(name) in signed)
    objects = tuple(obj for obj in catalog.objects if _object_is_signed(obj, signed))
    links = tuple(
        link
        for link in catalog.links
        if _end_is_signed(link.from_object, signed) and _end_is_signed(link.to_object, signed)
    )
    measures_kept: list[CatalogMeasure] = []
    for measure in catalog.measures:
        if measure.grain and not _end_is_signed(measure.grain, signed):
            continue
        tables = _sql_tables(measure.expression)
        if tables is None or not tables <= signed:
            continue
        measures_kept.append(measure)
    measures = tuple(measures_kept)
    kept_measures = {measure.name for measure in measures}
    definitions_kept: list[CatalogDefinition] = []
    for item in catalog.definitions:
        if item.name not in kept_measures:
            continue
        if item.grain and not _end_is_signed(item.grain, signed):
            continue
        tables = _sql_tables(item.text)
        if tables is None or not tables <= signed:
            continue
        definitions_kept.append(item)
    return BoundCatalog(
        objects=objects,
        links=links,
        measures=measures,
        definitions=tuple(definitions_kept),
        granted_tables=granted,
        signed=frozenset(signed),
    )


def _view_from_verified(verified: Any) -> BoundCatalog | None:
    """Bind a catalog only when its signature just verified.

    Anything else is the flag-off path: no catalog, so the demo layer stays
    in force. The signature check uses the issuer key the manifest verifier
    already resolved. A missing key is not a second chance to trust the JSON.
    """
    if not space_catalog_enabled() or verified is None:
        return None
    manifest = getattr(verified, "manifest", None)
    public_key = getattr(verified, "issuer_public_key", None)
    if manifest is None or getattr(manifest, "space_catalog", None) is None or public_key is None:
        return None
    from CortexOS.execution.manifest import catalog_signature_status

    trusted, _refusal = catalog_signature_status(manifest, public_key)
    if not trusted:
        return None
    view = _view_from_raw(manifest.space_catalog)
    if view is None:
        return None
    from CortexOS.execution.submit import granted_column_names

    clipped = _clip_to_signed(view, _signed_tables(manifest))
    return replace(
        clipped,
        schema_columns=tuple(sorted(granted_column_names(verified))),
    )


@contextmanager
def open_space_catalog(verified: Any) -> Iterator[None]:
    """Bind the catalog for one ask. Flag off, or no trusted catalog, binds nothing."""
    token: Token[BoundCatalog | None] = _BOUND.set(_view_from_verified(verified))
    try:
        yield
    finally:
        _BOUND.reset(token)


def chooser_blocks(catalog: BoundCatalog) -> tuple[str, str]:
    """Subject block and measure block. Names come only from ``catalog``."""
    subjects = ["Tables:"]
    for name in catalog.granted_tables:
        subjects.append(f"- {name}")
    subjects.append("Objects:")
    for obj in catalog.objects:
        key = ", ".join(obj.key) if obj.key else "-"
        relation = obj.relation or obj.name
        subjects.append(f"- {obj.name} | relation: {relation} | key: {key}")
    subjects.append("Links:")
    for link in catalog.links:
        subjects.append(
            f"- {link.name} | {link.from_object} -> {link.to_object} | {link.cardinality or '-'}"
        )
    metrics: list[str] = []
    for measure in catalog.measures:
        definition = catalog.definition(measure.name)
        text = definition.text if definition is not None and definition.text else measure.description
        sql = " ".join(measure.expression.split()) if measure.expression else ""
        if len(sql) > 240:
            sql = sql[:240] + "..."
        metrics.append(
            f"- {measure.name} | grain: {measure.grain or '-'} | "
            f"definition: {text or '-'} | expression: {sql or '-'}"
        )
    return "\n".join(subjects), "\n".join(metrics)


def gate_semantic() -> dict[str, Any] | None:
    """Table allowlist for the SQL gate, or None when the demo layer applies.

    Tables are the signed grant. Columns are the warehouse columns of those
    tables. A catalog key, alias, or name never becomes a column. When the
    warehouse has no columns for the grant, a sentinel keeps the gate from
    falling through to the demo column list.
    """
    catalog = current_space_catalog()
    if catalog is None:
        return None
    columns = list(catalog.schema_columns) or ["_space_catalog"]
    tables = {
        bare: {"columns": list(columns), "row_key": []}
        for bare in sorted(catalog.signed)
    }
    return {"tables": tables, "glossary": [], "sensitive_columns": []}


def structural_why(catalog: BoundCatalog) -> str:
    """A reason built from this catalog's object and measure names."""
    names: list[str] = []
    for obj in catalog.objects:
        if obj.name and obj.name.casefold() not in {item.casefold() for item in names}:
            names.append(obj.name)
    for measure in catalog.measures:
        if measure.name and measure.name.casefold() not in {item.casefold() for item in names}:
            names.append(measure.name)
    return ", ".join(names)


def _names_outside(text: str, catalog: BoundCatalog) -> bool:
    """True when ``text`` contains an identifier that is not a catalog entity.

    Membership in the catalog's entity set. Not an allowlist of English words.
    """
    allowed = catalog.entity_names()
    for token in _TOKEN.findall((text or "").casefold()):
        if token not in allowed:
            return True
    return False


def confine_why(text: str, catalog: BoundCatalog, question: str) -> str:
    """The customer reason: catalog entities, or a typed clarify.

    ``question`` is not an allowlist. The model was given only this catalog.
    A reason that still names something outside it is not rewritten; the
    caller gets ``CLARIFY_OUTSIDE_CATALOG``.
    """
    del question
    cleaned = " ".join(str(text or "").split())
    built = structural_why(catalog)
    if cleaned == built:
        return built
    if cleaned and _names_outside(cleaned, catalog):
        return CLARIFY_OUTSIDE_CATALOG
    return built


def confine_closest(text: str, catalog: BoundCatalog, question: str) -> str:
    from CortexOS.dms.metric_choice import NO_CLOSEST, is_no_closest

    del question
    cleaned = " ".join(str(text or "").split())
    if not cleaned or is_no_closest(cleaned) or _names_outside(cleaned, catalog):
        return NO_CLOSEST
    return cleaned


def listing_text(catalog: BoundCatalog) -> str:
    """What this Space can answer. No demo table or measure is named."""
    tables = ", ".join(name for name in catalog.granted_tables if name) or "none"
    labels = [measure.description or measure.name for measure in catalog.measures]
    measures = ", ".join(label for label in labels if label) or "none"
    return f"This Space can answer from {tables}. Approved measures: {measures}."
