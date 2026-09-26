"""Cortex #271 ROUTER-3: predict-before-spend gate (KEV-DECIDE) in front of complete().

No live kev and no provider key: a fake decision backend is installed with
``freeroute_prespend.use_backend`` and a scripted env-direct transport counts
what would have been sent. Each case asserts what the caller receives (ok,
text, the named reason) and how many paid calls went out.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from CortexOS.decision.models import RawDecision
from CortexOS.integrations import direct_providers
from CortexOS.integrations import freeroute as fr
from CortexOS.integrations import freeroute_prespend as ps

MSGS = [{"role": "user", "content": "how many skus are in stock"}]
GOOD_SQL = "SELECT COUNT(*) FROM inventory"
PLAN = {"task": "gen-ask-sql", "tables": ["inventory"], "joins": [], "retry": False}
_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY")


def _is_select(text: str) -> bool:
    return text.strip().upper().startswith("SELECT")


class Wire:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, method: str, path: str, *, body: Any = None, **_kw: Any) -> tuple[int, Any]:
        self.sent.append(str((body or {}).get("model") or ""))
        return 200, {
            "model": body["model"],
            "choices": [{"message": {"role": "assistant", "content": GOOD_SQL}}],
            "usage": {"total_tokens": 12},
        }


class Kev:
    """Fake calibrated backend: P(plan validates) = p, or a scripted failure."""

    name = "fake-kev"

    def __init__(self, p: float = 0.9, *, fail: str = "", boom: bool = False) -> None:
        self.p, self.fail, self.boom = p, fail, boom
        self.states: list[Any] = []

    def evaluate(self, question: Any, state: Any) -> RawDecision:
        self.states.append(state)
        if self.boom:
            raise RuntimeError("kev exploded")
        if self.fail:
            return RawDecision.failure(self.name, self.fail)
        return RawDecision(scores=(1.0 - self.p, self.p), is_logits=False, calibrated=True, backend=self.name)


@pytest.fixture()
def wire(freeroute_hermetic, monkeypatch) -> Iterator[Wire]:
    for name in _KEY_ENVS + (
        fr.SWITCH_ENV,
        fr.MODELS_ENV,
        fr.LOCAL_ONLY_ENV,
        fr.LADDER_ENV,
        ps.MODE_ENV,
        ps.THRESHOLD_ENV,
        "CORTEX_KEV_URL",
        "CORTEX_DECISION_ABSTAIN_THRESHOLD",
        "CORTEX_FREEROUTE_COOLDOWN_S",
    ):
        monkeypatch.delenv(name, raising=False)
    for p in direct_providers.PROVIDERS:
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-fake")
    fake = Wire()
    fr.reset()
    with fr.use_transport(fake, direct_providers.IMPL):
        yield fake
    fr.reset()


def _ask(**kw: Any) -> fr.Completion:
    kw.setdefault("predict_state", PLAN)
    return fr.complete("gen-ask-sql", MSGS, accept=_is_select, **kw)


def _enforce(monkeypatch, threshold: str = "0.5") -> None:
    monkeypatch.setenv(ps.MODE_ENV, "enforce")
    monkeypatch.setenv(ps.THRESHOLD_ENV, threshold)


def test_off_by_default_asks_nothing_and_stamps_nothing(wire) -> None:
    kev = Kev(0.01)
    with ps.use_backend(kev):
        out = _ask()
    assert out.ok and out.text == GOOD_SQL
    assert kev.states == [] and len(wire.sent) == 1
    assert out.stamp is not None and out.stamp.prespend == {}


def test_enforce_skips_the_paid_call_when_predicted_invalid(wire, monkeypatch) -> None:
    _enforce(monkeypatch)
    kev = Kev(0.1)
    with ps.use_backend(kev), fr.journal() as stamps:
        out = _ask()
    assert wire.sent == []
    assert out.ok is False and out.text == ""
    assert "pre-spend gate skipped the paid call: " in out.reason
    assert kev.states == [PLAN]
    assert out.stamp is not None and out.stamp.status is None
    assert out.stamp.prespend["decision"] == "skip"
    assert stamps == [out.stamp]
    assert "not sent (pre-spend gate skipped" in out.stamp.line()


def test_enforce_skips_on_abstain(wire, monkeypatch) -> None:
    _enforce(monkeypatch, "0.5")
    with ps.use_backend(Kev(0.6)):  # confidence 0.2 < 0.5 -> abstain
        out = _ask()
    assert wire.sent == []
    assert out.ok is False and "abstained" in out.reason


def test_enforce_spends_when_predicted_valid(wire, monkeypatch) -> None:
    _enforce(monkeypatch)
    with ps.use_backend(Kev(0.95)):
        out = _ask()
    assert out.ok and out.text == GOOD_SQL and len(wire.sent) == 1
    assert out.stamp is not None
    assert out.stamp.prespend["decision"] == "spend"
    assert out.stamp.prespend["p_valid"] == pytest.approx(0.95)


def test_shadow_records_but_always_spends(wire, monkeypatch) -> None:
    monkeypatch.setenv(ps.MODE_ENV, "shadow")
    with ps.use_backend(Kev(0.1)):
        out = _ask()
    assert out.ok and out.text == GOOD_SQL and len(wire.sent) == 1
    assert out.stamp is not None
    rec = out.stamp.prespend
    assert rec["decision"] == "spend (shadow)" and rec["would_skip"] is True
    import sqlite3

    con = sqlite3.connect(str(fr.store_path()))
    row = con.execute("SELECT prespend_p, prespend_mode FROM routes WHERE call_id = ?", (out.stamp.call_id,)).fetchone()
    con.close()
    assert row[0] == pytest.approx(0.1) and row[1] == "shadow"


@pytest.mark.parametrize(
    "kev, cause",
    [(Kev(fail="http 503"), "http 503"), (Kev(boom=True), "backend error: RuntimeError")],
)
def test_degraded_backend_falls_back_to_calling_visibly(wire, monkeypatch, kev, cause) -> None:
    _enforce(monkeypatch)
    with ps.use_backend(kev):
        out = _ask()
    assert out.ok and out.text == GOOD_SQL and len(wire.sent) == 1
    assert out.stamp is not None
    assert out.stamp.prespend["decision"] == "spend"
    assert out.stamp.prespend["degraded"] == cause


def test_no_kev_configured_is_degraded_not_silent_and_opens_no_socket(wire, monkeypatch) -> None:
    _enforce(monkeypatch)
    out = _ask()
    assert out.ok and len(wire.sent) == 1
    assert out.stamp is not None
    assert out.stamp.prespend["degraded"] == "no decision backend (CORTEX_KEV_URL unset)"


def test_non_loopback_kev_is_refused_before_any_request(wire, monkeypatch) -> None:
    _enforce(monkeypatch)
    monkeypatch.setenv("CORTEX_KEV_URL", "https://kev.example.com")
    out = _ask()
    assert out.ok and len(wire.sent) == 1
    assert out.stamp is not None
    assert "refuses non-loopback URL" in out.stamp.prespend["degraded"]


def test_caller_without_plan_state_is_spent_and_says_so(wire, monkeypatch) -> None:
    _enforce(monkeypatch)
    kev = Kev(0.01)
    with ps.use_backend(kev):
        out = _ask(predict_state=None)
    assert out.ok and len(wire.sent) == 1 and kev.states == []
    assert out.stamp is not None
    assert out.stamp.prespend["degraded"] == "caller passed no plan state"


@pytest.mark.parametrize(
    "mode, threshold, why",
    [
        ("enforce", "", "needs CORTEX_FREEROUTE_PRESPEND_THRESHOLD"),
        ("enforce", "high", "needs CORTEX_FREEROUTE_PRESPEND_THRESHOLD"),
        ("enforse", "0.5", "'enforse' is not shadow|enforce"),
    ],
)
def test_misconfiguration_fails_closed(wire, monkeypatch, mode, threshold, why) -> None:
    monkeypatch.setenv(ps.MODE_ENV, mode)
    monkeypatch.setenv(ps.THRESHOLD_ENV, threshold)
    with ps.use_backend(Kev(0.99)):
        out = _ask()
    assert wire.sent == []
    assert out.ok is False and out.text == ""
    assert why in out.reason and "nothing spent (fail-closed)" in out.reason


class _Rule:
    name = "rule:none"
    kind = fr.RUNG_DETERMINISTIC

    def __init__(self, answer: str | None) -> None:
        self.answer = answer

    def solve(self, task: str, messages: list[dict[str, Any]]) -> str | None:
        return self.answer


def test_free_rungs_still_run_before_a_skipped_paid_rung(wire, monkeypatch) -> None:
    _enforce(monkeypatch)
    with ps.use_backend(Kev(0.1)):
        solved = _ask(solvers=[_Rule(GOOD_SQL)])
        skipped = _ask(solvers=[_Rule(None)], ladder=2)
    assert solved.ok and solved.text == GOOD_SQL
    assert wire.sent == []
    assert skipped.ok is False and "pre-spend gate skipped" in skipped.reason
    assert skipped.stamp is not None
    assert [s["verdict"] for s in skipped.stamp.ladder] == ["abstained"]


def test_tune_on_train_only_and_report_on_heldout(wire, monkeypatch) -> None:
    monkeypatch.setenv(ps.MODE_ENV, "shadow")

    def record(p: float, split: str, verdict: str) -> None:
        with ps.use_backend(Kev(p)), fr.journal(split=split):
            out = _ask()
        fr.note_verdict(out.stamp, verdict)

    record(0.9, "train", "gate_pass")
    record(0.8, "train", "gate_pass")
    record(0.2, "train", "gate_fail")
    record(0.55, "heldout", "gate_pass")  # must not move the train threshold
    record(0.95, "heldout", "gate_pass")
    record(0.3, "heldout", "gate_fail")
    record(0.05, "product", "gate_pass")  # neither split

    t = ps.tune()
    assert t == pytest.approx(0.6)  # 2 * 0.8 - 1: keeps every good train row
    got = ps.report(t)
    assert got["n"] == 3 and got["measured"] is True
    assert (got["calls_skipped"], got["good_skipped"], got["bad_skipped"]) == (2, 1, 1)
    for key in ("ece", "brier", "automatable_share"):
        assert 0.0 <= got[key] <= 1.0


def test_tune_refuses_when_no_threshold_keeps_train_coverage() -> None:
    assert ps.tune([(0.4, 1), (0.9, 1), (0.1, 0)]) is None
    assert ps.tune([(0.1, 0)]) is None
    assert ps.report(0.5, []) == {"split": "heldout", "n": 0, "measured": False}


def test_sql_lane_passes_its_plan_to_the_gate(wire, monkeypatch) -> None:
    from packs.dms.generative import sql_generator

    monkeypatch.setenv(ps.MODE_ENV, "shadow")
    monkeypatch.setenv("DMS_L2_FEW_SHOT", "0")
    monkeypatch.setattr(sql_generator, "is_configured", lambda: True)
    schema = {
        "tables": {"inventory": {"columns": ["sku", "qty"]}, "skus": {"columns": ["sku"]}},
        "joins": [{"from_table": "inventory", "from_column": "sku", "to_table": "skus", "to_column": "sku"}],
    }
    kev = Kev(0.9)
    with ps.use_backend(kev):
        got = sql_generator.generate_candidates("how many skus are in stock", schema)
    assert got == [GOOD_SQL]
    assert kev.states == [
        {
            "task": "gen-ask-sql",
            "question": "how many skus are in stock",
            "tables": ["inventory", "skus"],
            "joins": ["inventory.sku=skus.sku"],
            "retry": False,
        }
    ]


def test_gate_survives_the_netie_alias_rebinding_decide(wire, monkeypatch) -> None:
    """Importing ``netie.decision.decide`` rebinds ``CortexOS.decision.decide`` to the
    submodule. Seen in the full suite: the gate degraded with a TypeError and spent."""
    import importlib

    import CortexOS.decision as pkg

    importlib.import_module("netie.decision.decide")
    monkeypatch.setattr(pkg, "decide", importlib.import_module("CortexOS.decision.decide"))
    _enforce(monkeypatch)
    with ps.use_backend(Kev(0.1)):
        out = _ask()
    assert wire.sent == []
    assert out.ok is False and "predicted invalid" in out.reason
