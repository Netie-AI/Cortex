"""The engine never presents an absent third-party runtime as a working backend.

Cortex is the orchestrator and the decisive layer. A backend is a swappable
execution substrate underneath it — never the brain, and never a dependency the
engine quietly assumes is present.

``just_works`` picked a backend from hardware alone and returned ``ok: True``
with "Selected Ollama" whether or not Ollama was installed, running, or
reachable. ``bakeoff.probe_backend`` — the honest check — already existed and
was not consulted. A plan that reports success for a runtime that is not there
is the silent-fallback lie R-0011 exists to prevent: the operator reads
"Selected Ollama" as "Ollama is serving", and only finds out at the first real
generation.
"""

from __future__ import annotations

from typing import Any

import pytest

from CortexOS.engine.just_works import just_works

CPU_ONLY: dict[str, Any] = {"vram_gb": 0, "nvidia": {"present": False}}


def _probe_stub(available: bool):
    def _probe(backend_id: str) -> dict[str, Any]:
        return {
            "id": backend_id,
            "ok": available,
            "error": "" if available else "ConnectionRefusedError: not running",
        }

    return _probe


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    import CortexOS.engine.bakeoff as bakeoff

    monkeypatch.setattr(bakeoff, "probe_backend", _probe_stub(False))


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    import CortexOS.engine.bakeoff as bakeoff

    monkeypatch.setattr(bakeoff, "probe_backend", _probe_stub(True))


def test_absent_backend_is_reported_as_unavailable(offline: None) -> None:
    """The plan must say the runtime is not there, in the payload."""
    plan = just_works(CPU_ONLY)

    assert plan["config"]["backend_available"] is False


def test_absent_backend_says_so_in_plain_language(offline: None) -> None:
    """A machine-readable flag nobody renders is the same lie in a smaller font."""
    plan = just_works(CPU_ONLY)

    human = " ".join(plan["human"]).lower()
    assert "not reachable" in human or "not running" in human or "not installed" in human


def test_present_backend_is_reported_available(online: None) -> None:
    """R-0005 control: a reachable backend must not be reported as missing."""
    plan = just_works(CPU_ONLY)

    assert plan["config"]["backend_available"] is True


def test_orchestration_stays_ours_regardless_of_backend(offline: None) -> None:
    """The decisive layer is Cortex. A missing substrate does not remove it.

    An absent backend degrades *generation*, not routing, governance or the
    manifest — so the plan still reports Cortex as the agents runtime rather
    than collapsing to "nothing works".
    """
    plan = just_works(CPU_ONLY)

    assert plan["config"]["agents_runtime"] == "cortex"
    assert plan["ok"] is True  # planning succeeded; availability is reported separately


def test_probe_can_be_skipped_without_claiming_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skipping the probe must report unknown, never a cheerful default.

    The probe costs a network timeout, so callers may opt out — but "we did not
    look" and "it is there" are different answers and must not share a value.
    """
    plan = just_works(CPU_ONLY, probe=False)

    assert plan["config"]["backend_available"] is None


def test_probe_failure_never_breaks_planning(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe that raises must degrade to unknown, not take the endpoint down."""
    import CortexOS.engine.bakeoff as bakeoff

    def _boom(backend_id: str) -> dict[str, Any]:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(bakeoff, "probe_backend", _boom)

    plan = just_works(CPU_ONLY)

    assert plan["ok"] is True
    assert plan["config"]["backend_available"] is None
