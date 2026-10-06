"""C-MEM (#290) — per-Space memory: tables, formulas, tools, solutions.

Four kinds, one store, keyed by Space in the storage query:

* ``table``    — schema, keys, joins, row counts from the connected DB or ontology.
* ``formula``  — metric definitions a steward confirmed.
* ``tool``     — which tool or step solved which kind of question, pass/fail.
* ``solution`` — validated answers from real use. Enter only through
  :meth:`SpaceMemory.confirm_solution` (steward) or
  :meth:`SpaceMemory.check_solution` (real-result check). A solution holds the
  question and its SQL, never rows or answer text, so reuse can only re-run.

Every entry carries ``source``, ``version``, ``written_at`` and ``space_id``.
Every read, write, view, delete, reuse and refusal is stamped with ``served_*``
fields (same naming as ``RouteStamp``) and kept per Space.

Hard rules, each a named refusal:

* nothing tagged with a scored-pack id is ever written (``MEMORY_SCORED_PACK_WRITE``);
* Space B never reads, derives from, or deletes Space A's entries
  (``MEMORY_NOT_IN_SPACE`` — the same code whether the id exists elsewhere or not);
* ``reused`` is never a validation (``MEMORY_REUSED_NOT_VALIDATION``);
* in a scored round nothing is written and only table/formula memory is read.

This module holds no model, no database driver and no source-DB handle
(import-linter contract 4). Reuse and real-result checks run SQL through an
executor the caller injects.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

KINDS: tuple[str, ...] = ("table", "formula", "tool", "solution")
SOLUTION_VALIDATIONS: frozenset[str] = frozenset({"steward_confirm", "real_result_check"})
STEWARD_ROLE = "steward"
# A scored round may read schema and steward formulas. Tools and solutions stay
# off so a round can never learn from, or be served by, scored questions.
SCORED_ROUND_READABLE: frozenset[str] = frozenset({"table", "formula"})

# Scored packs. Ids are matched after normalisation (case, '-', ' ' -> '_').
SCORED_PACK_IDS: frozenset[str] = frozenset(
    {
        "curated_ceo",  # DMS 52-question fixture (score_curated.py PACK_DENOMINATOR = 52)
        "dms_52",
        "bird_minidev",  # BIRD Mini-Dev (score_bird.py)
        "bird",
        "pack_d",  # DMS held-out Pack D, pinned by manifest root hash only (dms#339)
        "heldout_pack_d",
        "e63b422e26336c6cae5f824d1af69f68ee5236544160a0685d0f982fad2ceee7",
        "cca_eval",  # DMS real-world prove packs
        "hostile_score",
        "dms_golden_v1",  # Cortex bench packs
        "dms_paraphrase_v1",
        "dms_adversarial_v1",
        "c7_heldout_v1",
        "dms_180_curated_26",
    }
)
SCORED_PACK_PREFIXES: tuple[str, ...] = (
    "bird_",
    "pack_d_",
    "heldout_",
    "curated_ceo_",
    "prove_pack",
)
SCORED_PACKS_ENV = "CORTEX_SCORED_PACK_IDS"  # comma-separated; only ever adds
SCORED_ROUND_ENV = "CORTEX_SCORED_ROUND"
ENABLED_ENV = "CORTEX_SPACE_MEMORY"
PATH_ENV = "CORTEX_SPACE_MEMORY_PATH"

SCORED_PACK_WRITE = "MEMORY_SCORED_PACK_WRITE"
SCORED_ROUND_WRITE = "MEMORY_SCORED_ROUND_WRITE"
NOT_IN_SPACE = "MEMORY_NOT_IN_SPACE"
SPACE_UNBOUND = "MEMORY_SPACE_UNBOUND"
KIND_UNKNOWN = "MEMORY_KIND_UNKNOWN"
SOURCE_MISSING = "MEMORY_SOURCE_MISSING"
SOLUTION_UNVALIDATED = "MEMORY_SOLUTION_UNVALIDATED"
REUSED_NOT_VALIDATION = "MEMORY_REUSED_NOT_VALIDATION"
NOT_STEWARD = "MEMORY_NOT_STEWARD"
REAL_RESULT_EMPTY = "MEMORY_REAL_RESULT_EMPTY"
REAL_RESULT_MISMATCH = "MEMORY_REAL_RESULT_MISMATCH"
SCORED_ROUND_READ = "MEMORY_SCORED_ROUND_READ"
REUSE_EXEC_FAILED = "MEMORY_REUSE_EXEC_FAILED"

Executor = Callable[[str], Sequence[Mapping[str, Any]]]

_TRUTHY = {"1", "true", "on", "yes"}


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _TRUTHY


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_pack(value: str) -> str:
    return re.sub(r"[\s\-]+", "_", value.strip().lower())


def scored_pack_ids() -> frozenset[str]:
    extra = {_norm_pack(p) for p in os.environ.get(SCORED_PACKS_ENV, "").split(",") if p.strip()}
    return SCORED_PACK_IDS | frozenset(extra)


def is_scored_pack(value: str | None) -> bool:
    if not value or not str(value).strip():
        return False
    norm = _norm_pack(str(value))
    return norm in scored_pack_ids() or norm.startswith(SCORED_PACK_PREFIXES)


def _source_tokens(source: str) -> list[str]:
    return [tok for tok in re.split(r"[:/#@,;|\s]+", source) if tok]


def question_key(question: str) -> str:
    """Solution key: case, whitespace and trailing punctuation do not matter."""
    return re.sub(r"\s+", " ", question.strip().lower()).rstrip("?.! ")


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    role: str = ""


@dataclass(frozen=True, slots=True)
class MemoryStamp:
    served_op: str
    served_space_id: str
    served_kind: str | None
    served_entry_ids: tuple[str, ...]
    served_at: str
    served_by: str
    served_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "served_op": self.served_op,
            "served_space_id": self.served_space_id,
            "served_kind": self.served_kind,
            "served_entry_ids": list(self.served_entry_ids),
            "served_at": self.served_at,
            "served_by": self.served_by,
            "served_reason": self.served_reason,
        }


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    id: str
    space_id: str
    kind: str
    key: str
    body: Mapping[str, Any]
    source: str
    version: int
    written_at: str
    written_by: str
    derived_from: tuple[str, ...] = ()
    pack_id: str | None = None
    validation: str | None = None

    def provenance(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "space_id": self.space_id,
            "source": self.source,
            "version": self.version,
            "written_at": self.written_at,
        }


@dataclass(frozen=True, slots=True)
class SolutionReuse:
    """Outcome of re-running a stored solution on current data.

    ``reused`` marks that a solution was re-run, nothing more. ``validated`` is
    always False: a reuse is never a validation by itself.
    """

    entry: MemoryEntry
    sql: str
    rows: list[dict[str, Any]] | None
    stamps: tuple[MemoryStamp, ...]
    error: str = ""
    reused: bool = True
    validated: bool = False

    @property
    def ok(self) -> bool:
        return self.rows is not None and not self.error


class MemoryRefused(Exception):
    def __init__(self, code: str, message: str, stamp: MemoryStamp | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.stamp = stamp


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    version INTEGER NOT NULL,
    written_at TEXT NOT NULL,
    written_by TEXT NOT NULL,
    pack_id TEXT,
    validation TEXT,
    UNIQUE (space_id, kind, key)
);
CREATE TABLE IF NOT EXISTS derivations (
    space_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    PRIMARY KEY (child_id, parent_id)
);
CREATE TABLE IF NOT EXISTS stamps (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    space_id TEXT NOT NULL,
    stamp TEXT NOT NULL
);
"""


def _rows_match(actual: Sequence[Mapping[str, Any]], expected: Sequence[Mapping[str, Any]]) -> bool:
    """Unordered multiset of row values (column order kept, names ignored)."""

    def norm(value: Any) -> Any:
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            number = float(value)
            return round(number, 6) if math.isfinite(number) else str(number)
        return str(value).strip()

    def key(rows: Sequence[Mapping[str, Any]]) -> list[str]:
        return sorted(json.dumps([norm(v) for v in row.values()], default=str) for row in rows)

    return key(actual) == key(expected)


class SpaceMemory:
    """Per-Space memory store (stdlib sqlite; ``:memory:`` unless a path is given)."""

    def __init__(self, path: str = ":memory:", *, scored_round: bool = False) -> None:
        self._lock = threading.RLock()
        self._con = sqlite3.connect(path, check_same_thread=False)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._scored_round = scored_round

    # -- guards -------------------------------------------------------------
    def is_scored_round(self, scored_pack_id: str | None = None) -> bool:
        return (
            self._scored_round
            or _truthy(os.environ.get(SCORED_ROUND_ENV))
            or bool((scored_pack_id or "").strip())
        )

    @staticmethod
    def _in_space(space_id: str) -> tuple[str, list[Any]]:
        """Storage-side Space predicate. Every entry query goes through here."""
        return "space_id = ?", [space_id]

    def _refuse(
        self,
        code: str,
        message: str,
        *,
        space_id: str,
        kind: str | None,
        actor: str,
        entry_ids: Iterable[str] = (),
    ) -> MemoryRefused:
        stamp = self._stamp("refuse", space_id, kind, entry_ids, actor, reason=code)
        return MemoryRefused(code, message, stamp)

    @staticmethod
    def _space(space_id: str | None) -> str:
        sid = (space_id or "").strip()
        if not sid:
            # Not stamped: a stamp is kept per Space and there is none to keep it in.
            raise MemoryRefused(SPACE_UNBOUND, "per-Space memory needs a Space id")
        return sid

    def _refuse_scored_pack(
        self,
        *,
        space_id: str,
        kind: str,
        actor: str,
        pack_id: str | None,
        source: str,
        scored_pack_id: str | None,
    ) -> None:
        tagged = [
            t for t in (pack_id, scored_pack_id, *_source_tokens(source)) if is_scored_pack(t)
        ]
        if tagged:
            raise self._refuse(
                SCORED_PACK_WRITE,
                f"write tagged with scored pack {tagged[0]!r} is refused",
                space_id=space_id,
                kind=kind,
                actor=actor,
            )

    def _refuse_reused(
        self, *, space_id: str, kind: str, actor: str, validation: str | None, source: str
    ) -> None:
        if (validation or "").strip().lower() == "reused" or source.strip().lower().startswith(
            "reuse"
        ):
            raise self._refuse(
                REUSED_NOT_VALIDATION,
                "a reused answer is never a validation",
                space_id=space_id,
                kind=kind,
                actor=actor,
            )

    # -- stamps -------------------------------------------------------------
    def _stamp(
        self,
        op: str,
        space_id: str,
        kind: str | None,
        entry_ids: Iterable[str],
        actor: str,
        *,
        reason: str = "",
    ) -> MemoryStamp:
        stamp = MemoryStamp(
            served_op=op,
            served_space_id=space_id,
            served_kind=kind,
            served_entry_ids=tuple(entry_ids),
            served_at=_now(),
            served_by=actor,
            served_reason=reason,
        )
        with self._lock:
            self._con.execute(
                "INSERT INTO stamps (space_id, stamp) VALUES (?, ?)",
                (space_id, json.dumps(stamp.as_dict(), sort_keys=True)),
            )
            self._con.commit()
        return stamp

    def stamps(self, *, space_id: str) -> list[MemoryStamp]:
        sid = self._space(space_id)
        with self._lock:
            rows = self._con.execute(
                "SELECT stamp FROM stamps WHERE space_id = ? ORDER BY seq", (sid,)
            ).fetchall()
        out: list[MemoryStamp] = []
        for row in rows:
            raw = json.loads(row["stamp"])
            raw["served_entry_ids"] = tuple(raw["served_entry_ids"])
            out.append(MemoryStamp(**raw))
        return out

    # -- storage ------------------------------------------------------------
    def _entry(self, row: sqlite3.Row) -> MemoryEntry:
        with self._lock:
            parents = self._con.execute(
                "SELECT parent_id FROM derivations WHERE child_id = ? AND space_id = ? ORDER BY parent_id",
                (row["id"], row["space_id"]),
            ).fetchall()
        return MemoryEntry(
            id=row["id"],
            space_id=row["space_id"],
            kind=row["kind"],
            key=row["key"],
            body=json.loads(row["body"]),
            source=row["source"],
            version=int(row["version"]),
            written_at=row["written_at"],
            written_by=row["written_by"],
            derived_from=tuple(p["parent_id"] for p in parents),
            pack_id=row["pack_id"],
            validation=row["validation"],
        )

    def _select(
        self, space_id: str, where: str = "", args: Sequence[Any] = ()
    ) -> list[MemoryEntry]:
        clause, space_args = self._in_space(space_id)
        sql = f"SELECT * FROM entries WHERE {clause}" + (f" AND {where}" if where else "")
        with self._lock:
            rows = self._con.execute(sql + " ORDER BY kind, key", [*space_args, *args]).fetchall()
        return [self._entry(r) for r in rows]

    def _insert(
        self,
        *,
        space_id: str,
        kind: str,
        key: str,
        body: Mapping[str, Any],
        source: str,
        actor: str,
        pack_id: str | None,
        derived_from: Sequence[str],
        validation: str | None,
        scored_pack_id: str | None,
    ) -> tuple[MemoryEntry, MemoryStamp]:
        if kind not in KINDS:
            raise self._refuse(
                KIND_UNKNOWN,
                f"unknown memory kind {kind!r}",
                space_id=space_id,
                kind=kind,
                actor=actor,
            )
        if not source.strip():
            raise self._refuse(
                SOURCE_MISSING,
                "every entry needs a source",
                space_id=space_id,
                kind=kind,
                actor=actor,
            )
        self._refuse_scored_pack(
            space_id=space_id,
            kind=kind,
            actor=actor,
            pack_id=pack_id,
            source=source,
            scored_pack_id=scored_pack_id,
        )
        self._refuse_reused(
            space_id=space_id, kind=kind, actor=actor, validation=validation, source=source
        )
        if self.is_scored_round(scored_pack_id):
            raise self._refuse(
                SCORED_ROUND_WRITE,
                "nothing is written to memory during a scored round",
                space_id=space_id,
                kind=kind,
                actor=actor,
            )
        parents = tuple(dict.fromkeys(str(p) for p in derived_from))
        if parents:
            found = {
                e.id
                for e in self._select(space_id, f"id IN ({','.join('?' * len(parents))})", parents)
            }
            missing = [p for p in parents if p not in found]
            if missing:
                raise self._refuse(
                    NOT_IN_SPACE,
                    "derived_from names an entry that is not in this Space",
                    space_id=space_id,
                    kind=kind,
                    actor=actor,
                )
        with self._lock:
            existing = self._select(space_id, "kind = ? AND key = ?", (kind, key))
            entry_id = existing[0].id if existing else f"mem_{kind}_{uuid.uuid4().hex[:16]}"
            version = existing[0].version + 1 if existing else 1
            written_at = _now()
            self._con.execute(
                "INSERT INTO entries (id, space_id, kind, key, body, source, version, written_at,"
                " written_by, pack_id, validation) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (space_id, kind, key) DO UPDATE SET body = excluded.body,"
                " source = excluded.source, version = excluded.version,"
                " written_at = excluded.written_at, written_by = excluded.written_by,"
                " pack_id = excluded.pack_id, validation = excluded.validation",
                (
                    entry_id,
                    space_id,
                    kind,
                    key,
                    json.dumps(dict(body), sort_keys=True, default=str),
                    source,
                    version,
                    written_at,
                    actor,
                    pack_id,
                    validation,
                ),
            )
            self._con.execute("DELETE FROM derivations WHERE child_id = ?", (entry_id,))
            self._con.executemany(
                "INSERT INTO derivations (space_id, child_id, parent_id) VALUES (?, ?, ?)",
                [(space_id, entry_id, p) for p in parents],
            )
            self._con.commit()
        stamp = self._stamp("write", space_id, kind, (entry_id,), actor, reason=validation or "")
        return self._select(space_id, "id = ?", (entry_id,))[0], stamp

    # -- public API ---------------------------------------------------------
    def write(
        self,
        *,
        space_id: str,
        kind: str,
        key: str,
        body: Mapping[str, Any],
        source: str,
        actor: str,
        pack_id: str | None = None,
        derived_from: Sequence[str] = (),
        validation: str | None = None,
        scored_pack_id: str | None = None,
    ) -> tuple[MemoryEntry, MemoryStamp]:
        """Write a table, formula or tool entry. Solutions use confirm/check."""
        sid = self._space(space_id)
        if kind == "solution":
            self._refuse_scored_pack(
                space_id=sid,
                kind=kind,
                actor=actor,
                pack_id=pack_id,
                source=source,
                scored_pack_id=scored_pack_id,
            )
            self._refuse_reused(
                space_id=sid, kind=kind, actor=actor, validation=validation, source=source
            )
            raise self._refuse(
                SOLUTION_UNVALIDATED,
                "solutions enter memory only after steward confirm or a real-result check",
                space_id=sid,
                kind=kind,
                actor=actor,
            )
        return self._insert(
            space_id=sid,
            kind=kind,
            key=key,
            body=body,
            source=source,
            actor=actor,
            pack_id=pack_id,
            derived_from=derived_from,
            validation=validation,
            scored_pack_id=scored_pack_id,
        )

    def confirm_solution(
        self,
        *,
        space_id: str,
        question: str,
        sql: str,
        steward: Actor,
        source: str,
        pack_id: str | None = None,
        derived_from: Sequence[str] = (),
        scored_pack_id: str | None = None,
    ) -> tuple[MemoryEntry, MemoryStamp]:
        sid = self._space(space_id)
        if steward.role != STEWARD_ROLE:
            raise self._refuse(
                NOT_STEWARD,
                "only a steward confirms a solution",
                space_id=sid,
                kind="solution",
                actor=steward.actor_id,
            )
        return self._insert(
            space_id=sid,
            kind="solution",
            key=question_key(question),
            body={"question": question, "sql": sql, "confirmed_by": steward.actor_id},
            source=source,
            actor=steward.actor_id,
            pack_id=pack_id,
            derived_from=derived_from,
            validation="steward_confirm",
            scored_pack_id=scored_pack_id,
        )

    def check_solution(
        self,
        *,
        space_id: str,
        question: str,
        sql: str,
        expected_rows: Sequence[Mapping[str, Any]],
        execute: Executor,
        source: str,
        actor: str,
        pack_id: str | None = None,
        derived_from: Sequence[str] = (),
        scored_pack_id: str | None = None,
    ) -> tuple[MemoryEntry, MemoryStamp]:
        """Write a solution only if its SQL, run now, matches the real result."""
        sid = self._space(space_id)
        self._refuse_scored_pack(
            space_id=sid,
            kind="solution",
            actor=actor,
            pack_id=pack_id,
            source=source,
            scored_pack_id=scored_pack_id,
        )
        self._refuse_reused(
            space_id=sid, kind="solution", actor=actor, validation=None, source=source
        )
        if not expected_rows:
            raise self._refuse(
                REAL_RESULT_EMPTY,
                "an empty expected result cannot validate a solution",
                space_id=sid,
                kind="solution",
                actor=actor,
            )
        actual = [dict(r) for r in execute(sql)]
        if not _rows_match(actual, expected_rows):
            raise self._refuse(
                REAL_RESULT_MISMATCH,
                "SQL result does not match the real result",
                space_id=sid,
                kind="solution",
                actor=actor,
            )
        return self._insert(
            space_id=sid,
            kind="solution",
            key=question_key(question),
            body={"question": question, "sql": sql, "checked_rows": len(actual)},
            source=source,
            actor=actor,
            pack_id=pack_id,
            derived_from=derived_from,
            validation="real_result_check",
            scored_pack_id=scored_pack_id,
        )

    def read(
        self,
        *,
        space_id: str,
        kind: str,
        actor: str,
        key: str | None = None,
        scored_pack_id: str | None = None,
    ) -> tuple[list[MemoryEntry], MemoryStamp]:
        sid = self._space(space_id)
        if kind not in KINDS:
            raise self._refuse(
                KIND_UNKNOWN, f"unknown memory kind {kind!r}", space_id=sid, kind=kind, actor=actor
            )
        if self.is_scored_round(scored_pack_id) and kind not in SCORED_ROUND_READABLE:
            return [], self._stamp("read", sid, kind, (), actor, reason=SCORED_ROUND_READ)
        if key is None:
            entries = self._select(sid, "kind = ?", (kind,))
        else:
            entries = self._select(sid, "kind = ? AND key = ?", (kind, key))
        return entries, self._stamp("read", sid, kind, (e.id for e in entries), actor)

    def get(self, *, space_id: str, entry_id: str, actor: str) -> tuple[MemoryEntry, MemoryStamp]:
        sid = self._space(space_id)
        found = self._select(sid, "id = ?", (entry_id,))
        if not found:
            raise self._refuse(
                NOT_IN_SPACE, "no such entry in this Space", space_id=sid, kind=None, actor=actor
            )
        return found[0], self._stamp("read", sid, found[0].kind, (entry_id,), actor)

    def steward_view(
        self, *, space_id: str, steward: Actor, kind: str | None = None
    ) -> tuple[list[MemoryEntry], MemoryStamp]:
        sid = self._space(space_id)
        if steward.role != STEWARD_ROLE:
            raise self._refuse(
                NOT_STEWARD,
                "only a steward views memory",
                space_id=sid,
                kind=kind,
                actor=steward.actor_id,
            )
        entries = self._select(sid, "kind = ?", (kind,)) if kind else self._select(sid)
        return entries, self._stamp("view", sid, kind, (e.id for e in entries), steward.actor_id)

    def steward_delete(
        self, *, space_id: str, entry_id: str, steward: Actor
    ) -> tuple[tuple[str, ...], MemoryStamp]:
        """Delete an entry and everything derived from it (transitively)."""
        sid = self._space(space_id)
        if steward.role != STEWARD_ROLE:
            raise self._refuse(
                NOT_STEWARD,
                "only a steward deletes memory",
                space_id=sid,
                kind=None,
                actor=steward.actor_id,
            )
        if not self._select(sid, "id = ?", (entry_id,)):
            raise self._refuse(
                NOT_IN_SPACE,
                "no such entry in this Space",
                space_id=sid,
                kind=None,
                actor=steward.actor_id,
            )
        doomed: list[str] = [entry_id]
        frontier = [entry_id]
        with self._lock:
            while frontier:
                marks = ",".join("?" * len(frontier))
                children = [
                    r["child_id"]
                    for r in self._con.execute(
                        f"SELECT child_id FROM derivations WHERE space_id = ? AND parent_id IN ({marks})",
                        [sid, *frontier],
                    ).fetchall()
                ]
                frontier = [c for c in dict.fromkeys(children) if c not in doomed]
                doomed.extend(frontier)
            marks = ",".join("?" * len(doomed))
            clause, space_args = self._in_space(sid)
            self._con.execute(
                f"DELETE FROM entries WHERE {clause} AND id IN ({marks})", [*space_args, *doomed]
            )
            self._con.execute(
                f"DELETE FROM derivations WHERE space_id = ? AND (child_id IN ({marks}) OR parent_id IN ({marks}))",
                [sid, *doomed, *doomed],
            )
            self._con.commit()
        deleted = tuple(doomed)
        return deleted, self._stamp("delete", sid, None, deleted, steward.actor_id)

    def reuse_solution(
        self,
        *,
        space_id: str,
        question: str,
        execute: Executor,
        actor: str,
        scored_pack_id: str | None = None,
    ) -> SolutionReuse | None:
        """Re-run a stored solution's SQL on current data. Never serves stored rows.

        ``None`` when this Space holds no solution for the question, or when the
        round is scored (solution memory stays empty). The read is stamped either way.
        """
        found, read_stamp = self.read(
            space_id=space_id,
            kind="solution",
            key=question_key(question),
            actor=actor,
            scored_pack_id=scored_pack_id,
        )
        if not found:
            return None
        entry = found[0]
        sql = str(entry.body.get("sql") or "")
        try:
            rows: list[dict[str, Any]] | None = [dict(r) for r in execute(sql)]
            error = ""
        except Exception as exc:  # noqa: BLE001 — the ask path falls back to the engine
            rows, error = None, f"{type(exc).__name__}: {exc}"
        reuse_stamp = self._stamp(
            "reuse",
            entry.space_id,
            "solution",
            (entry.id,),
            actor,
            reason=f"{REUSE_EXEC_FAILED}: {error}" if error else "re-ran on current data",
        )
        return SolutionReuse(
            entry=entry, sql=sql, rows=rows, stamps=(read_stamp, reuse_stamp), error=error
        )


def space_memory_enabled() -> bool:
    return _truthy(os.environ.get(ENABLED_ENV))


@dataclass(slots=True)
class _Holder:
    memory: SpaceMemory | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_HOLDER = _Holder()


def get_space_memory() -> SpaceMemory:
    with _HOLDER.lock:
        if _HOLDER.memory is None:
            _HOLDER.memory = SpaceMemory(os.environ.get(PATH_ENV) or ":memory:")
        return _HOLDER.memory


def reset_space_memory_for_tests(memory: SpaceMemory | None = None) -> SpaceMemory:
    with _HOLDER.lock:
        _HOLDER.memory = memory or SpaceMemory()
        return _HOLDER.memory
