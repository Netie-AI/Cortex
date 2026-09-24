"""Outcome labels for the tier-decision log (KEV-CALIB, #247).

Joins one decision-log row (``run_id``, ``node_id``, KEV-LOG schema) with the
outcome the engine observed for that node and answers a single question:

    was the served tier sufficient?

Only one arm is ever observed (the tier that actually served), so the label is
binary and the probability under test is ``P(served tier)`` as the backend
reported it at decision time. Each labelled row is reduced to a two-class
probability row ``(1 - p, p)`` with label ``1`` (sufficient) or ``0``
(insufficient); ``calibration.fit_temperature`` then treats ``log`` of those
probabilities as pseudo-logits. That is stated in the report because a
probability-returning backend has no real logits to scale.

Outcome sources, in this order of authority:

1. ledger status for ``(run_id, node_id)`` when supplied (``node_executions``
   rows or ``NodeExecutionRecord`` objects), else the status the log row
   carries (both are written by the same call site in
   ``invoke_routed_completion``). A key is not unique: ``agent_task`` calls
   ``invoke_routed_completion`` once per step with the same ``run_id`` and
   ``node.id``, so one log row and one ledger record land per attempt. The
   join is therefore ordinal: the nth log row for a key is paired with the nth
   ledger record for that key, in append order. Log rows whose error class
   never reaches the ledger (cost ceilings raise before ``ledger.add``) are
   skipped in that count. Ledger rows with ``status='replayed'`` (a resumed
   worker serving a node from the step journal, H2-COST-NODE-ALL #252) are
   not attempts: a replay makes no routing decision and writes no log row,
   so the index drops them before the join. When the counts still differ
   the key is ambiguous and every one of its rows is excluded as
   ``ambiguous_ledger_join``, never labelled from the last record;
2. scoreboard ``predicates_pass`` for ``run_id`` where present: a failed
   predicate marks an ``ok`` node insufficient, a passed one confirms it.

Infrastructure failures (cost ceiling, workflow cost ceiling, redaction,
transport, timeout, provider availability such as 429/500/502/503 and auth)
say nothing about the tier. They are never labelled ``0``; they are excluded
and counted by reason so the report can print them. The check is applied to
the log row's own ``error_class`` and to the paired ledger record's error text
(the ledger stores ``str(exc)``, not a class name).

Fail closed: a row without probabilities, without a served-tier probability,
or with an unknown status is excluded and counted, never guessed. Nothing here
imports ``packs.*`` and nothing here reads prompt text (the log has none).
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Error classes that describe the infrastructure around a call, not the tier.
INFRA_ERROR_CLASSES: frozenset[str] = frozenset(
    {
        "CostCeilingExceeded",
        "WorkflowCostCeilingExceeded",
        "RedactionFailed",
        "TimeoutError",
        "TimeoutException",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TransportError",
        "RemoteProtocolError",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "BrokenPipeError",
        "CancelledError",
        # litellm / provider availability: the adapters call litellm.acompletion
        # directly, so these class names reach decision_log.build_entry as-is.
        "RateLimitError",
        "ServiceUnavailableError",
        "InternalServerError",
        "BadGatewayError",
        "APIError",
        "APIConnectionError",
        "APIStatusError",
        "OverloadedError",
        "HTTPStatusError",
        "Timeout",
        "AuthenticationError",
        "PermissionDeniedError",
        "BudgetExceededError",
        "MidStreamFallbackError",
    }
)
#: Substrings that mark an error class as infrastructure even when unlisted.
_INFRA_SUBSTRINGS: tuple[str, ...] = (
    "Timeout",
    "Transport",
    "Connect",
    "RateLimit",
    "Overloaded",
    "ServiceUnavailable",
    "InternalServer",
    "BadGateway",
    "Unavailable",
)
#: Lower-case fragments of ``str(exc)`` in the ledger ``error`` column that
#: describe infrastructure. Matched case-insensitively against the paired
#: ledger record when the log row's own class did not already exclude it.
_INFRA_TEXT_FRAGMENTS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "rate limit",
    "ratelimit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "service unavailable",
    "internal server error",
    "bad gateway",
    "cost ceiling",
    "ceiling",
    "redaction",
    "unauthorized",
    "authentication",
    "ratelimiterror",
    "serviceunavailableerror",
    "internalservererror",
    "badgatewayerror",
    "apiconnectionerror",
)
#: HTTP availability codes only when introduced as a status/code, never a bare
#: number ("bad output on line 500" is a content failure).
_INFRA_STATUS_CODE_RE = re.compile(
    r"\b(?:status(?: code)?|code|http|error)[:=\s]*(?:429|500|502|503)\b", re.I
)
#: Error classes that raise *before* ``ledger.add`` in ``invoke_routed_completion``,
#: so their log row has no ledger record and must not consume one in the join.
_NO_LEDGER_RECORD_CLASSES: frozenset[str] = frozenset(
    {"CostCeilingExceeded", "WorkflowCostCeilingExceeded"}
)
#: Ledger statuses that record no attempt of their own. ``replayed`` is
#: written by ``dag_runner`` when a resumed worker serves a node from the
#: step journal (#252): no adapter call, no routing decision, no log row.
#: Such rows must never consume a log row in the ordinal join.
LEDGER_NON_ATTEMPT_STATUSES: frozenset[str] = frozenset({"replayed"})

EXCLUDE_NO_PROBABILITIES = "no_probabilities"
EXCLUDE_NO_SERVED_PROBABILITY = "no_served_tier_probability"
EXCLUDE_UNKNOWN_STATUS = "unknown_status"
EXCLUDE_MISSING_KEY = "missing_run_or_node_id"
EXCLUDE_AMBIGUOUS_LEDGER_JOIN = "ambiguous_ledger_join"
EXCLUDE_INFRA_PREFIX = "infra:"
EXCLUDE_INFRA_LEDGER = "infra:ledger_error"

LABEL_SUFFICIENT = 1
LABEL_INSUFFICIENT = 0


@dataclass(frozen=True, slots=True)
class LabelledRow:
    run_id: str
    node_id: str
    tier: str
    backend: str | None
    p_served: float
    label: int
    source: str  # which evidence decided the label

    @property
    def prob_row(self) -> tuple[float, float]:
        return (1.0 - self.p_served, self.p_served)

    @property
    def pseudo_logits(self) -> tuple[float, float]:
        """``log`` of the two-class probabilities, clamped away from ``log 0``."""
        lo = 1e-9
        return (math.log(max(1.0 - self.p_served, lo)), math.log(max(self.p_served, lo)))


@dataclass(slots=True)
class LabelSet:
    rows: list[LabelledRow] = field(default_factory=list)
    excluded: Counter[str] = field(default_factory=Counter)
    total_entries: int = 0

    @property
    def n(self) -> int:
        return len(self.rows)

    @property
    def negatives(self) -> int:
        return sum(1 for r in self.rows if r.label == LABEL_INSUFFICIENT)

    @property
    def positives(self) -> int:
        return self.n - self.negatives

    @property
    def prob_rows(self) -> list[tuple[float, float]]:
        return [r.prob_row for r in self.rows]

    @property
    def pseudo_logit_rows(self) -> list[tuple[float, float]]:
        return [r.pseudo_logits for r in self.rows]

    @property
    def labels(self) -> list[int]:
        return [r.label for r in self.rows]


def is_infra_error(error_class: str | None) -> bool:
    """True when ``error_class`` describes infrastructure, not the served tier."""
    if not error_class:
        return False
    if error_class in INFRA_ERROR_CLASSES:
        return True
    return any(s in error_class for s in _INFRA_SUBSTRINGS)


def is_infra_error_text(error: str | None) -> bool:
    """True when a ledger ``error`` string (``str(exc)``) describes infrastructure."""
    if not error:
        return False
    low = error.lower()
    if any(frag in low for frag in _INFRA_TEXT_FRAGMENTS):
        return True
    return _INFRA_STATUS_CODE_RE.search(error) is not None


LedgerRecord = tuple[str, str | None]
LedgerIndex = dict[tuple[str, str], list[LedgerRecord]]


def ledger_status_index(records: Iterable[Any] | None) -> LedgerIndex:
    """``{(run_id, node_id): [(status, error), ...]}`` in append order.

    Every record for a key is kept, in the order the ledger appended it, so
    ``build_labels`` can pair the nth log row for a key with the nth ledger
    record. Nothing collapses to the last status: a multi-step node whose last
    attempt timed out must not relabel its earlier successful attempts.

    Records whose status is in ``LEDGER_NON_ATTEMPT_STATUSES`` (``replayed``)
    are dropped: a journal replay is not an attempt and has no log row to pair
    with, so keeping it would make every resumed node ambiguous.
    """
    index: LedgerIndex = {}
    if records is None:
        return index
    for rec in records:
        get = rec.get if isinstance(rec, Mapping) else lambda k, _r=rec: getattr(_r, k, None)
        run_id, node_id = get("run_id"), get("node_id")
        if run_id is None or node_id is None:
            continue
        status = get("status")
        if str(status) in LEDGER_NON_ATTEMPT_STATUSES:
            continue
        error = get("error")
        index.setdefault((str(run_id), str(node_id)), []).append(
            (
                str(status) if status is not None else "",
                str(error) if error is not None else None,
            )
        )
    return index


def _consumes_ledger_record(entry: Mapping[str, Any]) -> bool:
    """False for log rows whose error raised before ``ledger.add`` (no ledger row exists)."""
    return str(entry.get("error_class") or "") not in _NO_LEDGER_RECORD_CLASSES


def scoreboard_predicates(db_path: Path | str) -> dict[str, bool]:
    """``{run_id: predicates_pass}`` from a scoreboard SQLite file (``arch_runs``).

    Missing file or table gives an empty map: no predicates is not a failure,
    it just means ledger status alone labels the row. A run recorded more than
    once is a pass only if every recorded predicate passed.
    """
    path = Path(db_path)
    if not path.exists():
        return {}
    out: dict[str, bool] = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT run_id, predicates_pass FROM arch_runs WHERE predicates_pass IS NOT NULL"
            )
            for run_id, passed in cur.fetchall():
                key = str(run_id)
                out[key] = out.get(key, True) and bool(passed)
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return out


def _served_probability(entry: Mapping[str, Any]) -> float | None:
    probs = entry.get("probabilities")
    if not isinstance(probs, Mapping) or not probs:
        return None
    tier = entry.get("tier")
    if tier is None or tier not in probs:
        return None
    try:
        p = float(probs[tier])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p) or not 0.0 <= p <= 1.0:
        return None
    return p


def label_entry(
    entry: Mapping[str, Any],
    *,
    ledger_record: LedgerRecord | None = None,
    predicates: Mapping[str, bool] | None = None,
) -> tuple[LabelledRow | None, str | None]:
    """Label one decision-log row. Returns ``(row, None)`` or ``(None, exclusion_reason)``.

    ``ledger_record`` is the ``(status, error)`` ledger record already paired
    with this row by ``build_labels`` (the same attempt, not the last record
    for the key). When given, its status is the authority and its error text
    is checked for infrastructure before it can turn the row into a ``0``.
    """
    run_id, node_id = entry.get("run_id"), entry.get("node_id")
    if not run_id or not node_id:
        return None, EXCLUDE_MISSING_KEY
    key = (str(run_id), str(node_id))

    error_class = entry.get("error_class")
    if is_infra_error(error_class):
        return None, f"{EXCLUDE_INFRA_PREFIX}{error_class}"

    status = str(entry.get("status") or "")
    source = "log_status"
    if ledger_record is not None:
        status, ledger_error = ledger_record
        source = "ledger_status"
        if status == "error" and is_infra_error_text(ledger_error):
            return None, EXCLUDE_INFRA_LEDGER
    if status not in {"ok", "error"}:
        return None, EXCLUDE_UNKNOWN_STATUS

    if entry.get("probabilities") is None:
        return None, EXCLUDE_NO_PROBABILITIES
    p_served = _served_probability(entry)
    if p_served is None:
        return None, EXCLUDE_NO_SERVED_PROBABILITY

    if status == "error":
        label = LABEL_INSUFFICIENT
    else:
        label = LABEL_SUFFICIENT
        if predicates and key[0] in predicates:
            source = "scoreboard_predicates"
            label = LABEL_SUFFICIENT if predicates[key[0]] else LABEL_INSUFFICIENT

    backend = entry.get("backend")
    return (
        LabelledRow(
            run_id=key[0],
            node_id=key[1],
            tier=str(entry.get("tier")),
            backend=str(backend) if backend is not None else None,
            p_served=p_served,
            label=label,
            source=source,
        ),
        None,
    )


def build_labels(
    entries: Iterable[Mapping[str, Any]],
    *,
    ledger_records: Iterable[Any] | None = None,
    predicates: Mapping[str, bool] | None = None,
) -> LabelSet:
    """Join decision-log ``entries`` with ledger status and scoreboard predicates.

    The ledger join is ordinal per ``(run_id, node_id)``: the nth log row that
    can have a ledger record is paired with the nth ledger record. A key whose
    counts differ is ambiguous and all of its rows are excluded as
    ``ambiguous_ledger_join``; the last record never stands in for every attempt.
    """
    ledger = ledger_status_index(ledger_records)
    entry_list = list(entries)
    out = LabelSet()

    # First pass: how many log rows per key expect a ledger record.
    expected: Counter[tuple[str, str]] = Counter()
    for entry in entry_list:
        run_id, node_id = entry.get("run_id"), entry.get("node_id")
        if run_id and node_id and _consumes_ledger_record(entry):
            expected[(str(run_id), str(node_id))] += 1
    ambiguous = {key for key in expected if key in ledger and len(ledger[key]) != expected[key]}

    seen: Counter[tuple[str, str]] = Counter()
    for entry in entry_list:
        out.total_entries += 1
        run_id, node_id = entry.get("run_id"), entry.get("node_id")
        key = (str(run_id), str(node_id)) if run_id and node_id else None
        record: LedgerRecord | None = None
        if key is not None and key in ledger:
            if key in ambiguous:
                out.excluded[EXCLUDE_AMBIGUOUS_LEDGER_JOIN] += 1
                continue
            if _consumes_ledger_record(entry):
                record = ledger[key][seen[key]]
                seen[key] += 1
        row, reason = label_entry(entry, ledger_record=record, predicates=predicates)
        if row is None:
            out.excluded[reason or "unknown"] += 1
        else:
            out.rows.append(row)
    return out


__all__ = [
    "EXCLUDE_AMBIGUOUS_LEDGER_JOIN",
    "EXCLUDE_INFRA_LEDGER",
    "EXCLUDE_INFRA_PREFIX",
    "EXCLUDE_MISSING_KEY",
    "EXCLUDE_NO_PROBABILITIES",
    "EXCLUDE_NO_SERVED_PROBABILITY",
    "EXCLUDE_UNKNOWN_STATUS",
    "INFRA_ERROR_CLASSES",
    "LABEL_INSUFFICIENT",
    "LABEL_SUFFICIENT",
    "LEDGER_NON_ATTEMPT_STATUSES",
    "LabelSet",
    "LabelledRow",
    "build_labels",
    "is_infra_error",
    "is_infra_error_text",
    "label_entry",
    "ledger_status_index",
    "scoreboard_predicates",
]
