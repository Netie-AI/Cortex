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

Which catalog ranks an ask is decided by DMS, not guessed here:
``ontology.source == "space"`` means the caller catalog is the whole universe
(the engine pack, its metrics and certified formulas are never consulted);
``"demo"`` or absent means the engine pack ranks exactly as it always has and
the ontology body is not used. Table names follow the shared naming rule:
``table`` or ``schema.table``, each part matching ``IDENT`` in full.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

#: One SQL identifier part (shared naming rule). Always used with ``fullmatch``:
#: ``re.match`` with ``$`` accepts a trailing newline (``"schools\n"``).
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MAX_ONTOLOGY_BYTES = 64 * 1024
MAX_PLAN_BYTES = 4 * 1024
MAX_TABLES = 64
MAX_COLUMNS_PER_TABLE = 256
MAX_MEASURES = 256
MAX_LINKS = 256
MAX_TEXT = 500
MAX_FIELD = 64
#: Schema scores are small token-overlap counts; anything past this is not one.
MAX_SCORE = 1_000_000

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
#: ``ontology.source`` on the wire. DMS says which Space this is; Cortex never
#: guesses it from column names. Absent means demo (older DMS builds).
WIRE_SOURCE_DEMO = "demo"
WIRE_SOURCE_SPACE = "space"
WIRE_SOURCES = (WIRE_SOURCE_DEMO, WIRE_SOURCE_SPACE)
#: Named ABSTAIN when an ontology was sent but names no tables.
EMPTY_REASON = "caller_ontology_empty"


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
    """Validated caller catalog (source=space).

    Table keys are lower-cased ``table`` or ``schema.table`` names exactly as
    declared; columns are lower-cased single identifiers.
    """

    tables: dict[str, list[str]]
    scores: dict[str, int]
    measures: dict[str, str | None]  # name -> grain table (if a caller table)
    links: list[tuple[str, str, str]]  # (id, from, to)
    verified: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def table_names(self) -> set[str]:
        return set(self.tables)

    @property
    def is_empty(self) -> bool:
        return not self.tables


def _ident(value: Any, where: str) -> str:
    """One identifier part: no dot, no quote, no whitespace, no trailing newline."""
    if not isinstance(value, str) or IDENT.fullmatch(value) is None:
        raise CallerOntologyError("bad_identifier", f"{where} is not an identifier")
    return value.lower()


def _table_name(value: Any, where: str) -> str:
    """``table`` or ``schema.table``, each part an identifier (shared naming rule)."""
    if not isinstance(value, str):
        raise CallerOntologyError("bad_identifier", f"{where} is not a table name")
    parts = value.split(".")
    if len(parts) > 2 or any(IDENT.fullmatch(p) is None for p in parts):
        raise CallerOntologyError(
            "bad_identifier", f"{where} is not a table name (table or schema.table)"
        )
    return value.lower()


def _resolve(name: str, tables: dict[str, list[str]]) -> str | None:
    """A declared table for ``name``: exact, or a bare name unique among the declared."""
    if name in tables:
        return name
    if "." in name:
        return None
    hits = [t for t in tables if "." in t and t.rsplit(".", 1)[-1] == name]
    return hits[0] if len(hits) == 1 else None


def ontology_source(raw: Any) -> str | None:
    """``demo`` / ``space`` from ``ontology.source``; None when no ontology was sent.

    Absent ``source`` is ``demo``: that is the body every DMS build before the
    shared naming rule sends for the demo Space, and it must rank exactly as it
    did. Any other value is a named 422.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise CallerOntologyError("bad_shape", "ontology must be an object")
    source = raw.get("source", WIRE_SOURCE_DEMO)
    if source is None:
        source = WIRE_SOURCE_DEMO
    if source not in WIRE_SOURCES:
        raise CallerOntologyError(
            "bad_source", "ontology.source must be one of " + ", ".join(WIRE_SOURCES)
        )
    return str(source)


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
    """Validate a caller ontology. None only when absent.

    A present ontology that names no tables comes back as a catalog with
    ``tables == {}`` (``is_empty``): the route abstains on it by name rather
    than silently ranking the engine pack, which is how a SQL-source Space
    whose schema retrieval matched nothing used to reach demo metrics.

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
        name = _table_name(row.get("table"), f"ontology.schema[{idx}].table")
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
        # JSON on the wire may carry NaN / Infinity / 1e400; int() of those raises
        # OverflowError / ValueError, which would surface as a 500.
        if not math.isfinite(score) or abs(score) > MAX_SCORE:
            raise CallerOntologyError(
                "bad_number", f"ontology.schema[{idx}].score must be finite and <= {MAX_SCORE}"
            )
        scores[name] = max(scores.get(name, 0), int(score))
    if len(tables) > MAX_TABLES:
        raise CallerOntologyError("too_large", f"ontology names more than {MAX_TABLES} tables")

    # ``columns`` is keyed by ontology object; merge only where it is a table.
    col_map = _mapping(raw.get("columns"), "ontology.columns")
    for key, cols_raw in col_map.items():
        obj = _table_name(key, "ontology.columns key")
        if not isinstance(cols_raw, list):
            raise CallerOntologyError("bad_shape", f"ontology.columns.{obj} must be a list")
        if len(cols_raw) > MAX_COLUMNS_PER_TABLE:
            raise CallerOntologyError("too_large", f"ontology.columns.{obj} too long")
        names = [_ident(c, f"ontology.columns.{obj}[]") for c in cols_raw]
        target = _resolve(obj, tables)
        if target is not None:
            tables[target].extend(c for c in names if c not in tables[target])

    objects = _mapping(raw.get("objects"), "ontology.objects")
    for key, spec in objects.items():
        obj = _table_name(key, "ontology.objects key")
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
        grain_n = (
            _table_name(grain, f"ontology.measures.{name}.grain") if grain is not None else None
        )
        _text(spec_d.get("description"), f"ontology.measures.{name}.description")
        measures[name] = _resolve(grain_n, tables) if grain_n is not None else None

    links: list[tuple[str, str, str]] = []
    l_raw = _mapping(raw.get("links"), "ontology.links")
    if len(l_raw) > MAX_LINKS:
        raise CallerOntologyError("too_large", f"ontology names more than {MAX_LINKS} links")
    for key, spec in l_raw.items():
        lid = _ident(key, "ontology.links key")
        spec_d = _mapping(spec, f"ontology.links.{lid}")
        src_n = _table_name(spec_d.get("from"), f"ontology.links.{lid}.from")
        dst_n = _table_name(spec_d.get("to"), f"ontology.links.{lid}.to")
        src = _resolve(src_n, tables)
        dst = _resolve(dst_n, tables)
        card = spec_d.get("cardinality")
        if card is not None and (not isinstance(card, str) or len(card) > MAX_FIELD):
            raise CallerOntologyError("bad_shape", f"ontology.links.{lid}.cardinality")
        if src is not None and dst is not None:
            links.append((lid, src, dst))

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
    "EMPTY_REASON",
    "IDENT",
    "MAX_SCORE",
    "SOURCE_CALLER",
    "SOURCE_PACK",
    "WIRE_SOURCES",
    "WIRE_SOURCE_DEMO",
    "WIRE_SOURCE_SPACE",
    "check_field",
    "check_plan",
    "ontology_source",
    "parse_caller_ontology",
]
