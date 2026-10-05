"""H2-DLOG-MP (#255): prove decision-log process, thread, and fork safety."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import pytest
from netie.decision import decision_log

ROOT = Path(__file__).resolve().parents[2]
PROCESSES = 4
THREADS = 8
LINES_PER_THREAD = 25
FORKS = 12
LINES_PER_CHILD = 20
CHILD_TIMEOUT_S = 15.0

# Separate interpreters plus a two-phase process gate and per-process thread
# barriers ensure all 32 writers contend on one file.
_MP_WORKER = """
import json, os, sys, threading, time
from netie.decision import decision_log
proc, threads, count, gate = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
open(gate + ".ready." + proc, "w").close()
deadline = time.monotonic() + 60
while not os.path.exists(gate):
    if time.monotonic() > deadline:
        sys.exit("start barrier never opened")
    time.sleep(0.002)
barrier = threading.Barrier(threads)
results = [None] * threads
def run(thread):
    barrier.wait(timeout=30)
    written = 0
    for index in range(count):
        entry = {
            "run_id": f"p{proc}-t{thread}",
            "node_id": str(index),
            "proc": proc,
            "thread": thread,
            "index": index,
            "pad": "x" * (index % 7) * 40,
        }
        if decision_log.append(entry):
            written += 1
        time.sleep(0.001)
    results[thread] = written
workers = [threading.Thread(target=run, args=(thread,)) for thread in range(threads)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join(timeout=120)
print(json.dumps({
    "written": results,
    "write_failures": decision_log.write_failures(),
    "last_error": decision_log.last_write_error(),
}))
"""

# Parent threads write continuously while the process forks. Each child must
# append all its lines and exit before its timeout.
_FORK_WORKER = """
import json, os, sys, threading, time
from netie.decision import decision_log
forks, per_child, timeout = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
stop = threading.Event()
def hammer(thread):
    index = 0
    while not stop.is_set():
        decision_log.append({"run_id": f"parent-t{thread}", "node_id": str(index), "index": index})
        index += 1
workers = [threading.Thread(target=hammer, args=(thread,), daemon=True) for thread in range(8)]
for worker in workers:
    worker.start()
time.sleep(0.2)
hung = []
for fork_index in range(forks):
    pid = os.fork()
    if pid == 0:
        try:
            written = 0
            for index in range(per_child):
                entry = {"run_id": f"child-{fork_index}", "node_id": str(index), "index": index}
                if decision_log.append(entry):
                    written += 1
            os._exit(0 if written == per_child else 2)
        except BaseException:
            os._exit(4)
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            break
        time.sleep(0.01)
    else:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        hung.append(fork_index)
        continue
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        print(json.dumps({"child": fork_index, "exit": code}))
        sys.exit(5)
stop.set()
for worker in workers:
    worker.join(timeout=30)
print(json.dumps({"hung": hung, "write_failures": decision_log.write_failures()}))
sys.exit(3 if hung else 0)
"""

# Force the process-local counter lock to be held at fork. The child then
# takes the failure-counting path, which hangs unless after_in_child reset it.
_HELD_LOCK_FORK_WORKER = """
import os, sys, time
from netie.decision import decision_log
timeout = float(sys.argv[1])
decision_log.reset_write_failures()
decision_log._lock.acquire()
pid = os.fork()
if pid == 0:
    try:
        ok = decision_log.append({"run_id": "child", "node_id": "0"})
        if ok or decision_log.write_failures() != 1 or decision_log.last_write_error() is None:
            os._exit(2)
        decision_log.reset_write_failures()
        os._exit(0 if decision_log.write_failures() == 0 else 3)
    except BaseException:
        os._exit(4)
deadline = time.monotonic() + timeout
status = None
while time.monotonic() < deadline:
    waited_pid, status = os.waitpid(pid, os.WNOHANG)
    if waited_pid == pid:
        break
    time.sleep(0.01)
else:
    os.kill(pid, 9)
    os.waitpid(pid, 0)
    print("child HUNG on the lock inherited across fork")
    sys.exit(3)
code = os.waitstatus_to_exitcode(status)
print("child exit %d" % code)
sys.exit(code)
"""


def _env(log: Path) -> dict[str, str]:
    env = dict(os.environ)
    env[decision_log.PATH_ENV] = str(log)
    env.pop(decision_log.ENABLE_ENV, None)
    env["PYTHONUTF8"] = "1"
    return env


def _read_lines(log: Path) -> list[dict]:
    assert log.exists(), f"decision log not written at {log}"
    raw = log.read_bytes()
    assert raw.endswith(b"\n"), "last line is torn (no trailing newline)"
    entries = []
    for line_number, line in enumerate(raw.decode("utf-8").split("\n")[:-1], start=1):
        assert line, f"empty line {line_number}: torn or doubled newline"
        try:
            entry = json.loads(line)
        except ValueError as exc:
            pytest.fail(f"line {line_number} is invalid JSON ({exc}): {line[:120]!r}")
        assert isinstance(entry, dict), f"line {line_number} is not an object"
        entries.append(entry)
    return entries


def test_four_processes_eight_threads_each_write_every_line_whole(tmp_path: Path):
    log = tmp_path / "engine" / "tier_decisions.jsonl"
    gate = tmp_path / "go"
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _MP_WORKER,
                str(process_index),
                str(THREADS),
                str(LINES_PER_THREAD),
                str(gate),
            ],
            cwd=str(ROOT),
            env=_env(log),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for process_index in range(PROCESSES)
    ]

    deadline = time.monotonic() + 120
    while not all((tmp_path / f"go.ready.{index}").exists() for index in range(PROCESSES)):
        assert time.monotonic() < deadline, "workers never signalled ready"
        assert all(process.poll() is None for process in processes), "a worker exited before the barrier"
        time.sleep(0.01)
    gate.touch()

    results = [process.communicate(timeout=300) for process in processes]
    failed = [
        (process.args[3], process.returncode, stderr)
        for process, (_, stderr) in zip(processes, results, strict=True)
        if process.returncode != 0
    ]
    assert not failed, "worker(s) failed:\n" + "\n".join(
        f"[proc {worker}] rc={returncode}\n{stderr}" for worker, returncode, stderr in failed
    )
    for process_index, (stdout, _) in enumerate(results):
        report = json.loads(stdout.strip().splitlines()[-1])
        assert report["written"] == [LINES_PER_THREAD] * THREADS, f"proc {process_index}: {report}"
        assert report["write_failures"] == 0, f"proc {process_index}: {report}"

    entries = _read_lines(log)
    total = PROCESSES * THREADS * LINES_PER_THREAD
    assert len(entries) == total
    expected = {
        f"p{process_index}-t{thread_index}": LINES_PER_THREAD
        for process_index in range(PROCESSES)
        for thread_index in range(THREADS)
    }
    assert Counter(entry["run_id"] for entry in entries) == expected
    seen = Counter((entry["run_id"], entry["node_id"]) for entry in entries)
    assert len(seen) == total
    assert all(count == 1 for count in seen.values())

    process_switches = sum(
        first["proc"] != second["proc"] for first, second in zip(entries[:-1], entries[1:], strict=True)
    )
    writer_switches = sum(
        first["run_id"] != second["run_id"] for first, second in zip(entries[:-1], entries[1:], strict=True)
    )
    assert process_switches >= 20, f"only {process_switches} process switches; processes did not contend"
    assert writer_switches >= 100, f"only {writer_switches} writer switches; threads did not contend"


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "register_at_fork"),
    reason=(
        "H2-DLOG-MP fork-while-writing proof requires POSIX os.fork/os.register_at_fork; "
        "fork safety is NOT verified on this platform"
    ),
)
def test_fork_while_threads_write_child_finishes_its_appends(tmp_path: Path):
    log = tmp_path / "engine" / "tier_decisions.jsonl"
    process = subprocess.run(
        [sys.executable, "-c", _FORK_WORKER, str(FORKS), str(LINES_PER_CHILD), str(CHILD_TIMEOUT_S)],
        cwd=str(ROOT),
        env=_env(log),
        capture_output=True,
        text=True,
        timeout=FORKS * CHILD_TIMEOUT_S + 120,
    )
    assert process.returncode == 0, (
        f"rc={process.returncode}: a forked child hung or failed\n"
        f"stdout: {process.stdout}\nstderr: {process.stderr}"
    )
    report = json.loads(process.stdout.strip().splitlines()[-1])
    assert report["hung"] == []
    assert report["write_failures"] == 0

    entries = _read_lines(log)
    per_child = Counter(entry["run_id"] for entry in entries if entry["run_id"].startswith("child-"))
    assert per_child == {f"child-{fork_index}": LINES_PER_CHILD for fork_index in range(FORKS)}
    parent_lines = sum(entry["run_id"].startswith("parent-") for entry in entries)
    assert parent_lines >= FORKS * LINES_PER_CHILD
    first_child = next(index for index, entry in enumerate(entries) if entry["run_id"].startswith("child-"))
    assert any(entry["run_id"].startswith("parent-") for entry in entries[first_child:])


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "register_at_fork"),
    reason=(
        "H2-DLOG-MP held-lock fork proof requires POSIX os.fork/os.register_at_fork; "
        "the after_in_child reset is NOT verified on this platform"
    ),
)
def test_child_forked_while_lock_is_held_gets_a_fresh_lock(tmp_path: Path):
    blocker = tmp_path / "engine" / "tier_decisions.jsonl"
    blocker.mkdir(parents=True)
    process = subprocess.run(
        [sys.executable, "-c", _HELD_LOCK_FORK_WORKER, str(CHILD_TIMEOUT_S)],
        cwd=str(ROOT),
        env=_env(blocker),
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_S + 60,
    )
    assert process.returncode == 0, (
        f"rc={process.returncode}: child did not finish with a fresh lock\n"
        f"stdout: {process.stdout}\nstderr: {process.stderr}"
    )
    assert process.stdout.strip().splitlines()[-1] == "child exit 0"
    assert blocker.is_dir() and not any(blocker.iterdir())


def test_threads_never_raise_and_count_every_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    blocker = tmp_path / "engine" / "tier_decisions.jsonl"
    blocker.mkdir(parents=True)
    monkeypatch.setenv(decision_log.PATH_ENV, str(blocker))
    monkeypatch.delenv(decision_log.ENABLE_ENV, raising=False)
    decision_log.reset_write_failures()
    try:
        raised: list[BaseException] = []
        returned: list[bool] = []
        barrier = threading.Barrier(THREADS)

        def run(thread_index: int) -> None:
            barrier.wait(timeout=30)
            for line_index in range(LINES_PER_THREAD):
                try:
                    returned.append(
                        decision_log.append({"run_id": f"t{thread_index}", "node_id": str(line_index)})
                    )
                except BaseException as exc:
                    raised.append(exc)

        workers = [threading.Thread(target=run, args=(index,)) for index in range(THREADS)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=120)

        assert not raised
        assert returned == [False] * (THREADS * LINES_PER_THREAD)
        assert decision_log.write_failures() == THREADS * LINES_PER_THREAD
        assert decision_log.last_write_error() is not None
    finally:
        decision_log.reset_write_failures()
