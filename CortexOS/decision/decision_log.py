"""Append-only JSONL log of tier decisions (KEV-LOG, #246).

One line per ``invoke_routed_completion`` outcome (ok, error and T0 paths) so
that KEV-CALIB can join decisions with outcomes and fit a temperature. The log
never holds prompt or system text: the JudgmentModel state dict (which carries
``content``) is reduced to ``state_hash`` before anything is written.

Fail open on the write, fail closed on the content:

- a log that cannot be written never raises into the node path; the failure is
  counted (``write_failures()``) so an operator can see it, not silently dropped;
- ``CORTEX_DECISION_LOG=0`` disables writing entirely;
- ``CORTEX_DECISION_LOG_PATH`` overrides the default
  ``data_path("engine", "tier_decisions.jsonl")`` (gitignored runtime state).

Concurrency (H2-DLOG-MP, #255). Each line is one ``os.write`` on a descriptor
opened with ``O_APPEND``: POSIX positions every append at end-of-file atomically
and a single write of a short line lands whole, so concurrent processes and
threads never tear or interleave lines. No file lock is taken. The process-local
``threading.Lock`` only serialises the failure counters, and it is re-created in
a forked child (``os.register_at_fork(after_in_child=...)``) so a child never
inherits a lock a writer thread in the parent was holding at fork time.

Confidence and probabilities are recorded when they can be obtained without a
second backend call: the rules-v0 model is pure and cheap, so it is re-evaluated
here; a live decision backend is not re-asked (that would double kev traffic),
so those rows carry ``confidence=None`` until KEV-SHADOW threads the answer
through. Nothing here imports ``packs.*``.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from CortexOS.paths import data_path
from CortexOS.routing.tiers import Tier

SCHEMA_VERSION = 1
ENABLE_ENV = "CORTEX_DECISION_LOG"
PATH_ENV = "CORTEX_DECISION_LOG_PATH"
DEFAULT_FILENAME = "tier_decisions.jsonl"

# Keys of the JudgmentModel state dict that are never allowed into a log line,
# even if a caller passes the raw state through by mistake.
_FORBIDDEN_STATE_KEYS = frozenset({"content", "prompt", "system"})

_lock = threading.Lock()
_write_failures = 0
_last_write_error: str | None = None

_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)


def _reset_lock_after_fork() -> None:
    """Give the child a fresh, unheld lock; the parent's may be held by another thread."""
    global _lock
    _lock = threading.Lock()


if hasattr(os, "register_at_fork"):  # POSIX; Windows never forks
    os.register_at_fork(after_in_child=_reset_lock_after_fork)


def _write_line(path: Path, line: str) -> None:
    """One O_APPEND ``os.write`` per line: atomic against other processes and threads.

    The lock is deliberately not held here: the kernel serialises O_APPEND
    writes across processes, which a process-local lock cannot do, and holding
    it across the syscall is what deadlocked forked children before #255.
    """
    data = (line + "\n").encode("utf-8")
    fd = os.open(path, _OPEN_FLAGS, 0o644)
    try:
        written = os.write(fd, data)
        if written != len(data):  # short write: the line is torn, count it
            raise OSError(f"short write: {written} of {len(data)} bytes")
    finally:
        os.close(fd)


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def log_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    return data_path("engine", DEFAULT_FILENAME)


def write_failures() -> int:
    """How many decision lines could not be written since process start (or reset)."""
    return _write_failures


def last_write_error() -> str | None:
    return _last_write_error


def reset_write_failures() -> None:
    global _write_failures, _last_write_error
    with _lock:
        _write_failures = 0
        _last_write_error = None


def state_hash(state: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of the JudgmentModel state dict."""
    blob = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def describe_decision(judgment_model: Any, state: dict[str, Any]) -> dict[str, Any]:
    """Backend name plus confidence/probabilities when obtainable without a network call.

    Rules-v0 is deterministic and free, so it is re-evaluated to recover the
    confidence that ``ModelRouter.route()`` discards. A live backend is only
    named; re-asking it would double every kev call.
    """
    backend = getattr(judgment_model, "decision_backend", None)
    if backend is not None:
        return {
            "backend": str(getattr(backend, "name", type(backend).__name__)),
            "confidence": None,
            "probabilities": None,
        }
    try:
        from CortexOS.decision.backends import RulesBackend, tier_question

        rules = RulesBackend(judgment_model)
        raw = rules.evaluate(tier_question(), state)
        if raw.degraded:
            return {"backend": rules.name, "confidence": None, "probabilities": None}
        labels = tier_question().labels
        probs = {label: float(s) for label, s in zip(labels, raw.scores, strict=True)}
        return {
            "backend": rules.name,
            "confidence": max(probs.values()),
            "probabilities": probs,
        }
    except Exception as exc:  # never let bookkeeping reach the node path
        return {"backend": "rules-v0", "confidence": None, "probabilities": None, "describe_error": type(exc).__name__}


def build_entry(
    *,
    run_id: str,
    node_id: str,
    request_type: str,
    default_tier: Tier | str,
    max_tier: Tier | str,
    tier: Tier | str,
    reason: str,
    status: str,
    state: dict[str, Any],
    judgment_model: Any = None,
    provider: str | None = None,
    model: str | None = None,
    error: BaseException | None = None,
    cost_myr: float = 0.0,
) -> dict[str, Any]:
    """The JSON object one log line holds. Pure; raises only on programmer error."""
    described = describe_decision(judgment_model, state) if judgment_model is not None else {
        "backend": None,
        "confidence": None,
        "probabilities": None,
    }
    entry: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "node_id": node_id,
        "request_type": request_type,
        "default_tier": _tier_value(default_tier),
        "max_tier": _tier_value(max_tier),
        "tier": _tier_value(tier),
        "reason": reason,
        "backend": described.get("backend"),
        "confidence": described.get("confidence"),
        "probabilities": described.get("probabilities"),
        "status": status,
        "error_class": type(error).__name__ if error is not None else None,
        "cost_myr": float(cost_myr),
        "state_hash": state_hash(state),
        "context_size": _int_or_none(state.get("context_size")),
        "prior_tier_failures": _int_or_none(state.get("prior_tier_failures")),
        "user_tier_budget": state.get("user_tier_budget"),
        "is_vip": bool(state.get("is_vip", False)),
        "provider": provider,
        "model": model,
    }
    if "describe_error" in described:
        entry["describe_error"] = described["describe_error"]
    for key in _FORBIDDEN_STATE_KEYS:
        entry.pop(key, None)
    return entry


def append(entry: dict[str, Any]) -> bool:
    """Append one line. Returns True when written. Never raises."""
    global _write_failures, _last_write_error
    try:
        if not enabled():
            return False
        for key in _FORBIDDEN_STATE_KEYS:
            if key in entry:
                raise ValueError(f"decision log entry must not carry {key!r}")
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_line(path, line)
        return True
    except Exception as exc:
        with _lock:
            _write_failures += 1
            _last_write_error = f"{type(exc).__name__}: {exc}"
        return False


def log_decision(**kwargs: Any) -> bool:
    """``build_entry`` + ``append`` with every failure absorbed. Never raises."""
    try:
        entry = build_entry(**kwargs)
    except Exception as exc:
        global _write_failures, _last_write_error
        with _lock:
            _write_failures += 1
            _last_write_error = f"build: {type(exc).__name__}: {exc}"
        return False
    return append(entry)


def read_entries(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the log back as dicts. Malformed lines are skipped, not raised."""
    target = path if path is not None else log_path()
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


def _tier_value(tier: Tier | str) -> str:
    return tier.value if isinstance(tier, Tier) else str(tier)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
