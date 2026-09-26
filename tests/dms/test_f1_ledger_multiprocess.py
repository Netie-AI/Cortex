"""GH-04 (#244): the SQLite ledger is proven under real multi-process writers.

The thread-based test in test_f1_ledger.py is satisfied by the module-level
threading.Lock alone; nothing there exercises BEGIN IMMEDIATE or the busy
timeout across OS processes. These tests spawn separate interpreters so the
only serialisation left is SQLite's own file lock.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROCESSES = 4
ENTRIES_PER_PROCESS = 25

# Each worker appends through the public ledger.append API; it never touches
# sqlite3 directly. Started as `python -c` so it is spawn-safe on Windows and
# does not require the test module to be importable by name.
_WORKER = """
import os, sys, time
from packs.dms.audit.ledger import append
db, worker, n, go = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
deadline = time.monotonic() + 60
while not os.path.exists(go):  # start barrier: all workers append at once
    if time.monotonic() > deadline:
        sys.exit("start barrier never opened")
    time.sleep(0.005)
for i in range(n):
    append(f"proc-{worker}", "multiprocess.event", {"worker": worker, "i": i}, db_path=db)
    time.sleep(0.001)  # yield so the file lock changes hands between appends
"""


def _spawn_workers(db: Path, count: int, per_worker: int) -> list[subprocess.CompletedProcess[str]]:
    env = dict(os.environ)
    env.pop("DMS_LEDGER_DSN", None)  # force the SQLite path in every child
    env["DMS_OPS_DB"] = str(db)
    env["PYTHONUTF8"] = "1"
    go = db.with_name(db.name + ".go")
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(db), str(w), str(per_worker), str(go)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for w in range(count)
    ]
    # Open the barrier only after every interpreter has started, so the
    # appends genuinely contend on SQLite's file lock instead of running in
    # the staggered order process start-up would give them.
    go.touch()
    results = []
    for p in procs:
        out, err = p.communicate(timeout=300)
        results.append(subprocess.CompletedProcess(p.args, p.returncode, out, err))
    return results


def test_connect_sets_busy_timeout_of_at_least_30s(tmp_path, monkeypatch):
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    from packs.dms.audit.ledger import SQLITE_BUSY_TIMEOUT_SECONDS, _connect

    assert SQLITE_BUSY_TIMEOUT_SECONDS >= 30
    con = _connect(tmp_path / "ledger.db")
    try:
        ms = con.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        con.close()
    assert ms >= 30_000, f"busy_timeout is {ms} ms; cross-process writers would fail early"


def test_four_processes_append_25_each_yield_gap_free_verified_chain(tmp_path, monkeypatch):
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    from packs.dms.audit.ledger import list_entries, verify

    db = tmp_path / "ledger.db"
    results = _spawn_workers(db, PROCESSES, ENTRIES_PER_PROCESS)
    failed = [r for r in results if r.returncode != 0]
    assert not failed, "worker(s) failed:\n" + "\n".join(
        f"[{r.args[-2]}] rc={r.returncode}\n{r.stderr}" for r in failed
    )

    # User-visible artifact 1: verify() over the file the workers wrote.
    result = verify(db_path=db)
    assert result.ok is True, f"chain broken at seq {result.broken_at}"
    assert result.broken_at is None

    # User-visible artifact 2: the rows themselves.
    total = PROCESSES * ENTRIES_PER_PROCESS
    entries = list_entries(db_path=db, limit=total + 10)
    assert len(entries) == total
    assert [e.seq for e in entries] == list(range(total)), "seq must be exactly 0..99"
    assert len({e.seq for e in entries}) == total, "duplicate seq"
    assert len({e.id for e in entries}) == total, "duplicate id"
    assert len({e.entry_hash for e in entries}) == total, "duplicate entry_hash"

    from packs.dms.audit.ledger import GENESIS_HASH

    assert entries[0].prev_hash == GENESIS_HASH
    for prev, cur in zip(entries[:-1], entries[1:], strict=True):
        assert cur.prev_hash == prev.entry_hash, f"prev_hash mismatch at seq {cur.seq}"

    # Every process landed all of its entries; none were silently dropped.
    per_worker = {str(w): 0 for w in range(PROCESSES)}
    for e in entries:
        assert e.event_type == "multiprocess.event"
        per_worker[e.payload["worker"]] += 1
    assert per_worker == {str(w): ENTRIES_PER_PROCESS for w in range(PROCESSES)}

    # The proof only means something if the writers actually contended: the
    # rows must interleave across processes, not land as four serial blocks.
    switches = sum(
        1 for a, b in zip(entries[:-1], entries[1:], strict=True) if a.payload["worker"] != b.payload["worker"]
    )
    assert switches >= 10, f"only {switches} worker switches in {total} rows; writers did not contend"
