"""H2-SHADOW-ASYNC (#253): shadow evaluation off the serving path, bounded, counted.

Every test asserts what a caller or operator sees: the served ``JudgmentDecision``
and how long it took, the JSONL shadow file read back, and the drop and failure
counters. A slow kev must not slow the served decision; a full queue must drop
and count, never block or raise; ``flush`` and process exit must drain.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from netie.decision import shadow
from netie.decision.models import RawDecision
from netie.decision.shadow import ShadowEvaluator, ShadowWorker, read_rows, summary
from netie.routing.judgment_model import JudgmentDecision, JudgmentModel, JudgmentRequest

REPO_ROOT = Path(__file__).resolve().parents[2]
SECRET = "PLANTED-ASYNC-SECRET-7f3a1b-never-logged"


@pytest.fixture
def shadow_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "tier_shadow.jsonl"
    monkeypatch.setenv(shadow.PATH_ENV, str(path))
    monkeypatch.setenv("CORTEX_DECISION_LOG_PATH", str(tmp_path / "tier_decisions.jsonl"))
    shadow.reset_write_failures()
    shadow.reset_dropped()
    yield path
    # Nothing from this test may still be running when the next one starts.
    assert shadow.flush(30.0) is True
    shadow.reset_dropped()
    shadow.reset_write_failures()


def _same(a: JudgmentDecision, b: JudgmentDecision) -> bool:
    return (a.tier, a.confidence, a.reason) == (b.tier, b.confidence, b.reason)


class _Backend:
    """Answers ``choice`` by label. ``delay`` sleeps per call; ``gate`` blocks
    every call until it is set; ``started`` is set on entry to the first call."""

    name = "async-mock"

    def __init__(self, choice: str = "T3", *, delay: float = 0.0, gate: threading.Event | None = None) -> None:
        self.choice = choice
        self.delay = delay
        self.gate = gate
        self.calls = 0
        self.started = threading.Event()
        self._lock = threading.Lock()

    def evaluate(self, question, state):
        with self._lock:
            self.calls += 1
        self.started.set()
        if self.gate is not None:
            self.gate.wait()
        if self.delay:
            time.sleep(self.delay)
        scores = tuple(0.91 if label == self.choice else 0.03 for label in question.labels)
        return RawDecision(scores=scores, is_logits=False, calibrated=True, backend=self.name)


def _decide(jm: JudgmentModel, content: str = "hello") -> tuple[JudgmentDecision, float]:
    req = JudgmentRequest(request_type="chat", content=content)
    t0 = time.perf_counter()
    served = jm.decide(req)
    elapsed = time.perf_counter() - t0
    assert _same(served, JudgmentModel().rules_decide(req)), "shadow changed the served decision"
    return served, elapsed


# --- acceptance: a slow kev never delays the served decision ------------------


def test_slow_kev_does_not_delay_served_decision(shadow_file):
    """kev sleeps 2 s per call (4 s per observation with check_order). The
    served decision returns in under 0.2 s and equals ``rules_decide``; after
    ``flush`` the row is on disk."""
    backend = _Backend("T3", delay=2.0)
    jm = JudgmentModel(shadow=ShadowEvaluator(backend))
    served, elapsed = _decide(jm)
    assert elapsed < 0.2, f"decide() waited on kev: {elapsed:.3f}s"
    assert served.reason == "heuristic routing fallback"
    assert "kev" not in served.reason
    assert not shadow_file.exists(), "row must not be written before kev answered"

    assert shadow.flush(timeout=10.0) is True
    rows = read_rows(shadow_file)
    assert len(rows) == 1
    assert rows[0]["rules_tier"] == "T1"
    assert rows[0]["kev_choice"] == "T3"
    assert rows[0]["agree"] is False
    assert rows[0]["degraded"] is False
    assert backend.calls == 2, "check_order still asks twice, off the serving path"
    assert jm.shadow.observations == 1
    assert shadow.dropped() == 0
    assert "dropped" not in summary(shadow_file)


def test_healthy_kev_row_is_on_disk_before_the_decision_returns(shadow_file):
    """The grace: an idle worker and a fast kev put the row on disk in decision
    order before the decision is served, so a raw read after ``decide`` sees it."""
    jm = JudgmentModel(shadow=ShadowEvaluator(_Backend("T1")))
    for content in ("one", "two", "three"):
        _, elapsed = _decide(jm, content)
        assert elapsed < 0.2
    lines = shadow_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["agree"] for line in lines] == [True, True, True]


def test_grace_is_paid_at_most_once_then_never_behind_a_backlog(shadow_file):
    gate = threading.Event()
    backend = _Backend("T3", gate=gate)
    jm = JudgmentModel(shadow=ShadowEvaluator(backend, grace=0.1))
    try:
        _, first = _decide(jm, "first")
        assert 0.08 <= first < 0.2, f"idle worker: wait the grace, no more ({first:.3f}s)"
        for i in range(5):
            _, later = _decide(jm, f"later {i}")
            assert later < 0.05, f"behind a backlog nothing waits ({later:.3f}s)"
        assert not shadow_file.exists()
    finally:
        gate.set()
    assert shadow.flush(10.0) is True
    assert len(read_rows(shadow_file)) == 6


# --- acceptance: a full queue drops, counts, never blocks, never raises --------


def test_full_queue_drops_and_counts_without_blocking(shadow_file):
    gate = threading.Event()
    backend = _Backend("T3", gate=gate)
    worker = ShadowWorker(maxsize=2)
    ev = ShadowEvaluator(backend, worker=worker, grace=0.0)
    jm = JudgmentModel(shadow=ev)
    try:
        _decide(jm, "in flight")
        assert backend.started.wait(5.0), "worker never picked up the first observation"
        t0 = time.perf_counter()
        for i in range(4):
            _decide(jm, f"queued or dropped {i}")
        assert time.perf_counter() - t0 < 0.2, "a full queue must not block the caller"
        assert ev.dropped == 2, "queue of 2 behind one in flight: 2 of 4 dropped"
        assert worker.dropped == 2
        assert shadow.dropped() == 2
        assert summary(shadow_file)["dropped"] == 2
        assert shadow.write_failures() == 0, "a drop is a drop, not a failure"
    finally:
        gate.set()
    assert ev.flush(10.0) is True
    rows = read_rows(shadow_file)
    assert len(rows) == 3
    assert len(rows) + ev.dropped == 5
    assert ev.observations == 3
    assert summary(shadow_file, min_n=1)["n"] == 3
    assert summary(shadow_file)["dropped"] == 2


def test_summary_reports_dropped_only_once_any_were_dropped(tmp_path, shadow_file):
    s = summary(tmp_path / "absent.jsonl")
    assert "dropped" not in s and s["n"] == 0
    shadow._record_drop()
    assert summary(tmp_path / "absent.jsonl")["dropped"] == 1
    shadow.reset_dropped()
    assert "dropped" not in summary(tmp_path / "absent.jsonl")


# --- acceptance: flush and exit drain ------------------------------------------


def test_read_rows_summary_and_failure_counters_flush_first(shadow_file):
    backend = _Backend("T1", delay=0.15)
    jm = JudgmentModel(shadow=ShadowEvaluator(backend, grace=0.0))
    _, elapsed = _decide(jm)
    assert elapsed < 0.1
    assert not shadow_file.exists()
    assert len(read_rows(shadow_file)) == 1, "read_rows must drain the queue before reading"

    _decide(jm, "second")
    assert summary(shadow_file, min_n=1)["n"] == 2, "summary must drain the queue before counting"


def test_write_failure_counter_flushes_first(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv(shadow.PATH_ENV, str(blocker / "tier_shadow.jsonl"))
    shadow.reset_write_failures()
    shadow.reset_dropped()
    jm = JudgmentModel(shadow=ShadowEvaluator(_Backend("T1", delay=0.15), grace=0.0))
    _, elapsed = _decide(jm)
    assert elapsed < 0.1
    assert shadow.write_failures() == 1
    assert "append" in (shadow.last_write_error() or "")
    assert blocker.read_text(encoding="utf-8") == "not a directory"


def test_flush_timeout_returns_false_and_leaves_work_pending(shadow_file):
    gate = threading.Event()
    backend = _Backend("T3", gate=gate)
    ev = ShadowEvaluator(backend, worker=ShadowWorker(maxsize=8), grace=0.0)
    jm = JudgmentModel(shadow=ev)
    try:
        _decide(jm)
        t0 = time.perf_counter()
        assert ev.flush(0.2) is False
        assert 0.15 <= time.perf_counter() - t0 < 1.0
        assert ev.worker.pending() == 1
    finally:
        gate.set()
    assert ev.flush(10.0) is True
    assert ev.worker.pending() == 0
    assert len(read_rows(shadow_file)) == 1


def test_process_exit_drains_pending_observations(tmp_path):
    """No flush call: the interpreter exits with one observation still queued
    behind a 0.3 s kev, and the row must be on disk afterwards."""
    path = tmp_path / "exit_shadow.jsonl"
    script = f"""
import os, time
os.environ[{shadow.PATH_ENV!r}] = {str(path)!r}
from CortexOS.decision.models import RawDecision
from CortexOS.decision.shadow import ShadowEvaluator
from CortexOS.routing.judgment_model import JudgmentModel, JudgmentRequest

class Slow:
    name = "slow"
    def evaluate(self, question, state):
        time.sleep(0.3)
        return RawDecision(scores=(0.91, 0.03, 0.03, 0.03), is_logits=False, calibrated=True, backend=self.name)

jm = JudgmentModel(shadow=ShadowEvaluator(Slow(), grace=0.0))
t0 = time.perf_counter()
d = jm.decide(JudgmentRequest(request_type="chat", content="bye {SECRET}"))
print("elapsed", time.perf_counter() - t0, d.reason)
assert not os.path.exists({str(path)!r})
"""
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    elapsed = float(proc.stdout.split()[1])
    assert elapsed < 0.2, proc.stdout
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert len(rows) == 1
    assert rows[0]["kev_choice"] == "T0" and rows[0]["rules_tier"] == "T1"


# --- acceptance: concurrency -------------------------------------------------------


def test_16_threads_x_50_decisions_all_resolve_and_rows_plus_drops_equal_decisions(shadow_file):
    threads_n, per_thread = 16, 50
    backend = _Backend("T1", delay=0.001)
    ev = ShadowEvaluator(backend, worker=ShadowWorker(maxsize=32), grace=0.0)
    jm = JudgmentModel(shadow=ev)
    rules = JudgmentModel()
    served: list[JudgmentDecision] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(tid: int) -> None:
        try:
            for i in range(per_thread):
                req = JudgmentRequest(request_type="chat", content=f"t{tid} i{i} {SECRET}")
                d = jm.decide(req)
                assert _same(d, rules.rules_decide(req))
                with lock:
                    served.append(d)
        except BaseException as exc:  # pragma: no cover - reported below
            with lock:
                errors.append(exc)

    workers = [threading.Thread(target=run, args=(t,)) for t in range(threads_n)]
    t0 = time.perf_counter()
    for w in workers:
        w.start()
    for w in workers:
        w.join(timeout=30.0)
    wall = time.perf_counter() - t0
    assert not any(w.is_alive() for w in workers), "a decision thread hung"
    assert errors == []
    assert len(served) == threads_n * per_thread
    assert wall < 10.0, f"{wall:.2f}s for {threads_n * per_thread} served decisions"

    assert ev.flush(30.0) is True
    rows = read_rows(shadow_file)
    assert len(rows) + ev.dropped == threads_n * per_thread
    assert ev.observations == len(rows)
    assert ev.worker.dropped == ev.dropped
    assert shadow.write_failures() == 0
    assert all(r["agree"] is True and r["rules_tier"] == "T1" for r in rows)
    assert SECRET not in shadow_file.read_text(encoding="utf-8")
    assert all(json.loads(line) for line in shadow_file.read_text(encoding="utf-8").splitlines())


def test_worker_survives_an_item_that_raises(shadow_file):
    worker = ShadowWorker(maxsize=4)

    def boom() -> None:
        raise RuntimeError("worker item exploded")

    assert worker.submit(boom) is True
    assert worker.flush(5.0) is True
    assert shadow.write_failures() == 1
    assert "worker" in (shadow.last_write_error() or "")
    ev = ShadowEvaluator(_Backend("T1"), worker=worker)
    _decide(JudgmentModel(shadow=ev))
    assert ev.flush(5.0) is True
    assert len(read_rows(shadow_file)) == 1, "the thread must keep draining after a failed item"


# --- no false positives: legitimate load never drops ----------------------------


def test_sequential_legitimate_load_drops_nothing(shadow_file):
    """300 sequential decisions against a healthy kev: every row lands, no
    drop, no failure. A bounded queue that shed ordinary traffic would be a
    control blocking legitimate work."""
    n = 300
    backend = _Backend("T1")
    jm = JudgmentModel(shadow=ShadowEvaluator(backend))
    rules = JudgmentModel()
    for i in range(n):
        req = JudgmentRequest(request_type="chat", content=f"load {i}")
        assert _same(jm.decide(req), rules.rules_decide(req))
    assert shadow.flush(30.0) is True
    rows = read_rows(shadow_file)
    assert len(rows) == n
    assert jm.shadow.dropped == 0 and shadow.dropped() == 0
    assert shadow.write_failures() == 0
    assert backend.calls == 2 * n
    s = summary(shadow_file)
    assert s["n"] == n and s["sufficient"] is True and s["agreement_rate"] == 1.0
    assert "dropped" not in s


@pytest.mark.parametrize("value, expected", [("", 256), ("7", 7), ("0", 1), ("nope", 256)])
def test_queue_size_env(monkeypatch, value, expected):
    monkeypatch.setenv(shadow.QUEUE_ENV, value)
    assert shadow.queue_size() == expected
    assert ShadowWorker().maxsize == expected


@pytest.mark.parametrize("value, expected", [("", 0.1), ("0.25", 0.25), ("0", 0.0), ("-1", 0.0), ("nan", 0.0), ("x", 0.1)])
def test_grace_env(monkeypatch, value, expected):
    monkeypatch.setenv(shadow.GRACE_ENV, value)
    assert shadow.grace_seconds() == expected
