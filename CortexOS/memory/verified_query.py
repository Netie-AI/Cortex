"""VERIFIED-QUERY (#309) — steward-confirmed question -> SQL reuse on C-MEM.

A verified query is a C-MEM ``solution`` (#290) that a steward confirmed, plus
the parameters found in its question: SKU, location id, warehouse code, dates.
The confirmed SQL lives in solution memory (``SpaceMemory.confirm_solution``);
this library keeps only the proposal, its parameters and its status
(``pending`` / ``confirmed`` / ``revoked`` / ``superseded``), keyed by Space.

Matching is deterministic: an incoming question's normalised intent
(stopwords dropped, parameter values slotted, ontology terms canonicalised)
must equal the confirmed question's, and its parameter shape must fit. An
injected :class:`ModelMatcher` may only pick among confirmed queries whose
shape already fits; it never writes SQL and never chooses values.

Values replace the confirmed literals as numbered placeholders (``$1``,
``$2`` ...) and travel as bind parameters, so a value never enters SQL text.
The SQL is re-run through an executor the caller injects; nothing here stores
or serves rows. A reuse is marked ``reused`` and is never a validation.

Rules, each a named refusal stamped in the Space:

* only a steward confirms, revokes or lists (``MEMORY_NOT_STEWARD``);
* a query that is not confirmed, or was revoked, is never matched;
* a write tagged with a scored pack id is refused and a scored round reads
  nothing — the C-MEM guards themselves, not copies of them;
* Space B never sees Space A's queries (``MEMORY_NOT_IN_SPACE``);
* a value that is not exactly its type is refused (``VQ_PARAM_INVALID``).

This module holds no model, no database driver and no source-DB handle.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.memory.space_memory import (
    NOT_IN_SPACE,
    NOT_STEWARD,
    REUSE_EXEC_FAILED,
    SCORED_ROUND_READ,
    SCORED_ROUND_WRITE,
    SOURCE_MISSING,
    STEWARD_ROLE,
    Actor,
    MemoryEntry,
    MemoryRefused,
    MemoryStamp,
    SpaceMemory,
    get_space_memory,
    question_key,
)

PENDING = "pending"
CONFIRMED = "confirmed"
REVOKED = "revoked"
SUPERSEDED = "superseded"

SQL_INVALID = "VQ_SQL_INVALID"
PARAM_NOT_IN_SQL = "VQ_PARAM_NOT_IN_SQL"
PARAM_AMBIGUOUS = "VQ_PARAM_AMBIGUOUS"
PARAM_INVALID = "VQ_PARAM_INVALID"
PARAM_SHAPE = "VQ_PARAM_SHAPE"
NOT_PENDING = "VQ_NOT_PENDING"
AMBIGUOUS_MATCH = "VQ_AMBIGUOUS_MATCH"

PATH_ENV = "CORTEX_VERIFIED_QUERY_PATH"

#: Parameter types a verified query may bind. A value must fully match.
PARAM_PATTERNS: dict[str, re.Pattern[str]] = {
    "sku": re.compile(r"SKU-[A-Z0-9]+(?:-[A-Z0-9]+)*"),
    "location_id": re.compile(r"LOC-[0-9]{1,6}"),
    "warehouse_code": re.compile(r"WH-[A-Z0-9]{1,8}"),
}
DATE_TYPE = "date"

_MONTHS = {
    name: i
    for i, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_VALUE_RE = re.compile(
    r"\b(?:"
    r"(?P<sku>SKU-[A-Z0-9]+(?:-[A-Z0-9]+)*)"
    r"|(?P<location_id>LOC-[0-9]{1,6})"
    r"|(?P<warehouse_code>WH-[A-Z0-9]{1,8})"
    r"|(?P<iso_date>\d{4}-\d{2}-\d{2})"
    r"|(?P<month>(?P<month_name>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(?P<month_year>\d{4}))"
    r"|(?P<year_month>\d{4}-\d{2})"
    r")\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "of", "for", "in", "on", "at", "is", "are", "was", "were",
        "what", "whats", "how", "much", "many", "our", "we", "us", "me", "my",
        "do", "does", "did", "show", "give", "tell", "please", "can", "you", "i",
    }
)
_TOKEN_RE = re.compile(r"<[a-z_]+>|[a-z0-9_]+")


class VerifiedQueryError(ValueError):
    """A pure-function refusal; the library re-raises it as a stamped MemoryRefused."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: str
    literal: str


@dataclass(frozen=True, slots=True)
class ExtractedValue:
    type: str
    value: Any
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class VerifiedQuery:
    id: str
    space_id: str
    question: str
    sql: str
    params: tuple[Param, ...]
    status: str
    source: str
    proposed_by: str
    proposed_at: str
    entry_id: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    revoked_by: str | None = None
    revoked_at: str | None = None


@dataclass(frozen=True, slots=True)
class BoundQuery:
    """``sql`` carries ``$1..$n`` placeholders; ``values`` are bound, never inlined.

    ``display_sql`` renders the validated values as quoted literals for the
    reader and for drillthrough; it is never what the bound re-run executes.
    """

    sql: str
    values: tuple[Any, ...]
    display_sql: str
    bound: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelPick:
    """What a model matcher chose. ``served`` copies the route stamp's ``served_*``."""

    query_id: str | None
    served: Mapping[str, Any] = field(default_factory=dict)


ModelMatcher = Callable[[str, Sequence[VerifiedQuery]], ModelPick]
BoundExecutor = Callable[[str, Sequence[Any]], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class VerifiedMatch:
    query: VerifiedQuery
    entry: MemoryEntry
    bound: BoundQuery
    matched_by: str
    served: Mapping[str, Any]
    read_stamp: MemoryStamp


@dataclass(frozen=True, slots=True)
class VerifiedReuse:
    """Outcome of re-running a verified query on current data.

    ``reused`` marks that a confirmed query was re-run, nothing more;
    ``validated`` is always False.
    """

    match: VerifiedMatch
    rows: list[dict[str, Any]] | None
    stamps: tuple[MemoryStamp, ...]
    error: str = ""
    reused: bool = True
    validated: bool = False

    @property
    def ok(self) -> bool:
        return self.rows is not None and not self.error

    @property
    def query(self) -> VerifiedQuery:
        return self.match.query

    @property
    def entry(self) -> MemoryEntry:
        return self.match.entry


# -- pure functions ---------------------------------------------------------
def _next_month(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


def extract_values(question: str) -> list[ExtractedValue]:
    """Typed parameter values in order of appearance. A month yields two dates."""
    out: list[ExtractedValue] = []
    for m in _VALUE_RE.finditer(question):
        start, end = m.span()
        for kind in PARAM_PATTERNS:
            if m.group(kind):
                out.append(ExtractedValue(kind, m.group(kind).upper(), start, end))
                break
        else:
            try:
                if m.group("iso_date"):
                    out.append(
                        ExtractedValue(DATE_TYPE, date.fromisoformat(m.group("iso_date")), start, end)
                    )
                    continue
                if m.group("month"):
                    first = date(int(m.group("month_year")), _MONTHS[m.group("month_name").lower()], 1)
                else:
                    year, month = m.group("year_month").split("-")
                    first = date(int(year), int(month), 1)
            except ValueError:
                continue
            out.append(ExtractedValue(DATE_TYPE, first, start, end))
            out.append(ExtractedValue(DATE_TYPE, _next_month(first), start, end))
    return out


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def intent_signature(question: str, terms: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Normalised intent: parameter values become ``<type>`` slots, ontology
    phrases become their canonical term, stopwords and bare numbers drop out."""
    text = question
    for v in sorted(
        {(v.start, v.end, v.type) for v in extract_values(question)}, reverse=True
    ):
        text = f"{text[: v[0]]} <{v[2]}> {text[v[1]:]}"
    text = text.lower()
    for phrase, canon in sorted((terms or {}).items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<![a-z0-9_]){re.escape(phrase.lower())}(?![a-z0-9_])", f" {canon} ", text)
    tokens = {_stem(t) for t in _TOKEN_RE.findall(text) if t not in _STOPWORDS and not t.isdigit()}
    return tuple(sorted(tokens))


def _literal_text(kind: str, value: Any) -> str:
    return value.isoformat() if kind == DATE_TYPE else str(value)


def _parse_query(sql: str) -> exp.Query:
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception as exc:  # noqa: BLE001 — any parse failure is a refusal
        raise VerifiedQueryError(SQL_INVALID, f"SQL does not parse: {exc}") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise VerifiedQueryError(SQL_INVALID, "a verified query is exactly one SELECT")
    return statements[0]


def placeholder_numbers(sql: str) -> set[int]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    out: set[int] = set()
    for node in tree.find_all(exp.Placeholder, exp.Parameter):
        raw = str(node.this.this if isinstance(node.this, exp.Expression) else node.this or "")
        out.add(int(raw) if raw.isdigit() else -1)
    return out


def discover_params(question: str, sql: str) -> tuple[Param, ...]:
    """Each value in the question must be a string literal in the SQL."""
    tree = _parse_query(sql)
    if any(True for _ in tree.find_all(exp.Placeholder, exp.Parameter)):
        raise VerifiedQueryError(SQL_INVALID, "confirmed SQL carries literals, not placeholders")
    literals = {lit.this for lit in tree.find_all(exp.Literal) if lit.is_string}
    params: list[Param] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for v in extract_values(question):
        literal = _literal_text(v.type, v.value)
        if literal not in literals:
            raise VerifiedQueryError(
                PARAM_NOT_IN_SQL, f"{v.type} {literal!r} is in the question but not in the SQL"
            )
        if literal in seen:
            raise VerifiedQueryError(PARAM_AMBIGUOUS, f"{literal!r} names two parameters")
        seen.add(literal)
        counts[v.type] = counts.get(v.type, 0) + 1
        params.append(Param(f"{v.type}_{counts[v.type]}", v.type, literal))
    return tuple(params)


def validate_value(kind: str, value: Any) -> Any:
    """Return ``value`` if it is exactly a ``kind``; otherwise refuse."""
    if kind == DATE_TYPE:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
    elif kind in PARAM_PATTERNS:
        if isinstance(value, str) and PARAM_PATTERNS[kind].fullmatch(value):
            return value
    raise VerifiedQueryError(PARAM_INVALID, f"{value!r} is not a {kind}")


def assign_values(params: Sequence[Param], values: Sequence[ExtractedValue]) -> dict[str, Any]:
    """Map question values to parameters by type, in order of appearance."""
    by_type: dict[str, list[Any]] = {}
    for v in values:
        by_type.setdefault(v.type, []).append(v.value)
    wanted: dict[str, int] = {}
    for p in params:
        wanted[p.type] = wanted.get(p.type, 0) + 1
    if {k: len(v) for k, v in by_type.items()} != wanted:
        raise VerifiedQueryError(PARAM_SHAPE, "question values do not fit the query's parameters")
    taken: dict[str, int] = {}
    out: dict[str, Any] = {}
    for p in params:
        i = taken.get(p.type, 0)
        out[p.name] = by_type[p.type][i]
        taken[p.type] = i + 1
    return out


def bind_sql(sql: str, params: Sequence[Param], values: Sequence[Any]) -> tuple[str, tuple[Any, ...]]:
    """Replace each parameter's confirmed literal with ``$i``; values stay out of the text."""
    tree = _parse_query(sql)
    index = {p.literal: i for i, p in enumerate(params, start=1)}
    for lit in list(tree.find_all(exp.Literal)):
        if lit.is_string and lit.this in index:
            lit.replace(exp.Placeholder(this=str(index[lit.this])))
    template = tree.sql(dialect="duckdb")
    if placeholder_numbers(template) != set(range(1, len(params) + 1)):
        raise VerifiedQueryError(PARAM_SHAPE, "every parameter must bind exactly its placeholder")
    return template, tuple(values)


def render_sql(sql: str, params: Sequence[Param], bound: Mapping[str, Any]) -> str:
    tree = _parse_query(sql)
    texts = {p.literal: _literal_text(p.type, bound[p.name]) for p in params}
    for lit in list(tree.find_all(exp.Literal)):
        if lit.is_string and lit.this in texts:
            lit.replace(exp.Literal.string(texts[lit.this]))
    return tree.sql(dialect="duckdb")


def bind(query: VerifiedQuery, bound: Mapping[str, Any]) -> BoundQuery:
    if set(bound) != {p.name for p in query.params}:
        raise VerifiedQueryError(PARAM_SHAPE, "bind names every parameter exactly once")
    checked = {p.name: validate_value(p.type, bound[p.name]) for p in query.params}
    sql, values = bind_sql(query.sql, query.params, [checked[p.name] for p in query.params])
    return BoundQuery(
        sql=sql,
        values=values,
        display_sql=render_sql(query.sql, query.params, checked),
        bound=checked,
    )


# -- library ----------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS verified_queries (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    question TEXT NOT NULL,
    sql TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    source TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    entry_id TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    revoked_by TEXT,
    revoked_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def no_model_served(query_id: str) -> dict[str, Any]:
    return {
        "served_provider": None,
        "served_model": None,
        "served_local": False,
        "served_reason": f"verified_query:{query_id} (no model called)",
    }


class VerifiedQueryLibrary:
    """Per-Space verified queries over one :class:`SpaceMemory`."""

    def __init__(self, memory: SpaceMemory, path: str = ":memory:") -> None:
        self.memory = memory
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)

    # -- guards -------------------------------------------------------------
    @staticmethod
    def _in_space(space_id: str) -> tuple[str, list[Any]]:
        """Storage-side Space predicate for library rows."""
        return "space_id = ?", [space_id]

    @staticmethod
    def _live(query: VerifiedQuery, entry: MemoryEntry | None) -> bool:
        """Only a confirmed query whose steward-confirmed solution is still in memory."""
        return (
            query.status == CONFIRMED
            and entry is not None
            and entry.id == query.entry_id
            and entry.validation == "steward_confirm"
            and entry.body.get("sql") == query.sql
        )

    def _refuse(
        self, code: str, message: str, *, space_id: str, actor: str, entry_ids: Sequence[str] = ()
    ) -> MemoryRefused:
        return self.memory._refuse(
            code, message, space_id=space_id, kind="solution", actor=actor, entry_ids=entry_ids
        )

    def _require_steward(self, space_id: str, steward: Actor, what: str) -> None:
        if steward.role != STEWARD_ROLE:
            raise self._refuse(
                NOT_STEWARD, f"only a steward {what} a verified query", space_id=space_id,
                actor=steward.actor_id,
            )

    def _stamp(
        self, op: str, space_id: str, actor: str, entry_ids: Sequence[str] = (), reason: str = ""
    ) -> MemoryStamp:
        return self.memory._stamp(op, space_id, "solution", entry_ids, actor, reason=reason)

    # -- storage ------------------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row) -> VerifiedQuery:
        return VerifiedQuery(
            id=row["id"],
            space_id=row["space_id"],
            question=row["question"],
            sql=row["sql"],
            params=tuple(Param(**p) for p in json.loads(row["params"])),
            status=row["status"],
            source=row["source"],
            proposed_by=row["proposed_by"],
            proposed_at=row["proposed_at"],
            entry_id=row["entry_id"],
            confirmed_by=row["confirmed_by"],
            confirmed_at=row["confirmed_at"],
            revoked_by=row["revoked_by"],
            revoked_at=row["revoked_at"],
        )

    def _select(self, space_id: str, where: str = "", args: Sequence[Any] = ()) -> list[VerifiedQuery]:
        clause, space_args = self._in_space(space_id)
        sql = f"SELECT * FROM verified_queries WHERE {clause}" + (f" AND {where}" if where else "")
        with self._lock:
            rows = self._con.execute(sql + " ORDER BY proposed_at, id", [*space_args, *args]).fetchall()
        return [self._row(r) for r in rows]

    def _get(self, space_id: str, query_id: str, actor: str) -> VerifiedQuery:
        found = self._select(space_id, "id = ?", (query_id,))
        if not found:
            raise self._refuse(
                NOT_IN_SPACE, "no such verified query in this Space", space_id=space_id, actor=actor
            )
        return found[0]

    def _update(self, space_id: str, query_id: str, **values: Any) -> None:
        sets = ", ".join(f"{k} = ?" for k in values)
        clause, space_args = self._in_space(space_id)
        with self._lock:
            self._con.execute(
                f"UPDATE verified_queries SET {sets} WHERE {clause} AND id = ?",
                [*values.values(), *space_args, query_id],
            )
            self._con.commit()

    # -- steward flows ------------------------------------------------------
    def propose(
        self,
        *,
        space_id: str,
        question: str,
        sql: str,
        actor: str,
        source: str,
        pack_id: str | None = None,
        scored_pack_id: str | None = None,
    ) -> VerifiedQuery:
        """Record a question -> SQL pair for a steward to confirm. Never matched until then."""
        sid = self.memory._space(space_id)
        self.memory._refuse_scored_pack(
            space_id=sid,
            kind="solution",
            actor=actor,
            pack_id=pack_id,
            source=source,
            scored_pack_id=scored_pack_id,
        )
        self.memory._refuse_reused(
            space_id=sid, kind="solution", actor=actor, validation=None, source=source
        )
        if self.memory.is_scored_round(scored_pack_id):
            raise self._refuse(
                SCORED_ROUND_WRITE,
                "nothing is written to memory during a scored round",
                space_id=sid,
                actor=actor,
            )
        if not source.strip():
            raise self._refuse(SOURCE_MISSING, "every proposal needs a source", space_id=sid, actor=actor)
        try:
            params = discover_params(question, sql)
        except VerifiedQueryError as exc:
            raise self._refuse(exc.code, str(exc), space_id=sid, actor=actor) from exc
        query_id = f"vq_{uuid.uuid4().hex[:16]}"
        with self._lock:
            self._con.execute(
                "INSERT INTO verified_queries (id, space_id, question, sql, params, status, source,"
                " proposed_by, proposed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    query_id,
                    sid,
                    question,
                    sql,
                    json.dumps([{"name": p.name, "type": p.type, "literal": p.literal} for p in params]),
                    PENDING,
                    source,
                    actor,
                    _now(),
                ),
            )
            self._con.commit()
        self._stamp("vq_propose", sid, actor, reason=query_id)
        return self._get(sid, query_id, actor)

    def confirm(
        self,
        *,
        space_id: str,
        query_id: str,
        steward: Actor,
        scored_pack_id: str | None = None,
    ) -> VerifiedQuery:
        """Steward confirm: the pair enters C-MEM solution memory; only now can it match."""
        sid = self.memory._space(space_id)
        query = self._get(sid, query_id, steward.actor_id)
        self._require_steward(sid, steward, "confirms")
        if query.status != PENDING:
            raise self._refuse(
                NOT_PENDING, f"verified query is {query.status}, not pending", space_id=sid,
                actor=steward.actor_id,
            )
        entry, _ = self.memory.confirm_solution(
            space_id=sid,
            question=query.question,
            sql=query.sql,
            steward=steward,
            source=f"verified_query:{query.id};{query.source}",
            scored_pack_id=scored_pack_id,
        )
        for older in self._select(sid, "status = ? AND entry_id = ? AND id != ?", (CONFIRMED, entry.id, query.id)):
            self._update(sid, older.id, status=SUPERSEDED)
        self._update(
            sid,
            query.id,
            status=CONFIRMED,
            entry_id=entry.id,
            confirmed_by=steward.actor_id,
            confirmed_at=_now(),
        )
        self._stamp("vq_confirm", sid, steward.actor_id, (entry.id,), reason=query.id)
        return self._get(sid, query.id, steward.actor_id)

    def _retire(self, query: VerifiedQuery, steward: Actor) -> tuple[str, ...]:
        deleted: tuple[str, ...] = ()
        if query.status == CONFIRMED and query.entry_id:
            try:
                deleted, _ = self.memory.steward_delete(
                    space_id=query.space_id, entry_id=query.entry_id, steward=steward
                )
            except MemoryRefused as exc:
                if exc.code != NOT_IN_SPACE:
                    raise
        self._update(
            query.space_id, query.id, status=REVOKED, revoked_by=steward.actor_id, revoked_at=_now()
        )
        return deleted

    def revoke(self, *, space_id: str, query_id: str, steward: Actor) -> VerifiedQuery:
        """Steward revoke: the solution and everything derived from it leave memory."""
        sid = self.memory._space(space_id)
        query = self._get(sid, query_id, steward.actor_id)
        self._require_steward(sid, steward, "revokes")
        deleted = self._retire(query, steward)
        self._stamp("vq_revoke", sid, steward.actor_id, deleted, reason=query.id)
        return self._get(sid, query.id, steward.actor_id)

    def list_queries(
        self, *, space_id: str, steward: Actor, status: str | None = None
    ) -> list[VerifiedQuery]:
        sid = self.memory._space(space_id)
        self._require_steward(sid, steward, "lists")
        found = self._select(sid, "status = ?", (status,)) if status else self._select(sid)
        self._stamp("vq_list", sid, steward.actor_id, reason=",".join(q.id for q in found))
        return found

    # -- ask path -----------------------------------------------------------
    def _live_entry(
        self, query: VerifiedQuery, actor: str, scored_pack_id: str | None
    ) -> tuple[MemoryEntry | None, MemoryStamp]:
        entries, stamp = self.memory.read(
            space_id=query.space_id,
            kind="solution",
            key=question_key(query.question),
            actor=actor,
            scored_pack_id=scored_pack_id,
        )
        entry = entries[0] if entries else None
        return (entry if self._live(query, entry) else None), stamp

    def match(
        self,
        *,
        space_id: str,
        question: str,
        actor: str,
        terms: Mapping[str, str] | None = None,
        scored_pack_id: str | None = None,
        model: ModelMatcher | None = None,
    ) -> VerifiedMatch | None:
        """The one confirmed query this question asks again, with its values bound."""
        sid = self.memory._space(space_id)
        if self.memory.is_scored_round(scored_pack_id):
            self._stamp("read", sid, actor, reason=SCORED_ROUND_READ)
            return None
        values = extract_values(question)
        signature = intent_signature(question, terms)
        fits: list[tuple[VerifiedQuery, dict[str, Any]]] = []
        for query in self._select(sid):
            try:
                fits.append((query, assign_values(query.params, values)))
            except VerifiedQueryError:
                continue
        hits: list[tuple[VerifiedQuery, dict[str, Any], MemoryEntry, MemoryStamp]] = []
        for query, assigned in fits:
            if intent_signature(query.question, terms) != signature:
                continue
            entry, stamp = self._live_entry(query, actor, scored_pack_id)
            if entry is not None:
                hits.append((query, assigned, entry, stamp))
        matched_by = "deterministic"
        served: dict[str, Any] = {}
        if len(hits) > 1:
            self._stamp(
                "refuse", sid, actor, [h[2].id for h in hits], reason=AMBIGUOUS_MATCH
            )
            return None
        if not hits and model is not None:
            live: dict[str, tuple[VerifiedQuery, dict[str, Any], MemoryEntry, MemoryStamp]] = {}
            for query, assigned in fits:
                entry, stamp = self._live_entry(query, actor, scored_pack_id)
                if entry is not None:
                    live[query.id] = (query, assigned, entry, stamp)
            if live:
                pick = model(question, [hit[0] for hit in live.values()])
                if pick.query_id in live:
                    hits = [live[pick.query_id]]
                    matched_by = "model"
                    served = dict(pick.served) or {
                        "served_reason": f"verified_query:{pick.query_id} (model pick, no route stamp)"
                    }
        if not hits:
            return None
        query, assigned, entry, stamp = hits[0]
        try:
            bound = bind(query, assigned)
        except VerifiedQueryError as exc:
            self._stamp("refuse", sid, actor, (entry.id,), reason=f"{exc.code}: {query.id}")
            return None
        return VerifiedMatch(
            query=query,
            entry=entry,
            bound=bound,
            matched_by=matched_by,
            served=served if matched_by == "model" else no_model_served(query.id),
            read_stamp=stamp,
        )

    def reuse(
        self,
        *,
        space_id: str,
        question: str,
        execute: BoundExecutor,
        actor: str,
        terms: Mapping[str, str] | None = None,
        scored_pack_id: str | None = None,
        model: ModelMatcher | None = None,
    ) -> VerifiedReuse | None:
        """Re-run a matched verified query on current data. Never serves stored rows."""
        found = self.match(
            space_id=space_id,
            question=question,
            actor=actor,
            terms=terms,
            scored_pack_id=scored_pack_id,
            model=model,
        )
        if found is None:
            return None
        try:
            rows: list[dict[str, Any]] | None = [
                dict(r) for r in execute(found.bound.sql, found.bound.values)
            ]
            error = ""
        except Exception as exc:  # noqa: BLE001 — the ask path falls back to the engine
            rows, error = None, f"{type(exc).__name__}: {exc}"
        reason = f"{REUSE_EXEC_FAILED}: {error}" if error else "re-ran on current data"
        how = found.matched_by
        if how == "model":
            how += f", served_model={found.served.get('served_model')}"
        stamp = self._stamp(
            "reuse",
            found.entry.space_id,
            actor,
            (found.entry.id,),
            reason=f"verified_query:{found.query.id} ({how}) {reason}",
        )
        return VerifiedReuse(match=found, rows=rows, stamps=(found.read_stamp, stamp), error=error)


@dataclass(slots=True)
class _Holder:
    library: VerifiedQueryLibrary | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_HOLDER = _Holder()


def get_verified_queries() -> VerifiedQueryLibrary:
    """The process library, always over the current :func:`get_space_memory`."""
    memory = get_space_memory()
    with _HOLDER.lock:
        if _HOLDER.library is None or _HOLDER.library.memory is not memory:
            _HOLDER.library = VerifiedQueryLibrary(memory, os.environ.get(PATH_ENV) or ":memory:")
        return _HOLDER.library


def reset_verified_queries_for_tests(library: VerifiedQueryLibrary | None = None) -> VerifiedQueryLibrary:
    with _HOLDER.lock:
        _HOLDER.library = library or VerifiedQueryLibrary(get_space_memory())
        return _HOLDER.library
