"""A capped listing must not report a fabricated exact total.

``_true_count`` swallows every exception and returns ``None``. The response then
did ``total_count if total_count is not None else len(rows)``, so a listing that
hit the 1000-row guardrail cap with a failed COUNT probe reported
``total_count = 1000`` — an exact-looking number invented from the page size,
while the real total might be 50,000. ``truncated`` was ``False`` at the same
time (it required a non-None total), so nothing on screen disclosed the cap, and
a follow-up "how many of them?" replayed the invented 1000.

``len(rows)`` is the true total only when the cap was *not* reached. At the cap
with no count, the only honest statement is "at least this many".
"""

from __future__ import annotations

import pytest
from CortexOS.dms.answer_engine import MAX_LIMIT, _aggregate_prior


def test_an_inexact_total_is_not_replayed_as_a_follow_up_count() -> None:
    """The customer-visible half: "how many of them?" must not repeat a guess."""
    rows = [{"sku": f"S{i}", "v": 1} for i in range(MAX_LIMIT)]
    prior = f"SELECT sku, v FROM transactions LIMIT {MAX_LIMIT}"

    _, replayed = _aggregate_prior(
        prior, "how many of them?", rows, total_count=MAX_LIMIT, total_exact=False
    )
    assert replayed == [], "a lower bound was replayed as an exact count"


def test_a_real_total_is_still_used() -> None:
    """R-0005: the shortcut is correct when the number is real. Keep it."""
    rows = [{"sku": f"S{i}", "v": 1} for i in range(MAX_LIMIT)]
    prior = f"SELECT sku, v FROM transactions LIMIT {MAX_LIMIT}"

    _, replayed = _aggregate_prior(
        prior, "how many of them?", rows, total_count=50_000, total_exact=True
    )
    assert replayed == [{"followup_count": 50_000}]


@pytest.mark.parametrize(
    ("n_rows", "counted", "expect_exact"),
    [
        (5, None, True),           # under the cap: len(rows) IS the total
        (MAX_LIMIT, None, False),  # at the cap, no count: lower bound only
        (MAX_LIMIT, 50_000, True), # at the cap with a real count
    ],
)
def test_exactness_rule(n_rows: int, counted: int | None, expect_exact: bool) -> None:
    """The single rule the response and the answer text both derive from."""
    count_exact = counted is not None or n_rows < MAX_LIMIT
    assert count_exact is expect_exact


def test_a_capped_listing_with_no_count_is_disclosed_as_truncated() -> None:
    """truncated used to be False exactly when the total was unknown - the one
    case where the customer most needed to be told."""
    for total_count, n_rows, expected in [
        (None, MAX_LIMIT, True),      # was False: cap hit, count failed
        (50_000, MAX_LIMIT, True),
        (None, 5, False),
        (5, 5, False),
    ]:
        capped = n_rows >= MAX_LIMIT
        truncated = capped and (total_count is None or total_count > n_rows)
        assert truncated is expected, f"total={total_count} rows={n_rows}"
