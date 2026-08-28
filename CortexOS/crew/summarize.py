"""When a crew transcript must be compacted - the decision, never the writing.

``CortexOS.crew.context`` already owns HOW context is shed: ``offload_tool_result``
evicts an oversized tool result to the space workspace, ``archive_turns`` writes older
turns to disk. What was missing is WHEN. Compaction only ever fired because the model
chose to call ``compact_conversation``, so a run whose model never called it grew its
prompt every turn until the provider rejected the request and the whole run died with
a context-length error that looked like a provider outage.

DeepAgents MIT pattern (keep the working context bounded), absorbed as the policy half
only and written as original Cortex code. Pure: plain dicts in, a decision out. No I/O,
no scheduler, no second dag_runner - the decision layer stays dag_runner + manifest +
ledger, and this module only says whether a prompt is too long and by how much.

Rows are the crew transcript shape (``CrewStore.list_messages``): mappings with at
least ``role`` and ``content``, optionally ``seq``, ordered oldest first.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

#: Characters per token. Deliberately coarse - see ``estimate_row_tokens``.
CHARS_PER_TOKEN = 4

#: Framing every provider adds per message (role marker, delimiters). Counted so a
#: transcript of many tiny turns is not estimated as nearly free.
MESSAGE_OVERHEAD_TOKENS = 4

#: Turns that always survive a compaction. Matches the keep used by the runtime's
#: model-invoked compaction so both paths leave the same amount of recent context.
KEEP_TAIL = 8

#: Roles whose *leading* run is the charter: the agent's rules. Dropping those does
#: not shorten a conversation, it silently changes the agent's instructions, and the
#: run then misbehaves in a way no transcript explains.
CHARTER_ROLES = frozenset({"system", "charter", "developer"})

#: Compact down to this fraction of the budget rather than to the budget exactly.
#: Landing on the ceiling means the very next turn trips the check again, which turns
#: compaction into a per-turn archive-write loop.
TARGET_RATIO = 0.8


@dataclass(frozen=True)
class CompactionDecision:
    """What compaction would do, with the counts that prove it.

    ``should_compact`` and ``drop_count`` are validated against each other on
    construction: a decision cannot claim a compaction happened while dropping
    nothing, because a transcript that records a compaction which dropped zero turns
    reads as "context was reduced" forever afterwards and hides the real overflow.
    """

    should_compact: bool
    #: Number of oldest droppable turns to archive and remove.
    drop_count: int
    #: Index of the first dropped row, or None when nothing is dropped.
    drop_start: int | None
    #: Index of the last dropped row, or None when nothing is dropped.
    through_index: int | None
    #: ``seq`` of the last dropped row when the rows carry one; the archive and the
    #: space's ``compact_seq`` floor are keyed on this.
    through_seq: int | None
    #: Leading charter turns kept untouched.
    kept_charter: int
    #: Recent turns kept untouched.
    kept_tail: int
    #: Rows the decision was measured against; ``rows_to_archive`` re-checks it.
    row_count: int
    estimated_tokens: int
    projected_tokens: int
    budget_tokens: int
    #: False when even the maximum legal drop leaves the prompt over budget. The
    #: caller must then shed differently (offload tool results, trim the tail) - a
    #: decision that silently under-delivers would let the run die anyway.
    sufficient: bool
    #: Why, in operator-readable words. A refusal must carry its reason (KB R-0011).
    reason: str

    def __post_init__(self) -> None:
        if self.should_compact and self.drop_count <= 0:
            raise ValueError("should_compact is True but drop_count is 0")
        if not self.should_compact and self.drop_count:
            raise ValueError("drop_count is set but should_compact is False")
        if self.drop_count < 0:
            raise ValueError("drop_count cannot be negative")

    def as_dict(self) -> dict[str, Any]:
        """Event-bus / audit shape."""
        return asdict(self)


def estimate_row_tokens(row: Mapping[str, Any]) -> int:
    """Approximate tokens for one transcript row.

    This is characters divided by ``CHARS_PER_TOKEN``, rounded **up**, plus a fixed
    per-message overhead. It is not a tokenizer and must not be sold as one; adding a
    tokenizer dependency to decide whether to archive old turns is not worth the
    install, the model-specific vocabularies, or the import cost on every turn.

    An approximation is safe here because the decision only needs two properties:
    monotonic (more text never estimates smaller, so the check cannot be defeated by
    appending) and conservative (it rounds up and charges per-message framing, so it
    fires before the provider's real limit rather than after it). Over-firing costs an
    archive write; under-firing costs the run.
    """
    content = row.get("content")
    text = content if isinstance(content, str) else ("" if content is None else str(content))
    return MESSAGE_OVERHEAD_TOKENS + -(-len(text) // CHARS_PER_TOKEN)


def estimate_tokens(rows: Sequence[Mapping[str, Any]]) -> int:
    """Approximate tokens for a whole transcript. See ``estimate_row_tokens``."""
    return sum(estimate_row_tokens(row) for row in rows)


def charter_span(rows: Sequence[Mapping[str, Any]]) -> int:
    """How many leading rows are charter turns and therefore undroppable.

    Only the *leading* run counts. A system row further down a crew transcript is an
    event ("Run failed: ...", "Run budget exhausted"), not a rule, and holding those
    forever would make long runs uncompactable.
    """
    count = 0
    for row in rows:
        role = str(row.get("role") or "").strip().lower()
        if role not in CHARTER_ROLES:
            break
        count += 1
    return count


def should_compact(
    rows: Sequence[Mapping[str, Any]],
    budget: int,
    *,
    keep_tail: int = KEEP_TAIL,
    target_ratio: float = TARGET_RATIO,
) -> CompactionDecision:
    """Decide whether this transcript must be compacted, and by how much.

    Drops only the oldest turns that sit between the charter and the recent tail, and
    only as many as the budget requires. Returns a decision that always carries the
    real counts, including when it decides to do nothing and why.
    """
    if budget <= 0:
        raise ValueError("budget must be a positive token count")
    if keep_tail < 1:
        raise ValueError("keep_tail must be at least 1")
    if not 0.0 < target_ratio <= 1.0:
        raise ValueError("target_ratio must be in (0, 1]")

    rows = list(rows)
    sizes = [estimate_row_tokens(row) for row in rows]
    total = sum(sizes)
    head = charter_span(rows)
    tail_kept = min(len(rows) - head, keep_tail) if len(rows) > head else 0

    if total <= budget:
        return CompactionDecision(
            should_compact=False,
            drop_count=0,
            drop_start=None,
            through_index=None,
            through_seq=None,
            kept_charter=head,
            kept_tail=tail_kept,
            row_count=len(rows),
            estimated_tokens=total,
            projected_tokens=total,
            budget_tokens=budget,
            sufficient=True,
            reason=f"under budget: {total}/{budget} estimated tokens over {len(rows)} turns",
        )

    droppable = max(0, len(rows) - keep_tail - head)
    if droppable == 0:
        return CompactionDecision(
            should_compact=False,
            drop_count=0,
            drop_start=None,
            through_index=None,
            through_seq=None,
            kept_charter=head,
            kept_tail=tail_kept,
            row_count=len(rows),
            estimated_tokens=total,
            projected_tokens=total,
            budget_tokens=budget,
            sufficient=False,
            reason=(
                f"over budget ({total}/{budget} estimated tokens) but nothing may be"
                f" dropped: {len(rows)} turns, {head} charter, keep_tail={keep_tail}."
                " Shed size another way (offload tool results) rather than dropping"
                " the charter or the recent tail"
            ),
        )

    target = max(1, int(budget * target_ratio))
    projected = total
    drop = 0
    while drop < droppable and projected > target:
        projected -= sizes[head + drop]
        drop += 1

    through_index = head + drop - 1
    sufficient = projected <= budget
    if sufficient:
        reason = (
            f"over budget ({total}/{budget} estimated tokens); dropping {drop} of"
            f" {droppable} droppable turns leaves {projected}, keeping {head} charter"
            f" and the last {len(rows) - head - drop} turns"
        )
    else:
        reason = (
            f"over budget ({total}/{budget} estimated tokens); dropping all {drop}"
            f" droppable turns only reaches {projected}. The charter plus the last"
            f" {keep_tail} turns do not fit on their own - shed size another way too"
        )
    return CompactionDecision(
        should_compact=True,
        drop_count=drop,
        drop_start=head,
        through_index=through_index,
        through_seq=_seq_of(rows[through_index]),
        kept_charter=head,
        kept_tail=len(rows) - head - drop,
        row_count=len(rows),
        estimated_tokens=total,
        projected_tokens=projected,
        budget_tokens=budget,
        sufficient=sufficient,
        reason=reason,
    )


def rows_to_archive(
    rows: Sequence[Mapping[str, Any]],
    decision: CompactionDecision,
) -> list[dict[str, Any]]:
    """The exact rows ``decision`` says to drop, ready for ``context.archive_turns``.

    Refuses when ``rows`` is not the transcript the decision was measured against.
    Between deciding and acting the space can gain turns, and slicing a grown
    transcript by stale indices would archive the wrong turns and set the compaction
    floor past messages that were never written to the archive - a silent hole in the
    only durable record of the space.
    """
    if len(rows) != decision.row_count:
        raise ValueError(
            f"transcript changed since the decision: {len(rows)} rows now,"
            f" {decision.row_count} when decided; re-run should_compact"
        )
    if not decision.should_compact or decision.drop_start is None:
        return []
    start = decision.drop_start
    return [dict(row) for row in rows[start : start + decision.drop_count]]


def _seq_of(row: Mapping[str, Any]) -> int | None:
    raw = row.get("seq")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
