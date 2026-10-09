"""VERIFIED-QUERY (#309) — steward-confirmed examples for the SQL generator.

A verified query is a question and the SQL a steward confirmed for it. The
confirmed SQL is also a C-MEM solution (#290). This library keeps the pair
and its status (``pending`` / ``confirmed`` / ``revoked`` / ``superseded``),
keyed by Space.

The ask path does not match phrasing and does not run the stored SQL. It
hands the Space's confirmed pairs to the SQL generator as examples. The
generator writes new SQL. Nothing here stores or serves rows.

Rules, each a named refusal stamped in the Space:

* only a steward confirms, revokes or lists (``MEMORY_NOT_STEWARD``);
* a pair that is not confirmed, or was revoked, is never an example;
* a write tagged with a scored pack id is refused and a scored round reads
  nothing — the C-MEM guards themselves, not copies of them;
* Space B never sees Space A's pairs (``MEMORY_NOT_IN_SPACE``).

No parameter types, stopword lists, or source-specific tokens. Any source's
question and single SELECT can be an example.

This module holds no model, no database driver and no source-DB handle.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import sqlglot
from sqlglot import exp

from CortexOS.memory.space_memory import (
    NOT_IN_SPACE,
    NOT_STEWARD,
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
NOT_PENDING = "VQ_NOT_PENDING"

PATH_ENV = "CORTEX_VERIFIED_QUERY_PATH"

#: How many confirmed examples one prompt may carry. A budget, not a ranking.
EXAMPLE_CAP = 8


class VerifiedQueryError(ValueError):
    """A pure-function refusal; the library re-raises it as a stamped MemoryRefused."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class VerifiedQuery:
    id: str
    space_id: str
    question: str
    sql: str
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
class VerifiedExample:
    """One live example. ``read_stamp`` is the C-MEM read of its solution."""

    query: VerifiedQuery
    entry: MemoryEntry
    read_stamp: MemoryStamp


def _parse_query(sql: str) -> exp.Query:
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception as exc:  # noqa: BLE001 — any parse failure is a refusal
        raise VerifiedQueryError(SQL_INVALID, f"SQL does not parse: {exc}") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise VerifiedQueryError(SQL_INVALID, "a verified query is exactly one SELECT")
    return statements[0]


def require_one_select(sql: str) -> str:
    """Refuse anything that is not a single SELECT. Returns the SQL unchanged."""
    _parse_query(sql)
    return sql


_SCHEMA = """
CREATE TABLE IF NOT EXISTS verified_queries (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    question TEXT NOT NULL,
    sql TEXT NOT NULL,
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


class VerifiedQueryLibrary:
    """Per-Space verified examples over one :class:`SpaceMemory`."""

    def __init__(self, memory: SpaceMemory, path: str = ":memory:") -> None:
        self.memory = memory
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)

    @staticmethod
    def _in_space(space_id: str) -> tuple[str, list[Any]]:
        """Storage-side Space predicate for library rows."""
        return "space_id = ?", [space_id]

    @staticmethod
    def _live(query: VerifiedQuery, entry: MemoryEntry | None) -> bool:
        """Only a confirmed pair whose steward-confirmed solution is still in memory."""
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

    @staticmethod
    def _row(row: sqlite3.Row) -> VerifiedQuery:
        return VerifiedQuery(
            id=row["id"],
            space_id=row["space_id"],
            question=row["question"],
            sql=row["sql"],
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
        """Record a question -> SQL pair for a steward to confirm. Not an example until then."""
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
        if not question.strip():
            raise self._refuse(SQL_INVALID, "a verified query needs a question", space_id=sid, actor=actor)
        try:
            require_one_select(sql)
        except VerifiedQueryError as exc:
            raise self._refuse(exc.code, str(exc), space_id=sid, actor=actor) from exc
        query_id = f"vq_{uuid.uuid4().hex[:16]}"
        with self._lock:
            self._con.execute(
                "INSERT INTO verified_queries (id, space_id, question, sql, status, source,"
                " proposed_by, proposed_at) VALUES (?,?,?,?,?,?,?,?)",
                (query_id, sid, question, sql, PENDING, source, actor, _now()),
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
        """Steward confirm: the pair enters C-MEM and can be offered as an example."""
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
        for entry in entries:
            if self._live(query, entry):
                return entry, stamp
        return None, stamp

    def examples(
        self,
        *,
        space_id: str,
        actor: str,
        scored_pack_id: str | None = None,
    ) -> list[VerifiedExample]:
        """Every confirmed example in this Space.

        No word rank and no recency rank. The ask path asks the model which
        ids belong with the question. A scored round returns nothing.
        Unconfirmed, revoked and other Spaces are absent.
        """
        sid = self.memory._space(space_id)
        if self.memory.is_scored_round(scored_pack_id):
            self._stamp("read", sid, actor, reason=SCORED_ROUND_READ)
            return []
        found: list[VerifiedExample] = []
        for query in self._select(sid):
            entry, stamp = self._live_entry(query, actor, scored_pack_id)
            if entry is not None:
                found.append(VerifiedExample(query=query, entry=entry, read_stamp=stamp))
        return found


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
