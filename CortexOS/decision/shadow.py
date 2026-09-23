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
  metric: it reports n and flags ``sufficient=False``.

Nothing here imports ``packs.*``.
"""

from __future__ import annotations

import json
import os
import re
import threading
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


def shadow_enabled() -> bool:
    return os.environ.get(SHADOW_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def shadow_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    return data_path("engine", DEFAULT_FILENAME)


def write_failures() -> int:
    return _write_failures


def last_write_error() -> str | None:
    return _last_write_error


def reset_write_failures() -> None:
    global _write_failures, _last_write_error
    with _lock:
        _write_failures = 0
        _last_write_error = None


def _record_failure(exc: BaseException, where: str) -> None:
    global _write_failures, _last_write_error
    with _lock:
        _write_failures += 1
        _last_write_error = f"{where}: {type(exc).__name__}: {exc}"


class ShadowEvaluator:
    """Evaluates a decision backend beside the rules and logs agreement.

    ``observe(state, rules_tier)`` returns the row it wrote (or tried to write)
    and never raises. The backend is asked through ``decide(check_order=True)``
    so order sensitivity is recorded, which is two backend calls per decision.
    """

    def __init__(
        self,
        backend: Any,
        *,
        abstain_threshold: float | None = None,
        path: Path | None = None,
    ) -> None:
        self.backend = backend
        self.abstain_threshold = abstain_threshold
        self._path = path
        self.observations = 0

    @property
    def backend_name(self) -> str:
        return str(getattr(self.backend, "name", type(self.backend).__name__))

    def path(self) -> Path:
        return self._path if self._path is not None else shadow_path()

    def observe(self, state: dict[str, Any], rules_tier: Any) -> dict[str, Any] | None:
        """Evaluate the backend on ``state`` and append one row. Never raises."""
        try:
            row = self.build_row(state, rules_tier)
        except Exception as exc:  # bookkeeping must never reach the routing path
            _record_failure(exc, "build")
            return None
        append_row(row, self.path())
        self.observations += 1
        return row

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
            "agree": kev_choice is not None and kev_choice == served,
        }


def append_row(row: dict[str, Any], path: Path | None = None) -> bool:
    """Append one shadow row. Returns True when written. Never raises."""
    try:
        for key in _FORBIDDEN_KEYS:
            if key in row:
                raise ValueError(f"shadow row must not carry {key!r}")
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)
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
    """Read the shadow log back. Malformed lines are skipped, not raised."""
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


def summary(path: Path | None = None, *, min_n: int = MIN_N) -> dict[str, Any]:
    """Agreement rate, n and degraded count over the shadow log.

    ``agreement_rate`` is the share of all rows (degraded and abstaining rows
    included, since those are decisions kev failed to make) whose kev choice
    matched the served rules tier. It is ``None`` when there are no rows.
    ``sufficient`` is False below ``min_n`` rows; a caller must not present the
    rate as a metric in that case.
    """
    rows = read_rows(path)
    n = len(rows)
    agree = sum(1 for r in rows if r.get("agree") is True)
    degraded = sum(1 for r in rows if r.get("degraded") is True)
    abstained = sum(1 for r in rows if r.get("abstain") is True and r.get("degraded") is not True)
    order_sensitive = sum(1 for r in rows if r.get("order_sensitive") is True)
    return {
        "n": n,
        "agree": agree,
        "degraded": degraded,
        "abstained": abstained,
        "order_sensitive": order_sensitive,
        "agreement_rate": (agree / n) if n else None,
        "min_n": min_n,
        "sufficient": n >= min_n,
    }


def _tier_value(tier: Any) -> str:
    value = getattr(tier, "value", None)
    return str(value) if value is not None else str(tier)
