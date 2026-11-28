"""Chooser context from the asking Space's manifest catalog.

``CORTEX_SPACE_CATALOG`` (and the DMS producer flag ``DMS_SPACE_CATALOG``)
default off. Off, nothing here is bound and the demo catalog stays the
chooser's context. On, a manifest that carries ``space_catalog`` is the only
context: objects, links, measures, definitions, and granted tables. No
catalog at all keeps the demo catalog, which is how the demo Space is sent.
The catalog is not part of the signed bytes. Before it is bound, every
table it names is intersected with the signed ``row_predicates`` keys.
Anything that points at a table the signature did not grant is dropped,
so the chooser, the SQL gate, and the answer cannot widen the grant.

A Space with no approved measures does not reconfirm from the demo catalog.
The ask returns ``space_catalog_handoff`` ``generative`` so DMS runs its generative path.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

ENABLED_ENV = "CORTEX_SPACE_CATALOG"
PRODUCER_ENV = "DMS_SPACE_CATALOG"

# Words our own sentences use. A token outside this set, the question, and
# the asking catalog is not shown. This does not choose a metric or write SQL.
_TEMPLATE_WORDS = frozenset(
    """
    the so it cannot be served directly not armed metric chooser is none
    approved measure measures this space has answers that question closest
    no of for and was chosen meaning check agreed declared a an to on with
    from are its only when what which how many per all one goes generative
    path close confirmed answerable can pick above rephrase using those
    topics here definition run did accept returned nothing usable does mean
    same thing
    """.split()
)

_TOKEN = re.compile(r"[a-z0-9]+")


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

    def tokens(self) -> set[str]:
        parts: list[str] = []
        for name in self.granted_tables:
            parts.append(name)
        for obj in self.objects:
            parts.extend((obj.name, obj.relation, *obj.key))
        for link in self.links:
            parts.extend(
                (
                    link.name,
                    link.from_object,
                    link.to_object,
                    link.cardinality,
                    *link.from_columns,
                    *link.to_columns,
                )
            )
        for measure in self.measures:
            parts.extend(
                (measure.name, measure.description, measure.grain, measure.expression)
            )
        for item in self.definitions:
            parts.extend((item.name, item.grain, item.text))
        return _tokens(" ".join(parts))


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


def _view_from_manifest(manifest: Any) -> BoundCatalog | None:
    if not space_catalog_enabled() or manifest is None:
        return None
    raw = getattr(manifest, "space_catalog", None)
    if raw is None:
        return None
    view = _view_from_raw(raw)
    if view is None:
        return None
    return _clip_to_signed(view, _signed_tables(manifest))


@contextmanager
def open_space_catalog(verified: Any) -> Iterator[None]:
    """Bind the catalog for one ask. Flag off, or no catalog, binds nothing."""
    manifest = getattr(verified, "manifest", None) if verified is not None else None
    token: Token[BoundCatalog | None] = _BOUND.set(_view_from_manifest(manifest))
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

    Columns come from the Space's objects. When none are declared, a sentinel
    column keeps the gate from falling through to the demo column list.
    """
    catalog = current_space_catalog()
    if catalog is None:
        return None
    tables: dict[str, dict[str, Any]] = {}
    signed = catalog.signed

    def _add(raw: str, columns: list[str]) -> None:
        bare = raw.split(".")[-1].strip()
        # The object name is not a second table. Only a signed bare name enters.
        if not bare or _bare_table(bare) not in signed:
            return
        spec = tables.setdefault(bare, {"columns": [], "row_key": []})
        if columns:
            spec["columns"] = list(columns)

    for name in catalog.granted_tables:
        _add(name, [])
    for obj in catalog.objects:
        _add(obj.relation or obj.name, list(obj.key))
    if tables and not any(spec["columns"] for spec in tables.values()):
        for spec in tables.values():
            spec["columns"] = ["_space_catalog"]
    return {"tables": tables, "glossary": [], "sensitive_columns": []}


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall((text or "").casefold()))


def text_stays_in_catalog(text: str, catalog: BoundCatalog, question: str) -> bool:
    """True when every long token is the question, the catalog, or our sentence."""
    allowed = catalog.tokens() | _tokens(question) | _TEMPLATE_WORDS
    for token in _tokens(text):
        if len(token) >= 3 and token not in allowed:
            return False
    return True


def fallback_why(catalog: BoundCatalog) -> str:
    """A reason that names only this Space's measures or tables."""
    labels: list[str] = []
    for measure in catalog.measures:
        label = measure.description or measure.name
        if label:
            labels.append(label)
    if not labels:
        labels = [name for name in catalog.granted_tables if name]
    shown = ", ".join(labels)
    if not shown:
        return "this Space has no approved measure, so it cannot be served directly"
    return (
        f"none of the approved measures ({shown}) answers that question, "
        "so it cannot be served directly"
    )


def confine_why(text: str, catalog: BoundCatalog, question: str) -> str:
    if text_stays_in_catalog(text, catalog, question):
        return text
    return fallback_why(catalog)


def confine_closest(text: str, catalog: BoundCatalog, question: str) -> str:
    from CortexOS.dms.metric_choice import NO_CLOSEST, is_no_closest

    cleaned = " ".join(str(text or "").split())
    if not cleaned or is_no_closest(cleaned):
        return NO_CLOSEST
    if text_stays_in_catalog(cleaned, catalog, question):
        return cleaned
    return NO_CLOSEST


def listing_text(catalog: BoundCatalog) -> str:
    """What this Space can answer. No demo table or measure is named."""
    tables = ", ".join(name for name in catalog.granted_tables if name) or "none"
    labels = [measure.description or measure.name for measure in catalog.measures]
    measures = ", ".join(label for label in labels if label) or "none"
    return f"This Space can answer from {tables}. Approved measures: {measures}."
