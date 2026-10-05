"""Cross-process SQLite concurrency proof for the F1 ledger."""

from __future__ import annotations

import multiprocessing
import os
from itertools import pairwise
from pathlib import Path
from queue import Empty
from typing import Any

from packs.dms.audit import ledger

PROCESS_COUNT = 4
ENTRIES_PER_PROCESS = 25
EXPECTED_ENTRIES = PROCESS_COUNT * ENTRIES_PER_PROCESS


def _append_batch(
    db_path: str,
    worker_id: int,
    start_event: Any,
    result_queue: Any,
) -> None:
    if not start_event.wait(timeout=30):
        raise RuntimeError("multiprocess ledger start timed out")

    try:
        for item_id in range(ENTRIES_PER_PROCESS):
            ledger.append(
                f"worker-{worker_id}",
                "multiprocess.append",
                {"worker_id": worker_id, "item_id": item_id},
                db_path=db_path,
            )
    except BaseException as exc:
        result_queue.put((worker_id, os.getpid(), f"{type(exc).__name__}: {exc}"))
        raise
    else:
        result_queue.put((worker_id, os.getpid(), None))


def test_connect_sets_busy_timeout_of_at_least_30s(tmp_path: Path) -> None:
    con = ledger._connect(tmp_path / "busy-timeout.db")
    try:
        busy_timeout_ms = con.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        con.close()

    assert busy_timeout_ms >= 30_000


def test_four_processes_append_25_each_yield_gap_free_verified_chain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    db_path = tmp_path / "multiprocess-ledger.db"
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_append_batch,
            args=(str(db_path), worker_id, start_event, result_queue),
        )
        for worker_id in range(PROCESS_COUNT)
    ]

    for process in processes:
        process.start()
    start_event.set()

    for process in processes:
        process.join(timeout=60)

    stuck = [process for process in processes if process.is_alive()]
    for process in stuck:
        process.terminate()
        process.join()
    assert not stuck, "ledger append processes did not finish within 60 seconds"
    assert [process.exitcode for process in processes] == [0] * PROCESS_COUNT

    results = []
    try:
        for _ in range(PROCESS_COUNT):
            results.append(result_queue.get(timeout=5))
    except Empty:
        raise AssertionError("a ledger append process returned no result") from None
    finally:
        result_queue.close()
        result_queue.join_thread()

    assert {worker_id for worker_id, _, _ in results} == set(range(PROCESS_COUNT))
    assert len({pid for _, pid, _ in results}) == PROCESS_COUNT
    assert [error for _, _, error in results if error] == []

    verification = ledger.verify(db_path=db_path)
    assert verification.ok is True
    assert verification.broken_at is None

    entries = ledger.list_entries(db_path=db_path, limit=EXPECTED_ENTRIES + 1)
    assert len(entries) == EXPECTED_ENTRIES
    assert [entry.seq for entry in entries] == list(range(EXPECTED_ENTRIES))
    assert entries[0].prev_hash == ledger.GENESIS_HASH
    assert all(
        current.prev_hash == previous.entry_hash
        for previous, current in pairwise(entries)
    )
