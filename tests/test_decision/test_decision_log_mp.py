"""H2-DLOG-MP (#255): the decision log is proven under processes, threads and fork.

KEV-LOG (#246) assumed multi-process appends were safe and never tested them;
its verifier found that forking while writer threads held the module lock
deadlocked the children. Every test here reads the JSONL file back as the
operator would: the line count, ``json.loads`` on every line and the count per
writer are the artifact. Workers run as separate interpreters started with
``python -c`` so the only thing serialising the writes is the kernel's
O_APPEND semantics, not a lock inherited from pytest.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
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

# Each writer appends through the public append() API. A file barrier makes
# every interpreter and every thread start at once so the writers contend.
_MP_WORKER = """
import json, os, sys, threading, time
from netie.decision import decision_log
proc, threads, n, go = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
deadline = time.monotonic() + 60
while not os.path.exists(go):
    if time.monotonic() > deadline:
        sys.exit("start barrier never opened")
    time.sleep(0.002)
barrier = threading.Barrier(threads)
results = [None] * threads
def run(t):
    barrier.wait(timeout=30)
    ok = 0
    for i in range(n):
        if decision_log.append({"run_id": f"p{proc}-t{t}", "node_id": str(i), "proc": proc, "thread": t, "i": i,
                                "pad": "x" * (i % 7) * 40}):
            ok += 1
    results[t] = ok
ts = [threading.Thread(target=run, args=(t,)) for t in range(threads)]
for th in ts: th.start()
for th in ts: th.join(timeout=120)
print(json.dumps({"written": results, "write_failures": decision_log.write_failures(),
                  "last_error": decision_log.last_write_error()}))
"""

# Writer threads hammer append() while the main thread forks repeatedly. Each
# child must finish its own appends and exit; the parent waits with a timeout
# and reports a hung child as a non-zero exit instead of hanging pytest.
_FORK_WORKER = """
import json, os, sys, threading, time
from netie.decision import decision_log
forks, per_child, timeout = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
stop = threading.Event()
def hammer(t):
    i = 0
    while not stop.is_set():
        decision_log.append({"run_id": f"parent-t{t}", "node_id": str(i), "i": i})
        i += 1
ts = [threading.Thread(target=hammer, args=(t,), daemon=True) for t in range(8)]
for th in ts: th.start()
time.sleep(0.2)  # let the writers reach steady state before the first fork
hung = []
for k in range(forks):
    pid = os.fork()
    if pid == 0:
        try:
            ok = 0
            for i in range(per_child):
                if decision_log.append({"run_id": f"child-{k}", "node_id": str(i), "i": i}):
                    ok += 1
            os._exit(0 if ok == per_child else 2)
        except BaseException:
            os._exit(4)
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        wpid, status = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            break
        time.sleep(0.01)
    else:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        hung.append(k)
        continue
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        print(json.dumps({"child": k, "exit": code}))
        sys.exit(5)
stop.set()
for th in ts: th.join(timeout=30)
print(json.dumps({"hung": hung, "write_failures": decision_log.write_failures()}))
sys.exit(3 if hung else 0)
"""


# The parent holds the module lock at fork time, standing in for a writer
# thread that is inside the failure-counting critical section when another
# thread forks. The log path is a directory so every append fails and must
# take the lock to count the failure. Without the os.register_at_fork reset
# the child inherits a lock nobody in the child can ever release, and its
# first append() blocks forever. Exit codes: 0 fresh lock and counters work;
# 2 the failure was not counted; 3 reset did not clear it; 4 append raised.
_HELD_LOCK_FORK_WORKER = """
import os, sys, time
from netie.decision import decision_log
timeout = float(sys.argv[1])
decision_log.reset_write_failures()
decision_log._lock.acquire()  # a writer thread is inside the counter section at fork time
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
    wpid, status = os.waitpid(pid, os.WNOHANG)
    if wpid == pid:
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
    out = []
    for n, line in enumerate(raw.decode("utf-8").split("\n")[:-1], start=1):
        assert line, f"empty line {n}: a torn or doubled newline"
        try:
            obj = json.loads(line)
        except ValueError as exc:
            pytest.fail(f"line {n} is not valid JSON ({exc}): {line[:120]!r}")
        assert isinstance(obj, dict), f"line {n} is not an object"
        out.append(obj)
    return out


def test_four_processes_eight_threads_each_write_every_line_whole(tmp_path: Path):
    log = tmp_path / "engine" / "tier_decisions.jsonl"
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _MP_WORKER, str(p), str(THREADS), str(LINES_PER_THREAD), str(go)],
            cwd=str(ROOT),
            env=_env(log),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for p in range(PROCESSES)
    ]
    go.touch()  # start barrier: every interpreter is up, now they all append at once
    results = [p.communicate(timeout=300) for p in procs]
    failed = [(p.args[3], p.returncode, err) for p, (_, err) in zip(procs, results, strict=True) if p.returncode != 0]
    assert not failed, "worker(s) failed:\n" + "\n".join(f"[proc {w}] rc={rc}\n{err}" for w, rc, err in failed)

    # Every writer reported every append as written and counted no failures.
    for (out, _), p in zip(results, range(PROCESSES), strict=True):
        report = json.loads(out.strip().splitlines()[-1])
        assert report["written"] == [LINES_PER_THREAD] * THREADS, f"proc {p}: {report}"
        assert report["write_failures"] == 0, f"proc {p}: {report}"

    # The artifact: the file the operator reads back.
    entries = _read_lines(log)
    total = PROCESSES * THREADS * LINES_PER_THREAD
    assert len(entries) == total, f"{len(entries)} lines, expected {total}"
    per_writer = Counter(e["run_id"] for e in entries)
    expected = {f"p{p}-t{t}": LINES_PER_THREAD for p in range(PROCESSES) for t in range(THREADS)}
    assert per_writer == expected
    # Within one writer every index lands exactly once: nothing dropped, nothing doubled.
    seen = Counter((e["run_id"], e["node_id"]) for e in entries)
    assert all(c == 1 for c in seen.values()), "duplicate (writer, index) pairs"
    assert len(seen) == total

    # The proof only means something if the writers really contended: lines
    # must interleave across processes and across threads, not land as
    # serial blocks. 800 lines from 32 writers landing serially would be
    # exactly 31 switches; contention gives hundreds.
    proc_switches = sum(1 for a, b in zip(entries[:-1], entries[1:], strict=True) if a["proc"] != b["proc"])
    writer_switches = sum(1 for a, b in zip(entries[:-1], entries[1:], strict=True) if a["run_id"] != b["run_id"])
    assert proc_switches >= 20, f"only {proc_switches} process switches in {total} lines; processes did not contend"
    assert writer_switches >= 100, f"only {writer_switches} writer switches in {total} lines; threads did not contend"


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "register_at_fork"),
    reason="H2-DLOG-MP fork-while-writing test needs POSIX os.fork/os.register_at_fork; "
    "the fork-safety guarantee is NOT verified on this platform",
)
def test_fork_while_threads_write_child_finishes_its_appends(tmp_path: Path):
    log = tmp_path / "engine" / "tier_decisions.jsonl"
    proc = subprocess.run(
        [sys.executable, "-c", _FORK_WORKER, str(FORKS), str(LINES_PER_CHILD), str(CHILD_TIMEOUT_S)],
        cwd=str(ROOT),
        env=_env(log),
        capture_output=True,
        text=True,
        timeout=FORKS * CHILD_TIMEOUT_S + 120,
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode}: forked child(ren) hung or failed on an inherited lock\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["hung"] == []
    assert report["write_failures"] == 0

    entries = _read_lines(log)
    per_child = Counter(e["run_id"] for e in entries if e["run_id"].startswith("child-"))
    assert per_child == {f"child-{k}": LINES_PER_CHILD for k in range(FORKS)}, per_child
    parent_lines = sum(1 for e in entries if e["run_id"].startswith("parent-"))
    assert parent_lines >= FORKS * LINES_PER_CHILD, "parent writer threads were not writing during the forks"
    # The children wrote while the parent's threads wrote: their lines sit
    # between parent lines, not in a block after them.
    first_child = next(i for i, e in enumerate(entries) if e["run_id"].startswith("child-"))
    assert any(e["run_id"].startswith("parent-") for e in entries[first_child:]), (
        "no parent line after the first child line: the fork did not race the writers"
    )


@pytest.mark.skipif(
    not hasattr(os, "fork") or not hasattr(os, "register_at_fork"),
    reason="H2-DLOG-MP held-lock fork test needs POSIX os.fork/os.register_at_fork; "
    "the after_in_child lock reset is NOT verified on this platform",
)
def test_child_forked_while_lock_is_held_gets_a_fresh_lock_and_working_counters(tmp_path: Path):
    """Acceptance line 1: the child resets the lock via os.register_at_fork.

    The other tests never take the lock on the write path any more, so they
    pass with the at-fork hook deleted. This one forks with the lock held and
    forces the child onto the only remaining locked path (failure counting);
    the child's exit code is the served result. Deleting the hook hangs it.
    """
    blocker = tmp_path / "engine" / "tier_decisions.jsonl"
    blocker.mkdir(parents=True)  # a directory where the file must be: every append fails
    proc = subprocess.run(
        [sys.executable, "-c", _HELD_LOCK_FORK_WORKER, str(CHILD_TIMEOUT_S)],
        cwd=str(ROOT),
        env=_env(blocker),
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_S + 60,
    )
    assert proc.returncode == 0, (
        f"rc={proc.returncode}: child forked while the log lock was held did not finish "
        f"(register_at_fork reset missing or counters broken)\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert proc.stdout.strip().splitlines()[-1] == "child exit 0", proc.stdout
    assert blocker.is_dir() and not any(blocker.iterdir()), "nothing may be written under the blocking directory"


def test_eight_threads_on_an_unwritable_path_never_raise_and_every_failure_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    blocker = tmp_path / "engine"
    blocker.mkdir()
    (blocker / "tier_decisions.jsonl").mkdir()  # a directory where the file must be
    monkeypatch.setenv(decision_log.PATH_ENV, str(blocker / "tier_decisions.jsonl"))
    monkeypatch.delenv(decision_log.ENABLE_ENV, raising=False)
    decision_log.reset_write_failures()
    try:
        raised: list[BaseException] = []
        returned: list[bool] = []
        barrier = threading.Barrier(THREADS)

        def run(t: int) -> None:
            barrier.wait(timeout=30)
            for i in range(LINES_PER_THREAD):
                try:
                    returned.append(decision_log.append({"run_id": f"t{t}", "node_id": str(i)}))
                except BaseException as exc:  # the contract: append never raises
                    raised.append(exc)

        ts = [threading.Thread(target=run, args=(t,)) for t in range(THREADS)]
        for th in ts:
            th.start()
        for th in ts:
            th.join(timeout=120)
        assert not raised, raised
        assert returned == [False] * (THREADS * LINES_PER_THREAD)
        assert decision_log.write_failures() == THREADS * LINES_PER_THREAD
        assert decision_log.last_write_error() is not None
    finally:
        decision_log.reset_write_failures()
