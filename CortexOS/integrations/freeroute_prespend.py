"""Predict-before-spend gate in front of :func:`freeroute.complete` (Cortex #271 ROUTER-3).

Before a paid model call, ask KEV-DECIDE (:func:`CortexOS.decision.decide`) one
yes/no question about the caller's plan (tables, joins, SQL shape): will it
validate? Opt-in with ``CORTEX_FREEROUTE_PRESPEND``:

- unset / ``off``: nothing here runs; ``complete()`` is unchanged.
- ``shadow``: ask, record the prediction on the stamp and the route row,
  always spend. This is what the threshold is tuned from.
- ``enforce``: skip the paid call when ``decide()`` abstains or says no. It
  needs a threshold tuned on the train split
  (``CORTEX_FREEROUTE_PRESPEND_THRESHOLD``); without one nothing is spent.
- any other value: named refusal, nothing spent (fail-closed).

A degraded backend (no ``CORTEX_KEV_URL``, a non-loopback URL, an HTTP or parse
failure) does not guess: the call is spent and the stamp says the gate was
degraded and why. A caller that passes no plan state is spent the same way.

Stdlib at import time; :mod:`CortexOS.decision` is imported only when the gate
is on, so the FreeRoute answer path keeps its import cost.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

MODE_ENV = "CORTEX_FREEROUTE_PRESPEND"
THRESHOLD_ENV = "CORTEX_FREEROUTE_PRESPEND_THRESHOLD"
SHADOW = "shadow"
ENFORCE = "enforce"
_OFF = frozenset({"", "off", "0"})

QUESTION_TEXT = "Will this plan (tables, joins, SQL shape) produce SQL that passes the SQL gate?"

_backend_var: ContextVar[Any | None] = ContextVar("freeroute_prespend_backend", default=None)


@dataclass(frozen=True)
class Gate:
    spend: bool
    record: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@contextmanager
def use_backend(backend: Any) -> Iterator[None]:
    """Install a decision backend for this context (tests, an in-process kev)."""
    token = _backend_var.set(backend)
    try:
        yield
    finally:
        _backend_var.reset(token)


def mode() -> str:
    return (os.environ.get(MODE_ENV) or "").strip().lower()


def _threshold() -> float | None:
    raw = (os.environ.get(THRESHOLD_ENV) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0.0 <= value <= 1.0 else None


def _question() -> Any:
    from CortexOS.decision import NoulQuestion

    return NoulQuestion(
        QUESTION_TEXT,
        true_description="the SQL gate accepts the generated query",
        false_description="the SQL gate rejects it or the model answers unusably",
    )


def _backend() -> tuple[Any | None, str]:
    got = _backend_var.get()
    if got is not None:
        return got, ""
    from CortexOS.decision.backends import KEV_URL_ENV, KevHttpBackend

    url = (os.environ.get(KEV_URL_ENV) or "").strip()
    if not url:
        return None, f"no decision backend ({KEV_URL_ENV} unset)"
    try:
        return KevHttpBackend(url), ""
    except ValueError as exc:
        from CortexOS.integrations.freeroute import redact

        return None, redact(str(exc))


def check(state: Mapping[str, Any] | None) -> Gate | None:
    """``None`` when the gate is off. Otherwise whether to spend, and the record to stamp."""
    current = mode()
    if current in _OFF:
        return None
    if current not in (SHADOW, ENFORCE):
        shown = current[:16]
        return Gate(
            False,
            {"mode": shown, "decision": "refused"},
            f"{MODE_ENV}={shown!r} is not shadow|enforce; nothing spent (fail-closed)",
        )
    threshold = _threshold()
    if current == ENFORCE and threshold is None:
        return Gate(
            False,
            {"mode": current, "decision": "refused"},
            f"{MODE_ENV}=enforce needs {THRESHOLD_ENV} tuned on the train split "
            "(0..1); nothing spent (fail-closed)",
        )
    base: dict[str, Any] = {"mode": current, "threshold": threshold}
    if state is None:
        return Gate(True, {**base, "decision": "spend", "degraded": "caller passed no plan state"})
    backend, why = _backend()
    if backend is None:
        return Gate(True, {**base, "decision": "spend", "degraded": why})
    from CortexOS.decision import decide

    try:
        answer = decide(_question(), dict(state), backend, abstain_threshold=threshold)
    except Exception as exc:  # noqa: BLE001 - a broken backend is degraded, never a crash
        return Gate(True, {**base, "decision": "spend", "degraded": f"backend error: {type(exc).__name__}"})
    record = {
        **base,
        "backend": answer.backend,
        "p_valid": answer.noul,
        "confidence": round(answer.confidence, 4),
        "calibrated": answer.calibrated,
        "abstain": answer.abstain,
        "abstain_reason": answer.abstain_reason,
    }
    if answer.degraded:
        return Gate(True, {**record, "decision": "spend", "degraded": answer.cause or "backend degraded"})
    predicted_no = answer.noul is None or answer.noul < 0.5
    would_skip = answer.abstain or predicted_no
    why_skip = (
        f"abstained ({answer.abstain_reason})" if answer.abstain else f"predicted invalid (p_valid={answer.noul})"
    )
    if current == SHADOW:
        return Gate(True, {**record, "decision": "spend (shadow)", "would_skip": would_skip})
    if would_skip:
        return Gate(
            False,
            {**record, "decision": "skip"},
            f"pre-spend gate skipped the paid call: {why_skip}; re-plan or abstain",
        )
    return Gate(True, {**record, "decision": "spend"})


# -- tuning (train only) and reporting (held-out only) ---------------------------


def _labelled(split: str) -> list[tuple[float, int]]:
    """(p_valid, 1 if the checker accepted) for recorded predictions on one split."""
    from CortexOS.integrations import freeroute

    with freeroute._connect(write=False) as con:
        if con is None:
            return []
        try:
            rows = list(
                con.execute(
                    "SELECT prespend_p, verdict FROM routes WHERE split = ? AND shadow = 0 "
                    "AND prespend_p IS NOT NULL AND verdict IS NOT NULL",
                    (split,),
                )
            )
        except sqlite3.Error:
            return []
    out: list[tuple[float, int]] = []
    for p, verdict in rows:
        if verdict in freeroute.GOOD_VERDICTS:
            out.append((float(p), 1))
        elif verdict in freeroute.BAD_VERDICTS:
            out.append((float(p), 0))
    return out


def tune(rows: list[tuple[float, int]] | None = None) -> float | None:
    """Largest abstain threshold that skips no train row the checker accepted.

    ``decide()`` spends when ``p >= 0.5`` and confidence ``|2p - 1| >= t``,
    i.e. when ``p >= (1 + t) / 2``. Keeping every good train row means
    ``t = 2 * min(p_good) - 1``. ``None`` when there is no good train row, or
    when a good row already has ``p < 0.5`` (no threshold keeps coverage).
    """
    data = _labelled("train") if rows is None else rows
    good = [p for p, y in data if y == 1]
    if not good or min(good) < 0.5:
        return None
    return round(2.0 * min(good) - 1.0, 6)


def report(threshold: float, rows: list[tuple[float, int]] | None = None) -> dict[str, Any]:
    """Held-out ECE, Brier, automatable share, and what ``enforce`` would skip."""
    from CortexOS.decision import automatable_share, brier, ece

    data = _labelled("heldout") if rows is None else rows
    if not data:
        return {"split": "heldout", "n": 0, "measured": False}
    probs = [(1.0 - p, p) for p, _ in data]
    labels = [y for _, y in data]
    cut = (1.0 + threshold) / 2.0
    skipped = [(p, y) for p, y in data if p < cut]
    return {
        "split": "heldout",
        "n": len(data),
        "measured": True,
        "threshold": threshold,
        "ece": round(ece(probs, labels), 4),
        "brier": round(brier(probs, labels), 4),
        "automatable_share": round(automatable_share(probs, labels), 4),
        "calls_skipped": len(skipped),
        "good_skipped": sum(1 for _, y in skipped if y == 1),
        "bad_skipped": sum(1 for _, y in skipped if y == 0),
    }


if __name__ == "__main__":
    import json

    t = tune()
    print(json.dumps({"tuned_on": "train", "threshold": t, "heldout": report(t) if t is not None else None}))
