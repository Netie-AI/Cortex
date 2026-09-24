"""Shadow mode for the kev decision backend (KEV-SHADOW, #248).

With ``CORTEX_KEV_SHADOW=1`` and ``CORTEX_KEV_URL`` set, the rules-v0 cascade
(including the GH-02 floors, which are its first branches) keeps serving every
tier decision while kev is evaluated beside it through
``decide(check_order=True)``. Each evaluation appends one JSON line to
``data_path("engine", "tier_shadow.jsonl")`` so an operator can read back how
often the two agree before letting kev serve anything.

Guarantees:

- the served ``JudgmentDecision`` is exactly what the rules alone return; the
  shadow row is a side effect that never changes it;
- nothing here raises into the routing path: backend failures become a
  ``degraded`` row, and a file that cannot be written is counted, not raised;
- no raw prompt text is written: the state dict is reduced to ``state_hash``
  (reused from ``decision_log``) before anything reaches the file, and the
  backend's ``cause`` / ``abstain_reason`` are reduced to a fixed category
  vocabulary (``cause_category``) so exception text, which can echo whatever
  kev put in its response body, never reaches the file either;
- ``summary()`` never claims an agreement rate below ``MIN_N`` rows as a
  metric: it reports n and flags ``sufficient=False``;
- (H2-SHADOW-ASYNC, #253) the evaluation never runs on the serving path.
  ``observe`` puts the observation on a bounded queue and returns at once; a
  single daemon worker asks kev and appends the row. A full queue drops the
  observation and counts it (``dropped()``, and ``summary()["dropped"]`` once
  any were dropped); it never blocks and never raises. ``flush(timeout)``
  drains the queue for readers and at interpreter exit. ``read_rows``,
  ``summary``, ``write_failures`` and ``last_write_error`` flush first, so a
  reader sees every observation made before it asked.

Nothing here imports ``packs.*``.
"""

from __future__ import annotations

import atexit
import json
import math
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CortexOS.paths import data_path

from .decision_log import state_hash

SCHEMA_VERSION = 1
SHADOW_ENV = "CORTEX_KEV_SHADOW"
PATH_ENV = "CORTEX_KEV_SHADOW_PATH"
DEFAULT_FILENAME = "tier_shadow.jsonl"
MIN_N = 300

_FORBIDDEN_KEYS = frozenset({"content", "prompt", "system", "state"})

# Cause vocabulary. A backend failure string is ``"<head>: <detail>"`` where the
# detail is frequently ``str(exc)`` — and ``str(exc)`` for a parse failure quotes
# the offending value, which is whatever kev put in its response body, which a
# misbehaving kev can fill from the prompt it was sent. Only a head from this
# table, plus a detail that is a bare Python identifier (an exception class
# name) for the heads that carry one, is ever written. Everything else is
# collapsed to the head alone, or to ``"other"``.
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HTTP_CAUSE = re.compile(r"^http \d{3}$")
_RAISED_CAUSE = re.compile(r"^raised ([A-Za-z_][A-Za-z0-9_]*)$")
_CONFIDENCE_REASON = re.compile(r"^confidence \d+\.\d+ < threshold \d+\.\d+$")
_HEADS_WITH_TYPE_NAME = frozenset({"transport"})
_HEADS_BARE = frozenset({"parse", "invalid json", "transport", "unknown"})
OTHER_CAUSE = "other"


def cause_category(cause: str | None) -> str | None:
    """Reduce a backend ``cause`` to a fixed category. Never returns free text.

    ``"parse: could not convert string to float: '<prompt>'"`` becomes ``"parse"``;
    ``"transport: ReadTimeout"`` and ``"raised RuntimeError"`` keep the exception
    class name because it is an identifier, not a message; ``"http 500"`` and
    ``"unknown"`` pass as they are; anything unrecognised becomes ``"other"``.
    """
    if cause is None:
        return None
    text = str(cause).strip()
    if _HTTP_CAUSE.match(text):
        return text
    if _RAISED_CAUSE.match(text):
        return text
    head, _, detail = text.partition(":")
    head, detail = head.strip(), detail.strip()
    if head in _HEADS_WITH_TYPE_NAME and _IDENT.match(detail):
        return f"{head}: {detail}"
    if head in _HEADS_BARE:
        return head
    return OTHER_CAUSE


def abstain_reason_category(reason: str | None, *, degraded: bool, cause: str | None) -> str | None:
    """Reduce an ``abstain_reason`` to a fixed category. Never returns free text.

    A degraded answer's reason is ``"degraded: <cause>"`` and carries the same
    detail as the cause, so it is rebuilt from ``cause_category``. The two
    non-degraded reasons ``decide`` produces are fixed templates over numbers and
    pass through; anything else becomes ``"other"``.
    """
    if degraded:
        return f"degraded: {cause_category(cause) or 'unknown'}"
    if reason is None:
        return None
    text = str(reason).strip()
    if text.startswith("order_sensitive:"):
        return "order_sensitive"
    if _CONFIDENCE_REASON.match(text):
        return text
    return OTHER_CAUSE


_lock = threading.Lock()
_write_failures = 0
_last_write_error: str | None = None
_dropped = 0

QUEUE_ENV = "CORTEX_KEV_SHADOW_QUEUE"
DEFAULT_QUEUE_SIZE = 256
GRACE_ENV = "CORTEX_KEV_SHADOW_GRACE"
# Seconds ``observe`` may wait for an *idle* worker to finish this one
# observation. A healthy loopback kev answers in a few ms, so the row is on
# disk in decision order before the decision is served. A slow kev costs at
# most this once: the next observation finds a backlog and does not wait.
DEFAULT_GRACE = 0.1
EXIT_FLUSH_TIMEOUT = 10.0
FLUSH_TIMEOUT = 30.0


_SHADOW_OFF = frozenset({"", "0", "false", "no", "off"})


def shadow_enabled() -> bool:
    """Fail safe toward watching: any value other than an explicit off enables
    shadow. A typo ("y", "2", "enabled") must never fall through to letting
    kev serve live traffic when the operator only meant to observe it."""
    return os.environ.get(SHADOW_ENV, "").strip().lower() not in _SHADOW_OFF


def shadow_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    return data_path("engine", DEFAULT_FILENAME)


def write_failures() -> int:
    """Write failures so far. Flushes first: a failure is only known once the
    worker has tried the append, and a reader asking for it wants the count
    for the observations it has already made."""
    flush()
    return _write_failures


def last_write_error() -> str | None:
    flush()
    return _last_write_error


def reset_write_failures() -> None:
    global _write_failures, _last_write_error
    with _lock:
        _write_failures = 0
        _last_write_error = None


def dropped() -> int:
    """Observations dropped because the queue was full, process-wide."""
    return _dropped


def reset_dropped() -> None:
    global _dropped
    with _lock:
        _dropped = 0


def _record_failure(exc: BaseException, where: str) -> None:
    global _write_failures, _last_write_error
    with _lock:
        _write_failures += 1
        _last_write_error = f"{where}: {type(exc).__name__}: {exc}"


def _record_drop() -> None:
    global _dropped
    with _lock:
        _dropped += 1


def queue_size() -> int:
    raw = os.environ.get(QUEUE_ENV, "").strip()
    if not raw:
        return DEFAULT_QUEUE_SIZE
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_QUEUE_SIZE


def grace_seconds() -> float:
    raw = os.environ.get(GRACE_ENV, "").strip()
    if not raw:
        return DEFAULT_GRACE
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_GRACE
    return value if math.isfinite(value) and value > 0 else 0.0


class ShadowWorker:
    """One daemon thread draining a bounded queue of shadow observations.

    ``submit(fn)`` returns True when queued and False when the queue is full,
    without blocking either way. The thread is started lazily on first submit
    and restarted if it ever died, so a worker created at import time costs no
    thread until shadow is used. Items run one at a time in FIFO order, which
    keeps the file's row order equal to decision order for a single caller.

    ``flush(timeout)`` waits until every submitted item has finished (not just
    been dequeued) or the timeout passes, and returns whether it drained.
    """

    def __init__(self, maxsize: int | None = None) -> None:
        self.maxsize = maxsize if maxsize is not None else queue_size()
        self.dropped = 0
        self._init_state()

    def _init_state(self) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue(maxsize=self.maxsize)
        self._cv = threading.Condition()
        self._pending = 0
        self._thread: threading.Thread | None = None

    def _ensure_thread(self) -> None:
        with self._cv:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run, name="kev-shadow", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                item()
            except BaseException as exc:  # the worker must outlive any failure
                try:
                    _record_failure(exc, "worker")
                except Exception:
                    pass
            finally:
                with self._cv:
                    self._pending -= 1
                    self._cv.notify_all()

    def submit(self, fn: Callable[[], None], *, grace: float = 0.0) -> bool:
        """Queue ``fn``. Never blocks on the queue, never raises. False means
        it was dropped.

        With ``grace > 0`` and no other item pending, waits up to ``grace``
        seconds for ``fn`` to finish; behind a backlog it returns at once, so a
        stalled worker can never cost callers more than one grace in a row.
        """
        done = threading.Event()

        def run() -> None:
            try:
                fn()
            finally:
                done.set()

        with self._cv:
            self._pending += 1
            idle = self._pending == 1
        try:
            self._queue.put_nowait(run)
        except queue.Full:
            with self._cv:
                self._pending -= 1
                self.dropped += 1
                self._cv.notify_all()
            _record_drop()
            return False
        except Exception as exc:  # never reaches the serving path
            with self._cv:
                self._pending -= 1
                self._cv.notify_all()
            _record_failure(exc, "submit")
            return False
        try:
            self._ensure_thread()
        except Exception as exc:  # thread limits, interpreter shutdown
            _record_failure(exc, "thread")
            return True
        if idle and grace > 0:
            done.wait(grace)
        return True

    def pending(self) -> int:
        with self._cv:
            return self._pending

    def flush(self, timeout: float | None = FLUSH_TIMEOUT) -> bool:
        """Wait for every submitted item to finish. True when drained in time."""
        with self._cv:
            if self._pending == 0:
                return True
            thread = self._thread
        if thread is None or not thread.is_alive():
            # Nothing will drain the queue unless a thread exists; start one.
            try:
                self._ensure_thread()
            except Exception as exc:
                _record_failure(exc, "thread")
                return False
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._cv:
            while self._pending > 0:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cv.wait(remaining)
            return True

    def _after_fork_in_child(self) -> None:
        # The worker thread does not survive a fork; queued closures belong to
        # the parent. Start clean so the child never waits on work nobody runs
        # and never inherits a lock a parent thread held at the fork.
        self._init_state()


_worker = ShadowWorker()


def default_worker() -> ShadowWorker:
    return _worker


def flush(timeout: float | None = FLUSH_TIMEOUT) -> bool:
    """Drain the process-wide shadow queue. True when every observation made
    before the call has been evaluated and appended (or counted as failed)."""
    return _worker.flush(timeout)


def _flush_at_exit() -> None:
    try:
        _worker.flush(EXIT_FLUSH_TIMEOUT)
    except Exception:
        pass


atexit.register(_flush_at_exit)

if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=lambda: _worker._after_fork_in_child())


def _with_persistent_client(backend: Any) -> Any:
    """Give a clientless ``KevHttpBackend`` one persistent HTTP client.

    ``httpx.post`` builds a client, and with it a TLS context, on every call:
    measured at 55 to 80 ms each, so two calls per observation cost more than
    the whole grace budget before kev has answered anything. The worker is one
    thread and asks kev serially, so one client serves every observation. Any
    other backend, or one that already carries a client, is returned as is.
    """
    try:
        from .backends import KevHttpBackend
    except Exception:
        return backend
    if not isinstance(backend, KevHttpBackend) or getattr(backend, "_client", None) is not None:
        return backend
    try:
        import httpx

        return KevHttpBackend(
            backend.base_url,
            model=backend.model,
            timeout_s=backend.timeout_s,
            server_calibrated=backend.server_calibrated,
            client=httpx.Client(),
        )
    except Exception:
        return backend


class ShadowEvaluator:
    """Evaluates a decision backend beside the rules and logs agreement.

    ``observe(state, rules_tier)`` queues the evaluation on the worker and
    returns at once, never raising; the row is appended by the worker. The
    backend is asked through ``decide(check_order=True)`` so order sensitivity
    is recorded, which is two backend calls per decision, off the serving path.
    """

    def __init__(
        self,
        backend: Any,
        *,
        abstain_threshold: float | None = None,
        path: Path | None = None,
        worker: ShadowWorker | None = None,
        grace: float | None = None,
    ) -> None:
        self.backend = _with_persistent_client(backend)
        self.abstain_threshold = abstain_threshold
        self._path = path
        self.worker = worker if worker is not None else _worker
        self.grace = grace if grace is not None else grace_seconds()
        self._count_lock = threading.Lock()
        self.observations = 0
        self.dropped = 0

    @property
    def backend_name(self) -> str:
        return str(getattr(self.backend, "name", type(self.backend).__name__))

    def path(self) -> Path:
        return self._path if self._path is not None else shadow_path()

    def observe(self, state: dict[str, Any], rules_tier: Any) -> bool:
        """Queue the evaluation of ``state``. Never blocks on kev, never raises.

        True means the observation was queued; False means the queue was full
        and it was dropped (counted). When the worker is idle the call waits at
        most ``self.grace`` seconds for the row, so a fast kev leaves it on disk
        before the decision is served; otherwise the row becomes visible after
        ``flush()``, which ``read_rows`` and ``summary`` call for you.
        """
        try:
            target = self.path()
        except Exception as exc:
            _record_failure(exc, "path")
            return False

        def work() -> None:
            self._evaluate_and_append(state, rules_tier, target)

        try:
            queued = self.worker.submit(work, grace=self.grace)
        except Exception as exc:  # bookkeeping must never reach the routing path
            _record_failure(exc, "submit")
            return False
        if not queued:
            with self._count_lock:
                self.dropped += 1
        return queued

    def flush(self, timeout: float | None = FLUSH_TIMEOUT) -> bool:
        return self.worker.flush(timeout)

    def _evaluate_and_append(self, state: dict[str, Any], rules_tier: Any, target: Path) -> None:
        try:
            row = self.build_row(state, rules_tier)
        except Exception as exc:
            _record_failure(exc, "build")
            return
        append_row(row, target)
        with self._count_lock:
            self.observations += 1

    def build_row(self, state: dict[str, Any], rules_tier: Any) -> dict[str, Any]:
        from .backends import tier_question
        from .decide import decide

        served = _tier_value(rules_tier)
        try:
            answer = decide(
                tier_question(),
                state,
                self.backend,
                abstain_threshold=self.abstain_threshold,
                check_order=True,
            )
        except Exception as exc:  # a backend that raises is a degraded observation
            return self._row(
                state,
                served,
                kev_choice=None,
                kev_probs=None,
                kev_confidence=None,
                abstain=True,
                abstain_reason=f"degraded: raised {type(exc).__name__}",
                degraded=True,
                cause=f"raised {type(exc).__name__}",
                order_sensitive=False,
                calibrated=False,
            )
        return self._row(
            state,
            served,
            kev_choice=answer.choice,
            kev_probs=dict(answer.probabilities) if answer.probabilities else None,
            kev_confidence=float(answer.confidence) if not answer.degraded else None,
            abstain=bool(answer.abstain),
            abstain_reason=answer.abstain_reason,
            degraded=bool(answer.degraded),
            cause=answer.cause,
            order_sensitive=bool(answer.order_sensitive),
            calibrated=bool(answer.calibrated),
        )

    def _row(
        self,
        state: dict[str, Any],
        served: str,
        *,
        kev_choice: str | None,
        kev_probs: dict[str, float] | None,
        kev_confidence: float | None,
        abstain: bool,
        abstain_reason: str | None,
        degraded: bool,
        cause: str | None,
        order_sensitive: bool,
        calibrated: bool,
    ) -> dict[str, Any]:
        # cause and abstain_reason are reduced to a fixed vocabulary here, at the
        # only point where backend text meets the file: str(exc) from a parse
        # failure quotes the value kev returned, and kev's body can echo the prompt.
        # kev's parser accepts NaN (NaN < 0 and sum <= 0 are both False); a
        # non-finite number is not a probability, and json would write a
        # non-standard NaN/Infinity token, so drop it rather than log it.
        if kev_probs is not None and not all(_finite(v) for v in kev_probs.values()):
            # confidence and choice derived from non-finite probabilities are
            # meaningless too (decide() turns NaN into confidence 1.0).
            kev_probs = None
            kev_confidence = None
            kev_choice = None
        if kev_confidence is not None and not _finite(kev_confidence):
            kev_confidence = None
        cause_cat = cause_category(cause)
        reason_cat = abstain_reason_category(abstain_reason, degraded=degraded, cause=cause)
        return {
            "schema_version": SCHEMA_VERSION,
            "ts": datetime.now(timezone.utc).isoformat(),
            "backend": self.backend_name,
            "state_hash": state_hash(state),
            "request_type": str(state.get("request_type", "")),
            "rules_tier": served,
            "kev_choice": kev_choice,
            "kev_probs": kev_probs,
            "kev_confidence": kev_confidence,
            "abstain": abstain,
            "abstain_reason": reason_cat,
            "degraded": degraded,
            "cause": cause_cat,
            "order_sensitive": order_sensitive,
            "calibrated": calibrated,
            # agree is only true when kev produced a choice that matches the served tier;
            # a degraded or abstaining kev did not agree, it did not answer.
            "agree": (
                kev_choice is not None and not abstain and not degraded and kev_choice == served
            ),
        }


def append_row(row: dict[str, Any], path: Path | None = None) -> bool:
    """Append one shadow row. Returns True when written. Never raises."""
    try:
        for key in _FORBIDDEN_KEYS:
            if key in row:
                raise ValueError(f"shadow row must not carry {key!r}")
        line = json.dumps(
            row, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False, allow_nan=False
        )
        target = path if path is not None else shadow_path()
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return True
    except Exception as exc:
        _record_failure(exc, "append")
        return False


def read_rows(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the shadow log back. Malformed lines are skipped, not raised.

    Flushes the process-wide worker first, so every observation queued before
    the call is on disk (or counted as a failure) before the file is read.
    """
    flush()
    target = path if path is not None else shadow_path()
    if not target.exists():
        return []
    out: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def summary(path: Path | None = None, *, min_n: int = MIN_N) -> dict[str, Any]:
    """Agreement rate, n and degraded count over the shadow log.

    ``agreement_rate`` is the share of all rows (degraded and abstaining rows
    included, since those are decisions kev failed to make) whose kev choice
    matched the served rules tier. It is ``None`` below ``min_n`` rows (and
    when there are none), so no caller can present it as a metric too early;
    ``agree`` and ``n`` are still returned as raw counts.

    ``dropped`` (observations the bounded queue refused, process-wide, see
    ``dropped()``) is reported once any were dropped. It is a count of
    decisions this process never evaluated, so it belongs beside ``n``; it is
    absent when zero because the summary shape with no drops is frozen by the
    existing readers.
    """
    rows = read_rows(path)
    n = len(rows)
    # Recomputed from the row's own flags: an abstaining or degraded kev never
    # counts as agreeing, whatever an older row's ``agree`` field says.
    agree = sum(
        1
        for r in rows
        if r.get("agree") is True
        and r.get("abstain") is not True
        and r.get("degraded") is not True
        and r.get("order_sensitive") is not True
    )
    degraded = sum(1 for r in rows if r.get("degraded") is True)
    abstained = sum(1 for r in rows if r.get("abstain") is True and r.get("degraded") is not True)
    order_sensitive = sum(1 for r in rows if r.get("order_sensitive") is True)
    out: dict[str, Any] = {
        "n": n,
        "agree": agree,
        "degraded": degraded,
        "abstained": abstained,
        "order_sensitive": order_sensitive,
        # No rate below min_n: an agreement number at n=7 reads as evidence.
        "agreement_rate": (agree / n) if n >= min_n and n else None,
        "min_n": min_n,
        "sufficient": n >= min_n,
    }
    if _dropped:
        out["dropped"] = _dropped
    return out


def _tier_value(tier: Any) -> str:
    value = getattr(tier, "value", None)
    return str(value) if value is not None else str(tier)
