"""FREEROUTE-CONNECT-01: ``_connect`` yields once and the original error surfaces.

Refs #365. On 0faede64 a sqlite error after the yield becomes
``RuntimeError: generator didn't stop after throw()`` (a commit error becomes
``RuntimeError: generator didn't stop``).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from CortexOS.integrations import freeroute as fr

_INSERT = (
    "INSERT OR REPLACE INTO routes ("
    "call_id, task, requested, served, status, usable, scored, verdict, "
    "latency_ms, impl, shadow, ts"
    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _row(call_id: str) -> tuple[object, ...]:
    return (call_id, "t", "m", "m", 200, 1, 0, None, 1.0, "impl", 0, 1.0)


def _assert_closed(con: sqlite3.Connection | _CommitLocked) -> None:
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")


def _second_write(call_id: str) -> None:
    """Write from another thread. Fails if the store lock or connection leaked."""
    box: list[BaseException] = []

    def run() -> None:
        if not fr._store_lock.acquire(timeout=0.5):
            box.append(AssertionError("_store_lock still held after the error"))
            return
        fr._store_lock.release()
        try:
            with fr._connect(write=True) as con:
                if con is None:
                    raise AssertionError("follow-up write found the store unavailable")
                con.execute(_INSERT, _row(call_id))
        except BaseException as exc:
            box.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(6.0)
    assert not thread.is_alive(), "_store_lock still held; follow-up write blocked"
    if box:
        raise box[0]
    check = sqlite3.connect(str(fr.store_path()))
    try:
        got = check.execute(
            "SELECT call_id FROM routes WHERE call_id = ?", (call_id,)
        ).fetchone()
    finally:
        check.close()
    assert got == (call_id,)


class _CommitLocked:
    """Real connection whose ``commit`` raises ``database is locked``."""

    def __init__(self, con: sqlite3.Connection, exc: sqlite3.OperationalError) -> None:
        self._con = con
        self._exc = exc

    def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        return self._con.execute(*args, **kwargs)

    def commit(self) -> None:
        raise self._exc

    def close(self) -> None:
        self._con.close()


def test_write_error_inside_block_surfaces_original() -> None:
    err = sqlite3.OperationalError("write failed")
    held: list[sqlite3.Connection] = []
    with pytest.raises(sqlite3.OperationalError, match="write failed") as caught:
        with fr._connect(write=True) as con:
            assert con is not None
            held.append(con)
            raise err
    assert caught.value is err
    _assert_closed(held[0])
    _second_write("after-write-error")


def test_locked_store_write_surfaces_original(monkeypatch: pytest.MonkeyPatch) -> None:
    with fr._connect(write=True) as con:
        assert con is not None
    path = fr.store_path()
    locker = sqlite3.connect(str(path), timeout=0.05)
    locker.execute("BEGIN EXCLUSIVE")
    real_connect = sqlite3.connect

    def fast_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        if "timeout" in kwargs:
            kwargs["timeout"] = 0.05
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", fast_connect)
    held: list[sqlite3.Connection] = []
    try:
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            with fr._connect(write=True) as con:
                assert con is not None
                held.append(con)
                con.execute(_INSERT, _row("locked"))
    finally:
        locker.rollback()
        locker.close()
    _assert_closed(held[0])
    monkeypatch.setattr(sqlite3, "connect", real_connect)
    _second_write("after-locked")


def test_commit_error_notes_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    noted: list[BaseException] = []
    real_note = fr._note_store_error

    def spy(exc: BaseException) -> None:
        noted.append(exc)
        real_note(exc)

    monkeypatch.setattr(fr, "_note_store_error", spy)
    exc = sqlite3.OperationalError("database is locked")
    real_open = fr._open_for_write

    def open_then_fail_commit(path: Path) -> _CommitLocked:
        return _CommitLocked(real_open(path), exc)

    monkeypatch.setattr(fr, "_open_for_write", open_then_fail_commit)
    held: list[_CommitLocked] = []
    with pytest.raises(sqlite3.OperationalError, match="database is locked") as caught:
        with fr._connect(write=True) as con:
            assert con is not None
            held.append(con)
    assert caught.value is exc
    assert noted == [exc]
    assert "database is locked" in fr.store_error()
    _assert_closed(held[0])
    monkeypatch.setattr(fr, "_open_for_write", real_open)
    _second_write("after-commit-error")


def test_open_failure_yields_none_once(monkeypatch: pytest.MonkeyPatch) -> None:
    real_open = fr._open_for_write

    def boom(path: Path) -> sqlite3.Connection:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(fr, "_open_for_write", boom)
    seen: list[sqlite3.Connection | None] = []
    with fr._connect(write=True) as con:
        seen.append(con)
    assert seen == [None]
    assert "unable to open database file" in fr.store_error()
    monkeypatch.setattr(fr, "_open_for_write", real_open)
    _second_write("after-open-failure")
