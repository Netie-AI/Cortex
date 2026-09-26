"""Cortex #270 ROUTER-2: opt-in cost-ordered ladder with checker-driven step-up.

No network: a scripted transport stands in for env-direct providers (and for an
OpenVault-shaped body where served_local matters). Provider keys are fake
values set by the test. Every case asserts what the caller receives: the text,
``ok``, and the rungs on the stamp / Insights fingerprint.
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
MISTRAL = "mistral:mistral-medium-latest"
MSGS = [{"role": "user", "content": "how many skus are in stock"}]
GOOD_SQL = "SELECT COUNT(*) FROM inventory"
_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY")


def _is_select(text: str) -> bool:
    return text.strip().upper().startswith("SELECT")


class Script:
    """Per-provider scripted replies. ('ok', text, tokens) | ('status', code)."""

    def __init__(self) -> None:
        self.replies: dict[str, deque[tuple[Any, ...]]] = {}
        self.sent: list[str] = []
        self.extra: dict[str, dict[str, Any]] = {}

    def on(self, label: str, *replies: tuple[Any, ...]) -> Script:
        self.replies.setdefault(label, deque()).extend(replies)
        return self

    def __call__(self, method: str, path: str, *, body: Any = None, **_kw: Any) -> tuple[int, Any]:
        model = str((body or {}).get("model") or "")
        self.sent.append(model)
        label = model.partition(":")[0]
        queue = self.replies.get(label)
        reply = queue.popleft() if queue else ("ok", GOOD_SQL, 10)
        if reply[0] == "status":
            return reply[1], {"error": {"message": f"{label} said {reply[1]}"}}
        out = {
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": reply[1]}}],
            "usage": {"total_tokens": reply[2]},
        }
        out.update(self.extra.get(label, {}))
        return 200, out


@pytest.fixture()
def script(freeroute_hermetic, monkeypatch) -> Iterator[Script]:
    for name in _KEY_ENVS + (
        fr.SWITCH_ENV,
        fr.MODELS_ENV,
        fr.LOCAL_ONLY_ENV,
        fr.LADDER_ENV,
        "CORTEX_FREEROUTE_COOLDOWN_S",
    ):
        monkeypatch.delenv(name, raising=False)
    for p in direct_providers.PROVIDERS:
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-fake")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-fake")
    monkeypatch.setenv("MISTRAL_API_KEY", "mi-fake")
    fake = Script()
    fr.reset()
    with fr.use_transport(fake, direct_providers.IMPL):
        yield fake
    fr.reset()


def _seed_cost(script: Script, model: str, tokens: int) -> None:
    """One pinned, accepted call so the store holds a measured cost for ``model``."""
    script.on(model.partition(":")[0], ("ok", GOOD_SQL, tokens))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, pin=model, pin_source="test")
    assert out.ok


def test_rejected_answer_steps_up_one_rung_and_every_rung_is_stamped(script) -> None:
    script.on("google", ("ok", "I think about 40 SKUs.", 10))
    with fr.journal() as stamps:
        out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=2)
    assert out.ok is True
    assert out.text == GOOD_SQL
    assert script.sent == [GOOGLE, NVIDIA]
    assert out.stamp is not None
    ladder = out.stamp.ladder
    assert [s["requested"] for s in ladder] == [GOOGLE, NVIDIA]
    assert [s["verdict"] for s in ladder] == ["rejected", "accepted"]
    assert [s["final"] for s in ladder] == [False, True]
    assert [s["rung"] for s in ladder] == [fr.RUNG_CLOUD, fr.RUNG_CLOUD]
    assert len(stamps) == 2 and stamps[-1] is out.stamp
    assert "[ladder: 2 rung(s) tried]" in out.stamp.line()
    assert out.stamp.line().startswith("NOT OpenVault FreeRoute (env-direct)")


def test_step_limit_ends_in_an_honest_refusal_not_the_best_guess(script) -> None:
    for label in ("google", "nvidia", "mistral"):
        script.on(label, ("ok", f"{label} guesses 40", 10))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=1)
    assert out.ok is False
    assert out.text == ""
    assert script.sent == [GOOGLE, NVIDIA]
    assert "no rung passed the checker (2 tried; step limit 1 reached)" in out.reason
    assert out.stamp is not None
    assert [s["final"] for s in out.stamp.ladder] == [False, False]


@pytest.mark.parametrize("status", [429, 402, 503])
def test_no_step_up_on_budget_or_refusal_status(script, status) -> None:
    script.on("google", ("status", status))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=2)
    assert out.ok is False
    assert out.text == ""
    assert script.sent == [GOOGLE]
    assert "stopped at rung 1 on a refusal status, no step-up" in out.reason
    assert out.stamp is not None
    assert out.stamp.ladder[0]["verdict"] == f"refused (HTTP {status})"


def test_server_error_is_not_a_refusal_and_steps_up(script) -> None:
    script.on("google", ("status", 500))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=1)
    assert out.ok is True
    assert out.text == GOOD_SQL
    assert script.sent == [GOOGLE, NVIDIA]
    assert out.stamp is not None
    assert out.stamp.ladder[0]["verdict"] == "failed (HTTP 500)"


def test_candidates_are_ordered_cheapest_measured_cost_first(script) -> None:
    _seed_cost(script, GOOGLE, 900)
    _seed_cost(script, NVIDIA, 40)
    _seed_cost(script, MISTRAL, 300)
    script.sent.clear()
    rungs, _ = fr.ladder_order("gen-ask-sql", fr.arming())
    assert [r.requested for r in rungs] == [NVIDIA, MISTRAL, GOOGLE]
    assert "40.0 tokens" in rungs[0].reason
    script.on("nvidia", ("ok", "about forty", 40))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=2)
    assert out.ok and out.text == GOOD_SQL
    assert script.sent == [NVIDIA, MISTRAL]


def test_a_cooling_provider_is_not_a_rung(script) -> None:
    script.on("google", ("status", 429))
    first = fr.complete("gen-ask-sql", MSGS, accept=_is_select, pin=GOOGLE, pin_source="test")
    assert first.ok is False
    script.sent.clear()
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=2)
    assert out.ok is True
    assert GOOGLE not in script.sent
    assert out.stamp is not None
    assert f"left out: {GOOGLE} (cooling (HTTP 429" in out.stamp.pick_reason


def test_without_opt_in_one_request_and_no_ladder_field(script) -> None:
    script.on("google", ("ok", "about forty", 10))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select)
    assert out.ok is False
    assert script.sent == [GOOGLE]
    assert out.stamp is not None
    assert out.stamp.ladder == [] and out.stamp.rung == ""
    assert "ladder" not in fr.router_fingerprint(out.stamp)


def test_env_opt_in_and_a_bad_env_value_is_named_not_silent(script, monkeypatch) -> None:
    monkeypatch.setenv(fr.LADDER_ENV, "1")
    script.on("google", ("ok", "about forty", 10))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select)
    assert out.ok is True and script.sent == [GOOGLE, NVIDIA]

    monkeypatch.setenv(fr.LADDER_ENV, "lots")
    script.sent.clear()
    script.on("nvidia", ("ok", "about forty", 10))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select)
    assert len(script.sent) == 1
    assert out.stamp is not None
    assert "CORTEX_FREEROUTE_LADDER='lots' ignored" in out.stamp.pick_reason


class _Count:
    name = "rule:count-literal"
    kind = fr.RUNG_DETERMINISTIC

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.calls = 0

    def solve(self, task: str, messages: list[dict[str, Any]]) -> str | None:
        self.calls += 1
        return self.answer


def test_deterministic_rung_answer_is_checked_before_it_is_used(script) -> None:
    solver = _Count(GOOD_SQL)
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, solvers=[solver])
    assert out.ok is True and out.text == GOOD_SQL
    assert script.sent == []
    assert out.stamp is not None
    step = out.stamp.ladder[0]
    assert step["rung"] == fr.RUNG_DETERMINISTIC and step["final"] is True
    assert step["served_model"] == "rule:count-literal" and step["served_local"] is False
    assert "(no model call)" in out.stamp.line()


def test_rejected_deterministic_rung_moves_up_to_a_model(script) -> None:
    solver = _Count("forty, roughly")
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, solvers=[solver])
    assert solver.calls == 1
    assert out.ok is True and out.text == GOOD_SQL
    assert script.sent == [GOOGLE]
    assert out.stamp is not None
    assert [s["verdict"] for s in out.stamp.ladder] == ["rejected", "accepted"]


def test_solver_without_a_checker_never_answers(script) -> None:
    solver = _Count("anything")
    out = fr.complete("gen-ask-sql", MSGS, solvers=[solver])
    assert solver.calls == 0
    assert out.stamp is not None
    assert out.stamp.ladder[0]["verdict"].startswith("skipped: no checker")
    assert script.sent == [GOOGLE]


class _Untrained:
    name = "tier-clf"
    kind = fr.RUNG_CLASSIC_ML
    heldout = None

    def solve(self, task: str, messages: list[dict[str, Any]]) -> str | None:
        raise AssertionError("an untrained classic ML rung must not run")


def test_classic_ml_rung_stays_disabled_without_heldout_numbers(script) -> None:
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, solvers=[_Untrained()])
    assert out.ok is True
    assert out.stamp is not None
    assert out.stamp.ladder[0]["verdict"] == "disabled: classic ML rung has no held-out numbers"


def test_local_rung_only_from_served_local_and_insights_sees_every_step(script) -> None:
    script.extra["google"] = {"served_provider": "google", "served_model": "gemini-3-flash-preview"}
    script.extra["nvidia"] = {
        "served_provider": "ollama",
        "served_model": "qwen3:8b",
        "served_local": True,
    }
    script.on("google", ("ok", "about forty", 10))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=1)
    assert out.ok is True
    envelope = fr.stamp_router_fingerprint({}, out.stamp)
    steps = envelope["ladder"]
    assert [(s["rung"], s["served_provider"], s["served_model"], s["served_local"], s["verdict"], s["final"]) for s in steps] == [
        (fr.RUNG_CLOUD, "google", "gemini-3-flash-preview", False, "rejected", False),
        (fr.RUNG_LOCAL, "ollama", "qwen3:8b", True, "accepted", True),
    ]
    assert envelope["served_local"] is True


def test_requested_versus_served_stays_visible_per_rung(script) -> None:
    script.on("google", ("ok", "forty", 10))

    def swap(method: str, path: str, *, body: Any = None, **kw: Any) -> tuple[int, Any]:
        status, data = script(method, path, body=body, **kw)
        if status == 200 and str(body.get("model")).startswith("nvidia"):
            data["model"] = "nvidia:meta/llama-4"
        return status, data

    with fr.use_transport(swap, direct_providers.IMPL):
        out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=1)
    assert out.ok is True
    assert out.stamp is not None
    final = out.stamp.ladder[-1]
    assert (final["requested"], final["served"], final["honored"]) == (NVIDIA, "nvidia:meta/llama-4", False)


def test_insights_stamp_reader_keeps_the_ladder(script) -> None:
    from CortexOS.crew import insights

    script.on("google", ("ok", "forty", 10))
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=1)
    assert out.stamp is not None
    envelope = {"generative": {"stamp": out.stamp.public()}}
    rebuilt = insights._route_stamp_for_fingerprint(envelope)
    assert [s["verdict"] for s in fr.router_fingerprint(rebuilt)["ladder"]] == ["rejected", "accepted"]


def test_every_ladder_rung_sends_masked_text_and_the_answer_is_restored(script, monkeypatch) -> None:
    """#268 x #270: masking runs per request, so a stepped-up rung never sends raw PII."""
    bodies: list[str] = []
    inner = script

    def recording(method: str, path: str, *, body: Any = None, **kw: Any) -> tuple[int, Any]:
        bodies.append(str((body or {}).get("messages")))
        return inner(method, path, body=body, **kw)

    email = "tan.ah.kow@example.com"
    msgs = [{"role": "user", "content": f"how many orders did {email} place"}]
    script.on("google", ("ok", "I think about 40 orders.", 10))
    with fr.use_transport(recording, direct_providers.IMPL):
        out = fr.complete("gen-ask-sql", msgs, accept=_is_select, ladder=2)
    assert out.ok is True and out.text == GOOD_SQL
    assert len(bodies) == 2, "expected one request per rung"
    for sent in bodies:
        assert email not in sent
        assert "<PII:EMAIL_1>" in sent
    assert out.stamp is not None and out.stamp.masked.get("EMAIL") == 1


def test_masker_failure_on_a_ladder_refuses_every_rung_with_nothing_sent(script, monkeypatch) -> None:
    from CortexOS.integrations import pii_mask

    def boom(_messages: Any) -> Any:
        raise pii_mask.MaskingFailed(pii_mask.REFUSED_REASON)

    monkeypatch.setattr(pii_mask, "mask_messages", boom)
    out = fr.complete("gen-ask-sql", MSGS, accept=_is_select, ladder=2)
    assert out.ok is False and out.text == ""
    assert script.sent == []
    assert pii_mask.REFUSED_REASON in out.reason
