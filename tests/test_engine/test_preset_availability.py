"""The engine must not offer an architecture it cannot execute.

`PRESET_CATALOG` advertises eight architectures. Two of them — `langgraph` and
`langchain` — resolve to `marketplace_*` runners whose entire implementation is

    return {"ok": False, "runner": runner, "status_code": 501,
            "error": "adapter_unavailable"}

They can never succeed. Measured consequence on this machine, from
`data/engine/action_events.db`:

    routine_langgraph  langgraph  failed     327
    routine_minimal    minimal    succeeded  118

A routine pinned to `langgraph` burned 327 consecutive runs discovering, each
time, something knowable before the first one. The governor's error-streak
pause is the wrong instrument here: it exists for a routine that *started*
working and degraded, not for one that is structurally impossible.

This is also the engine-independence rule. Cortex orchestrates; a third-party
framework is a substrate it may sit on, never a capability it claims to have
while lacking the adapter.
"""

from __future__ import annotations

import pytest

from CortexOS.execution import architecture_presets as ap


def test_every_advertised_preset_declares_availability() -> None:
    """Availability is a fact about the runner, so every preset must state it."""
    for preset in ap.catalog():
        assert "available" in preset, f"{preset['id']} does not say whether it can run"


def test_marketplace_presets_are_marked_unavailable() -> None:
    """The two that resolve to a permanent 501 must say so."""
    by_id = {p["id"]: p for p in ap.catalog()}

    assert by_id["langgraph"]["available"] is False
    assert by_id["langchain"]["available"] is False


def test_the_engines_own_architectures_stay_available() -> None:
    """R-0005 control — this must not disable the presets that do work."""
    by_id = {p["id"]: p for p in ap.catalog()}

    for pid in ("minimal", "sequential", "dag", "rag", "memory", "computer_control"):
        assert by_id[pid]["available"] is True, f"{pid} was wrongly marked unavailable"


def test_availability_is_derived_from_the_runner_not_hand_maintained() -> None:
    """A hand-kept list drifts from the dispatch chain the moment one changes.

    Availability must come from the same set `execute_run_plan` branches on, so
    implementing an adapter flips the flag with no second edit — and forgetting
    to implement one cannot be papered over by editing a literal.
    """
    from CortexOS.execution.run_plan import IMPLEMENTED_RUNNERS

    for preset in ap.catalog():
        expected = preset["runner"] in IMPLEMENTED_RUNNERS
        assert preset["available"] is expected, (
            f"{preset['id']}: available={preset['available']} but runner "
            f"{preset['runner']!r} implemented={expected}"
        )


def test_unavailable_preset_is_refused_up_front() -> None:
    """Fail once, at selection, with a reason — not silently on every run."""
    with pytest.raises(ap.PresetUnavailable) as exc:
        ap.resolve_runner("langgraph", require_available=True)

    assert "langgraph" in str(exc.value)
    assert "adapter" in str(exc.value).lower()


def test_available_preset_resolves_normally() -> None:
    """R-0005 control: the check must not refuse legitimate work."""
    plan = ap.resolve_runner("minimal", require_available=True)

    assert plan["runner"] == "dag_single"
    assert plan["available"] is True


def test_race_never_probes_an_unexecutable_preset() -> None:
    """A race spends a real budget per candidate.

    Cold start does not include the marketplace presets today, but nothing
    stopped a stored history row from ranking one back in — and every probe of
    a permanent 501 is wasted budget plus a recorded loss that skews the family
    score.
    """
    from CortexOS.execution.race_router import rank_candidates

    unavailable = {p["id"] for p in ap.catalog() if not p["available"]}
    assert not (set(rank_candidates("any-family")) & unavailable)
