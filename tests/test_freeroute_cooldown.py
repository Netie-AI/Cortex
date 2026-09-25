"""429-aware routing across calls (live run 2026-09-25: gemini 50x 429, kimi 25x).

The picker used to keep sending a question's next step to the provider that had
just answered 429, because a candidate only went ineligible after its last 3
calls for the *same task* were refusals. Here a refusal that means "come back
later" (429, 402, 503) cools that candidate (per provider under env-direct) for
``CORTEX_FREEROUTE_COOLDOWN_S`` across every task, and the pick reason names the
skip. There is still no in-call fallback: each case below is one request per
``complete``.

No network: a fake transport is installed with ``freeroute.use_transport`` and
the provider keys are fake values set by the test.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from typing import Any

import pytest

from CortexOS.integrations import direct_providers
from CortexOS.integrations import freeroute as fr

GOOGLE = "google:gemini-3-flash-preview"
NVIDIA = "nvidia:moonshotai/kimi-k3"
COOLDOWN_ENV = "CORTEX_FREEROUTE_COOLDOWN_S"
MSGS = [{"role": "user", "content": "how many skus are in stock"}]
_REAL_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY")


class FakeProviders:
    """Transport stand-in. Scripted status per provider label; 200 otherwise."""

    def __init__(self) -> None:
        self.script: dict[str, deque[int]] = {}
        self.sent: list[str] = []

    def refuse(self, label: str, *statuses: int) -> FakeProviders:
        self.script.setdefault(label, deque()).extend(statuses)
        return self

    def __call__(self, method: str, path: str, *, body: Any = None, **_kw: Any) -> tuple[int, Any]:
        model = str((body or {}).get("model") or "")
        self.sent.append(model)
        label = model.partition(":")[0]
        queue = self.script.get(label)
        if queue:
            status = queue.popleft()
            return status, {"error": {"message": f"{label} said {status}"}}
        return 200, {
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": "SELECT 1 FROM inventory"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


def _stamp(out: fr.Completion) -> fr.RouteStamp:
    assert out.stamp is not None
    return out.stamp


@pytest.fixture()
def providers(freeroute_hermetic, monkeypatch) -> Iterator[FakeProviders]:
    for name in _REAL_KEY_ENVS + (fr.SWITCH_ENV, fr.MODELS_ENV, fr.LOCAL_ONLY_ENV, COOLDOWN_ENV):
        monkeypatch.delenv(name, raising=False)
    for p in direct_providers.PROVIDERS:
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-fake")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-fake")
    fake = FakeProviders()
    fr.reset()
    with fr.use_transport(fake, direct_providers.IMPL):
        yield fake
    fr.reset()


def test_after_a_429_the_next_call_goes_to_the_other_provider(providers, monkeypatch) -> None:
    """The measured case: a think step 429s, the SQL step must not go back there."""
    providers.refuse("google", 429)
    first = fr.complete("crew-think", MSGS)
    assert first.ok is False and _stamp(first).status == 429
    assert _stamp(first).requested == GOOGLE

    nxt = fr.complete("crew-insights-sql", MSGS)  # other task: cooldown is per provider
    assert nxt.ok is True
    assert _stamp(nxt).requested == NVIDIA
    assert "cooling (skipped): " + GOOGLE + " (HTTP 429" in _stamp(nxt).pick_reason
    assert providers.sent == [GOOGLE, NVIDIA]  # one request per call, no in-call fallback

    # The window is the operator's: 0 turns cooldown off and exploration resumes.
    monkeypatch.setenv(COOLDOWN_ENV, "0")
    assert fr.pick("crew-insights-sql", fr.arming()).requested == GOOGLE


@pytest.mark.parametrize("status", [402, 503])
def test_budget_and_no_hop_refusals_cool_too_but_a_500_does_not(providers, status) -> None:
    providers.refuse("google", status)
    assert _stamp(fr.complete("t", MSGS)).status == status
    picked = fr.pick("t", fr.arming())
    assert picked.requested == NVIDIA
    assert f"{GOOGLE} (HTTP {status}" in picked.reason

    fr.complete("t", MSGS, pin=NVIDIA)
    providers.refuse("nvidia", 500)
    assert _stamp(fr.complete("t", MSGS, pin=NVIDIA)).status == 500
    # 500 is a scored failure, not a come-back-later; it never cools a provider.
    assert fr._stats("t", [NVIDIA])[NVIDIA].cooling == ""


def test_a_later_answer_from_the_provider_ends_its_cooldown(providers) -> None:
    providers.refuse("google", 429)
    fr.complete("t", MSGS)
    assert fr._stats("t", [GOOGLE])[GOOGLE].cooling.startswith("HTTP 429")
    assert fr.complete("t", MSGS, pin=GOOGLE).ok is True
    assert fr._stats("t", [GOOGLE])[GOOGLE].cooling == ""


def test_every_candidate_cooling_picks_the_one_whose_cooldown_ends_first(providers) -> None:
    providers.refuse("google", 429).refuse("nvidia", 429)
    fr.complete("t", MSGS, pin=GOOGLE)
    fr.complete("t", MSGS, pin=NVIDIA)
    picked = fr.pick("t", fr.arming())
    assert picked.requested == GOOGLE  # refused first, so its window closes first
    assert "every eligible candidate cooling" in picked.reason
    assert "cooldown ends first" in picked.reason
    assert "HTTP 429" in picked.reason


def test_env_direct_cools_the_whole_provider_not_one_model(providers, monkeypatch) -> None:
    """One key, one quota: a 429 on one Gemini model says the other will 429 too."""
    monkeypatch.setenv("CORTEX_DIRECT_GOOGLE_MODELS", "gemini-a,gemini-b")
    providers.refuse("google", 429)
    assert _stamp(fr.complete("t", MSGS, pin="google:gemini-a")).status == 429
    picked = fr.pick("t", fr.arming())
    assert picked.candidates[:2] == ("google:gemini-a", "google:gemini-b")
    assert picked.requested == NVIDIA
    assert "google:gemini-b (HTTP 429" in picked.reason


def test_a_benchmark_split_429_still_cools(providers) -> None:
    """Cooldown is a fact about the provider now, not training data."""
    providers.refuse("google", 429)
    with fr.journal(split=fr.SPLIT_BENCHMARK):
        fr.complete("t", MSGS)
    picked = fr.pick("t", fr.arming())
    assert picked.requested == NVIDIA
    assert "cooling (skipped)" in picked.reason
