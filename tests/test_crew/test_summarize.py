"""The compaction decision must be honest about what it would actually drop.

Every assertion here is on the decision an operator (or the runtime) would act on -
the counts and the reason text - not on an internal intermediate. A decision that says
"compacted" while dropping nothing, or one that quietly drops the charter, would pass a
test that only looked at "did it return something".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from CortexOS.crew import context as ctxmod
from CortexOS.crew.summarize import (
    CHARS_PER_TOKEN,
    CompactionDecision,
    charter_span,
    estimate_row_tokens,
    estimate_tokens,
    rows_to_archive,
    should_compact,
)
from CortexOS.crew.workspace import SpaceWorkspace


def _row(seq: int, role: str = "user", chars: int = 400) -> dict[str, Any]:
    return {"seq": seq, "role": role, "content": "x" * chars}


def _transcript(n: int, *, chars: int = 400, charter: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if charter:
        rows.append({"seq": 1, "role": "system", "content": "rules"})
    for i in range(n):
        rows.append(_row(len(rows) + 1, "user" if i % 2 == 0 else "assistant", chars))
    return rows


# -- the estimate -------------------------------------------------------------


def test_estimate_is_monotonic_and_never_under_the_character_count() -> None:
    small = {"role": "user", "content": "hi"}
    big = {"role": "user", "content": "hi" * 500}
    assert estimate_row_tokens(big) > estimate_row_tokens(small)
    # Conservative: rounds up, and charges per-message framing on top.
    assert estimate_row_tokens(big) >= len(big["content"]) / CHARS_PER_TOKEN

    rows = _transcript(5)
    grown = [*rows, _row(99)]
    assert estimate_tokens(grown) > estimate_tokens(rows)
    assert estimate_tokens([]) == 0


def test_estimate_charges_for_tiny_turns_so_chatter_cannot_look_free() -> None:
    chatter = [{"role": "user", "content": "ok"} for _ in range(100)]
    assert estimate_tokens(chatter) >= 100


def test_estimate_handles_missing_and_non_string_content() -> None:
    assert estimate_row_tokens({"role": "user"}) > 0
    assert estimate_row_tokens({"role": "tool", "content": None}) > 0
    assert estimate_row_tokens({"role": "tool", "content": {"ok": True}}) > 0


# -- charter protection -------------------------------------------------------


def test_charter_span_counts_only_the_leading_run() -> None:
    rows = [
        {"role": "system", "content": "charter"},
        {"role": "system", "content": "roster"},
        {"role": "user", "content": "go"},
        {"role": "system", "content": "Run failed: boom"},
    ]
    assert charter_span(rows) == 2
    assert charter_span([{"role": "user", "content": "go"}]) == 0
    assert charter_span([]) == 0


def test_charter_is_never_dropped_even_when_it_is_the_thing_that_overflows() -> None:
    rows = [{"seq": 1, "role": "system", "content": "R" * 40_000}]
    rows += [_row(i + 2, chars=400) for i in range(20)]

    decision = should_compact(rows, budget=2000)

    assert decision.should_compact is True
    assert decision.drop_start == 1  # index 0 is the charter
    dropped = rows_to_archive(rows, decision)
    assert all(row["role"] != "system" for row in dropped)
    assert rows[0] not in dropped
    # Dropping everything legal still does not fit: say so instead of pretending.
    assert decision.sufficient is False
    assert decision.projected_tokens > decision.budget_tokens
    assert "shed size another way" in decision.reason


def test_mid_transcript_system_notes_are_droppable() -> None:
    rows = _transcript(20)
    rows[3]["role"] = "system"
    rows[3]["content"] = "Run failed: boom" + "x" * 400

    decision = should_compact(rows, budget=2000)

    assert decision.should_compact is True
    dropped = rows_to_archive(rows, decision)
    assert any(row["content"].startswith("Run failed") for row in dropped)


# -- the decision -------------------------------------------------------------


def test_under_budget_decides_nothing_and_still_reports_the_numbers() -> None:
    rows = _transcript(4, chars=40)

    decision = should_compact(rows, budget=10_000)

    assert decision.should_compact is False
    assert decision.drop_count == 0
    assert decision.drop_start is None
    assert decision.through_seq is None
    assert decision.projected_tokens == decision.estimated_tokens
    assert decision.sufficient is True
    assert "under budget" in decision.reason
    assert rows_to_archive(rows, decision) == []


def test_drops_the_minimum_needed_to_reach_the_target_headroom() -> None:
    # 1 charter turn (6 tokens) + 20 turns of 400 chars (104 tokens each) = 2086.
    rows = _transcript(20, chars=400)
    assert estimate_tokens(rows) == 2086

    decision = should_compact(rows, budget=2000)

    # target = 2000 * 0.8 = 1600; five drops (520 tokens) is the first that fits.
    assert decision.drop_count == 5
    assert decision.projected_tokens == 1566
    assert decision.sufficient is True
    assert decision.kept_charter == 1
    assert decision.kept_tail == len(rows) - 1 - 5
    assert decision.drop_start == 1
    assert decision.through_index == 5
    assert decision.through_seq == rows[5]["seq"]
    assert "dropping 5 of" in decision.reason


def test_compacting_leaves_headroom_so_the_next_turn_does_not_trip_again() -> None:
    rows = _transcript(20, chars=400)
    decision = should_compact(rows, budget=2000)
    assert decision.projected_tokens <= int(2000 * 0.8)


def test_recent_tail_always_survives() -> None:
    rows = _transcript(30, chars=4000)

    decision = should_compact(rows, budget=100, keep_tail=8)

    kept = [row for row in rows if row not in rows_to_archive(rows, decision)]
    assert kept[0] is rows[0]
    assert kept[-8:] == rows[-8:]
    assert decision.kept_tail == 8


def test_refusal_when_nothing_may_be_dropped_says_why() -> None:
    # Charter plus a tail shorter than keep_tail: legal to drop nothing at all.
    rows = _transcript(6, chars=8000)

    decision = should_compact(rows, budget=100, keep_tail=8)

    assert decision.should_compact is False
    assert decision.drop_count == 0
    assert decision.sufficient is False
    assert "nothing may be dropped" in decision.reason
    assert "keep_tail=8" in decision.reason
    assert str(decision.estimated_tokens) in decision.reason


def test_empty_transcript_is_a_no_op_not_a_crash() -> None:
    decision = should_compact([], budget=1000)
    assert decision.should_compact is False
    assert decision.row_count == 0
    assert decision.kept_tail == 0


def test_through_seq_is_none_when_rows_carry_no_seq() -> None:
    rows: list[dict[str, Any]] = [{"role": "system", "content": "rules"}]
    rows += [{"role": "user", "content": "x" * 400} for _ in range(20)]

    decision = should_compact(rows, budget=2000)

    assert decision.should_compact is True
    assert decision.through_seq is None


def test_bad_inputs_fail_closed() -> None:
    rows = _transcript(20)
    for budget in (0, -1):
        with pytest.raises(ValueError, match="positive token count"):
            should_compact(rows, budget=budget)
    with pytest.raises(ValueError, match="keep_tail"):
        should_compact(rows, budget=1000, keep_tail=0)
    with pytest.raises(ValueError, match="target_ratio"):
        should_compact(rows, budget=1000, target_ratio=0.0)
    with pytest.raises(ValueError, match="target_ratio"):
        should_compact(rows, budget=1000, target_ratio=1.5)


# -- the decision cannot lie --------------------------------------------------


def test_a_decision_cannot_claim_a_compaction_that_dropped_nothing() -> None:
    fields: dict[str, Any] = {
        "drop_start": 1,
        "through_index": 1,
        "through_seq": 2,
        "kept_charter": 1,
        "kept_tail": 8,
        "row_count": 9,
        "estimated_tokens": 100,
        "projected_tokens": 100,
        "budget_tokens": 50,
        "sufficient": False,
        "reason": "made up",
    }
    with pytest.raises(ValueError, match="drop_count is 0"):
        CompactionDecision(should_compact=True, drop_count=0, **fields)
    with pytest.raises(ValueError, match="should_compact is False"):
        CompactionDecision(should_compact=False, drop_count=3, **fields)


def test_as_dict_carries_the_counts_for_the_event_bus() -> None:
    decision = should_compact(_transcript(20, chars=400), budget=2000)
    payload = decision.as_dict()
    assert payload["drop_count"] == decision.drop_count
    assert payload["projected_tokens"] == decision.projected_tokens
    assert payload["reason"] == decision.reason


def test_rows_to_archive_refuses_a_transcript_that_changed_since_the_decision() -> None:
    rows = _transcript(20, chars=400)
    decision = should_compact(rows, budget=2000)
    grown = [*rows, _row(99)]

    with pytest.raises(ValueError, match="transcript changed"):
        rows_to_archive(grown, decision)


def test_dropped_rows_are_contiguous_and_oldest_first() -> None:
    rows = _transcript(20, chars=400)
    decision = should_compact(rows, budget=2000)

    dropped = rows_to_archive(rows, decision)

    assert len(dropped) == decision.drop_count
    assert [row["seq"] for row in dropped] == [
        row["seq"] for row in rows[1 : 1 + decision.drop_count]
    ]
    assert dropped[-1]["seq"] == decision.through_seq


# -- it fits the writer it is meant to drive ----------------------------------


def test_decision_output_feeds_context_archive_turns(tmp_path: Path) -> None:
    """The decision is only useful if context.archive_turns accepts it verbatim."""
    rows = _transcript(20, chars=400)
    decision = should_compact(rows, budget=2000)
    ws = SpaceWorkspace(tmp_path / "ws")

    assert decision.through_seq is not None
    rel = ctxmod.archive_turns(
        ws,
        rows_to_archive(rows, decision),
        through_seq=decision.through_seq,
    )

    body = ws.read(rel, limit=2000)
    assert f"compacted through seq {decision.through_seq}" in body
    assert ws.read("archives/LATEST.txt").endswith(rel)
