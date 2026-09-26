"""Caller ontology for ``POST /v1/insights`` (trust boundary).

DMS sends the Space's retrieved schema/ontology context as ``ontology`` (the
``retrieve_short_context`` shape: ``schema`` = ``[{table, columns, score}]``,
``measures`` = ``{name: {grain, description}}``, ``objects``, ``links``,
``columns``). Before this module the field was silently dropped and every ask
was ranked against the engine's built-in pack, so a SQL-source Space (BIRD)
refused before generation or matched demo metrics.

This module only validates and normalises. It never reads a pack, never opens
a database, and never lets caller text reach SQL: names must be identifiers,
descriptions must not look like SQL, sizes are bounded. A malformed ontology
raises ``CallerOntologyError`` with a stable ``code`` the route turns into a
named 4xx ABSTAIN, never a 500.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Same identifier rule DMS applies before it sends a name (``_safe_ident``).
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
MAX_ONTOLOGY_BYTES = 64 * 1024
MAX_PLAN_BYTES = 4 * 1024
MAX_TABLES = 64
MAX_COLUMNS_PER_TABLE = 256
MAX_MEASURES = 256
MAX_LINKS = 256
MAX_TEXT = 500
MAX_FIELD = 64

# Statement-shaped SQL only: prose like "lots below reorder level; one per lot"
# is a legitimate DMS description and must not 4xx the demo Space.
_SQLISH = re.compile(
    r"\bselect\b[\s\S]*\bfrom\b|\binsert\s+into\b|\bdelete\s+from\b|"
    r"\bdrop\s+(?:table|view|schema|database|macro)\b|\bupdate\s+\w+\s+set\b|"
    r"\battach\s+(?:database\s+)?'|\bcopy\s+\w+\s+(?:to|from)\s+'|"
    r"\bcreate\s+(?:or\s+replace\s+)?(?:table|view|macro|function|secret)\b|"
    r"\bpragma\s+\w+|\bread_(?:csv|parquet|json)\w*\s*\(",
    re.I,
)

SOURCE_CALLER = "caller_ontology"
SOURCE_PACK = "engine_pack"


class CallerOntologyError(ValueError):
    """A caller-sent ontology/plan the engine refuses to use. ``code`` is stable."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def reason(self) -> str:
        return f"caller_ontology_invalid:{self.code}"


@dataclass(frozen=True)
class CallerCatalog:
    """Validated caller catalog. Tables and columns are lower-cased identifiers."""

    tables: dict[str, list[str]]
    scores: dict[str, int]
    measures: dict[str, str | None]  # name -> grain table (if a caller table)
    links: list[tuple[str, str, str]]  # (id, from, to)
    verified: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def table_names(self) -> set[str]:
        return set(self.tables)


def _ident(value: Any, where: str) -> str:
    if not isinstance(value, str) or not IDENT.match(value):
        raise CallerOntologyError("bad_identifier", f"{where} is not an identifier")
    return value.lower()


def _text(value: Any, where: str) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        raise CallerOntologyError("bad_text", f"{where} is not a string")
    if len(value) > MAX_TEXT:
        raise CallerOntologyError("too_large", f"{where} exceeds {MAX_TEXT} chars")
    if _SQLISH.search(value):
        raise CallerOntologyError("sql_in_ontology", f"{where} carries SQL text")


def _size(raw: Any, limit: int, what: str) -> None:
    try:
        blob = json.dumps(raw, default=str)
    except (TypeError, ValueError) as exc:
        raise CallerOntologyError("not_json", f"{what} is not JSON-serialisable") from exc
    if len(blob.encode("utf-8")) > limit:
        raise CallerOntologyError("too_large", f"{what} exceeds {limit} bytes")


def _mapping(raw: Any, where: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CallerOntologyError("bad_shape", f"{where} must be an object")
    return raw


def parse_caller_ontology(raw: Any) -> CallerCatalog | None:
    """Validate a caller ontology. None when absent or it names no tables.

    Raises ``CallerOntologyError`` on anything malformed. Measure SQL is
    refused outright: DMS keeps measure expressions off the wire, and an
    engine that accepted them would be running caller SQL.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CallerOntologyError("bad_shape", "ontology must be an object")
    _size(raw, MAX_ONTOLOGY_BYTES, "ontology")

    tables: dict[str, list[str]] = {}
    scores: dict[str, int] = {}
    schema = raw.get("schema")
    if schema is not None and not isinstance(schema, list):
        raise CallerOntologyError("bad_shape", "ontology.schema must be a list")
    for idx, row in enumerate(schema or []):
        if not isinstance(row, dict):
            raise CallerOntologyError("bad_shape", f"ontology.schema[{idx}] must be an object")
        name = _ident(row.get("table"), f"ontology.schema[{idx}].table")
        cols_raw = row.get("columns") or []
        if not isinstance(cols_raw, list):
            raise CallerOntologyError("bad_shape", f"ontology.schema[{idx}].columns must be a list")
        if len(cols_raw) > MAX_COLUMNS_PER_TABLE:
            raise CallerOntologyError("too_large", f"ontology.schema[{idx}].columns too long")
        cols = tables.setdefault(name, [])
        for jdx, col in enumerate(cols_raw):
            c = _ident(col, f"ontology.schema[{idx}].columns[{jdx}]")
            if c not in cols:
                cols.append(c)
        score = row.get("score", 0)
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise CallerOntologyError("bad_shape", f"ontology.schema[{idx}].score must be a number")
        scores[name] = max(scores.get(name, 0), int(score))
    if len(tables) > MAX_TABLES:
        raise CallerOntologyError("too_large", f"ontology names more than {MAX_TABLES} tables")

    # ``columns`` is keyed by ontology object; merge only where it is a table.
    col_map = _mapping(raw.get("columns"), "ontology.columns")
    for key, cols_raw in col_map.items():
        obj = _ident(key, "ontology.columns key")
        if not isinstance(cols_raw, list):
            raise CallerOntologyError("bad_shape", f"ontology.columns.{obj} must be a list")
        if len(cols_raw) > MAX_COLUMNS_PER_TABLE:
            raise CallerOntologyError("too_large", f"ontology.columns.{obj} too long")
        names = [_ident(c, f"ontology.columns.{obj}[]") for c in cols_raw]
        if obj in tables:
            tables[obj].extend(c for c in names if c not in tables[obj])

    objects = _mapping(raw.get("objects"), "ontology.objects")
    for key, spec in objects.items():
        obj = _ident(key, "ontology.objects key")
        spec_d = _mapping(spec, f"ontology.objects.{obj}")
        keys = spec_d.get("key") or []
        if not isinstance(keys, list):
            raise CallerOntologyError("bad_shape", f"ontology.objects.{obj}.key must be a list")
        for k in keys:
            _ident(k, f"ontology.objects.{obj}.key[]")

    measures: dict[str, str | None] = {}
    m_raw = _mapping(raw.get("measures"), "ontology.measures")
    if len(m_raw) > MAX_MEASURES:
        raise CallerOntologyError("too_large", f"ontology names more than {MAX_MEASURES} measures")
    for key, spec in m_raw.items():
        name = _ident(key, "ontology.measures key")
        spec_d = _mapping(spec, f"ontology.measures.{name}")
        for banned in ("sql", "expr", "expression", "query"):
            if banned in spec_d:
                raise CallerOntologyError(
                    "sql_in_ontology", f"ontology.measures.{name}.{banned} is not accepted"
                )
        grain = spec_d.get("grain")
        grain_n = _ident(grain, f"ontology.measures.{name}.grain") if grain is not None else None
        _text(spec_d.get("description"), f"ontology.measures.{name}.description")
        measures[name] = grain_n if grain_n in tables else None

    links: list[tuple[str, str, str]] = []
    l_raw = _mapping(raw.get("links"), "ontology.links")
    if len(l_raw) > MAX_LINKS:
        raise CallerOntologyError("too_large", f"ontology names more than {MAX_LINKS} links")
    for key, spec in l_raw.items():
        lid = _ident(key, "ontology.links key")
        spec_d = _mapping(spec, f"ontology.links.{lid}")
        src = _ident(spec_d.get("from"), f"ontology.links.{lid}.from")
        dst = _ident(spec_d.get("to"), f"ontology.links.{lid}.to")
        card = spec_d.get("cardinality")
        if card is not None and (not isinstance(card, str) or len(card) > MAX_FIELD):
            raise CallerOntologyError("bad_shape", f"ontology.links.{lid}.cardinality")
        if src in tables and dst in tables:
            links.append((lid, src, dst))

    if not tables:
        return None
    return CallerCatalog(
        tables=tables,
        scores=scores,
        measures=measures,
        links=links,
        verified=raw.get("verified") is True,
    )


def check_plan(raw: Any, what: str = "query_plan") -> dict[str, Any] | None:
    """Bounded, typed caller plan (prompt hint only). None when absent."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CallerOntologyError("bad_shape", f"{what} must be an object")
    _size(raw, MAX_PLAN_BYTES, what)
    measure = raw.get("measure")
    if measure is not None:
        _ident(measure, f"{what}.measure")
    return raw


def check_field(raw: Any, what: str) -> str | None:
    """Short free-form request field (mode, model_preference, ...)."""
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > MAX_FIELD:
        raise CallerOntologyError("bad_field", f"{what} must be a string <= {MAX_FIELD} chars")
    return raw


__all__ = [
    "CallerCatalog",
    "CallerOntologyError",
    "IDENT",
    "SOURCE_CALLER",
    "SOURCE_PACK",
    "check_field",
    "check_plan",
    "parse_caller_ontology",
]
