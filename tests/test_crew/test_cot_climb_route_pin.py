"""cot_climb: one provider per question, and certified formulas in the prompt.

Live run 2026-09-25 (52 questions, env-direct gemini + kimi): climb re-picked a
model for each of its 2-6 steps, so one question bounced between providers,
often straight back to one that had just answered 429; and the SQL step
invented a min-max supplier score instead of the certified
``0.65 x risk + 0.35 x lead time`` formula.

These go through the real stack: ``crew.freeroute.complete`` ->
``integrations.freeroute.complete`` (env-direct, fake keys) -> a fake transport
installed with ``use_transport``. No network, no real keys.
"""

from __future__ import annotations

import sqlite3
from collections import deque
from collections.abc import Iterator
from typing import Any

import pytest

from CortexOS.crew import cot_climb, insights
from CortexOS.crew import freeroute as crew_fr
from CortexOS.execution.gen_cfsm import DECISION_TERMINATE
from CortexOS.integrations import direct_providers
from CortexOS.integrations import freeroute as core

GOOGLE = "google:gemini-3-flash-preview"
NVIDIA = "nvidia:moonshotai/kimi-k3"
SUPPLIER_Q = "Rank suppliers by combined risk and lead time score"
_REAL_KEY_ENVS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY", "MISTRAL_API_KEY", "CEREBRAS_API_KEY")

# What the model wrote in the live run when it had no certified definition.
INVENTED_MINMAX = (
    "SELECT supplier_id, "
    "(risk_score - (SELECT MIN(risk_score) FROM suppliers)) / "
    "((SELECT MAX(risk_score) FROM suppliers) - (SELECT MIN(risk_score) FROM suppliers)) "
    "+ (lead_time_days - (SELECT MIN(lead_time_days) FROM suppliers)) * 1.0 / "
    "((SELECT MAX(lead_time_days) FROM suppliers) - (SELECT MIN(lead_time_days) FROM suppliers)) "
    "AS ranking_score FROM suppliers ORDER BY ranking_score DESC LIMIT 10"
)
# Rows where min-max (equal weights) and the certified 0.65/0.35 formula disagree.
SUPPLIERS = [
    ("S1", 0.90, 5),
    ("S2", 0.30, 60),
    ("S3", 0.60, 20),
    ("S4", 0.10, 55),
]


class FakeProviders:
    """Scripted transport. Answers think with prose and SQL steps via ``sql_for``."""

    def __init__(self) -> None:
        self.script: dict[str, deque[int]] = {}
        self.sent: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self.sql_for = lambda prompt: "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"

    def refuse(self, label: str, *statuses: int) -> FakeProviders:
        self.script.setdefault(label, deque()).extend(statuses)
        return self

    def __call__(self, method: str, path: str, *, body: Any = None, **_kw: Any) -> tuple[int, Any]:
        body = body or {}
        model = str(body.get("model") or "")
        messages = body.get("messages") or []
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        user = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        kind = "sql" if "DuckDB SELECT" in system else "think"
        self.sent.append((kind, model))
        self.prompts.append(user)
        queue = self.script.get(model.partition(":")[0])
        if queue:
            status = queue.popleft()
            return status, {"error": {"message": f"{model} said {status}"}}
        text = self.sql_for(user) if kind == "sql" else "Use the ranked tables. No numbers."
        return 200, {
            "model": model,
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


@pytest.fixture()
def providers(crew_env, freeroute_hermetic, monkeypatch) -> Iterator[FakeProviders]:
    for name in _REAL_KEY_ENVS + (core.SWITCH_ENV, core.MODELS_ENV, core.LOCAL_ONLY_ENV, "CORTEX_FREEROUTE_COOLDOWN_S"):
        monkeypatch.delenv(name, raising=False)
    for p in direct_providers.PROVIDERS:
        monkeypatch.delenv(p.models_env, raising=False)
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setenv(direct_providers.TRANSPORT_ENV, "env-direct")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-fake")
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-fake")
    fake = FakeProviders()
    core.reset()
    with core.use_transport(fake, direct_providers.IMPL):
        yield fake
    core.reset()


def _seed_sql_task_prefers_nvidia(providers: FakeProviders) -> None:
    """Measured history where the SQL task's best route is nvidia, think's is google."""
    msgs = [{"role": "user", "content": "seed"}]
    for model, verdict in ((GOOGLE, "gate_fail"), (NVIDIA, "gate_pass")):
        for _ in range(2):
            out = core.complete("crew-insights-sql", msgs, pin=model)
            core.note_verdict(out.stamp, verdict)
    providers.sent.clear()
    providers.prompts.clear()


@pytest.mark.asyncio
async def test_one_question_stays_on_the_provider_that_answered_step_zero(providers) -> None:
    _seed_sql_task_prefers_nvidia(providers)
    assert core.pick("crew-insights-sql", core.arming()).requested == NVIDIA  # would bounce
    answers = deque(["SELECT secret FROM payroll", "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"])
    providers.sql_for = lambda prompt: answers.popleft()

    env = await cot_climb.climb("how many skus", insights.retrieve_ontology("how many skus"))

    assert env["status"] == "ABSTAIN" and env["valid"] is True
    assert env["values"] == []
    assert "inventory" in (env["sql"] or "").lower()
    kinds = [k for k, _ in providers.sent]
    assert kinds == ["think", "sql", "think", "sql"]  # improve loop ran: 4 steps
    assert {m for _, m in providers.sent} == {GOOGLE}  # never bounced to nvidia
    pin = env["climb"]["pin"]
    assert pin["model"] == GOOGLE and pin["from_step"] == 0
    assert pin["events"] == [{"step": 0, "event": "pinned", "model": GOOGLE}]
    assert [s["model"] for s in env["climb"]["steps"]] == [GOOGLE] * 4
    assert env["route"]["model"] == GOOGLE
    assert "operator pin caller pick" in env["stamp"]["source"]


@pytest.mark.asyncio
async def test_a_refused_pinned_step_releases_the_pin_and_the_next_call_moves(providers) -> None:
    """The live failure: kimi/gemini 429 mid-question. The step is re-sent once
    unpinned and the picker's cooldown sends it to the other provider."""
    calls = {"n": 0}
    real = providers.__call__

    def scripted(method: str, path: str, **kw: Any) -> tuple[int, Any]:
        calls["n"] += 1
        if calls["n"] == 2:  # think answered on google; the pinned SQL step is rate limited
            providers.sent.append(("sql", str((kw.get("body") or {}).get("model"))))
            return 429, {"error": {"message": "RESOURCE_EXHAUSTED"}}
        return real(method, path, **kw)

    with core.use_transport(scripted, direct_providers.IMPL):
        env = await cot_climb.climb("how many skus", insights.retrieve_ontology("how many skus"))

    assert providers.sent == [("think", GOOGLE), ("sql", GOOGLE), ("sql", NVIDIA)]
    assert env["status"] == "ABSTAIN" and env["valid"] is True and env["values"] == []
    assert "inventory" in (env["sql"] or "").lower()
    events = env["climb"]["pin"]["events"]
    assert [(e["event"], e["model"]) for e in events] == [
        ("pinned", GOOGLE),
        ("released", GOOGLE),
        ("pinned", NVIDIA),
    ]
    assert "HTTP 429" in events[1]["reason"]
    assert env["climb"]["pin"]["model"] == NVIDIA
    assert "cooling (skipped): " + GOOGLE in env["stamp"]["pick_reason"]


def test_certified_definition_reaches_the_sql_prompt() -> None:
    ranking = insights.retrieve_ontology(SUPPLIER_Q)
    lines = cot_climb.certified_definitions(SUPPLIER_Q, ranking)
    prompt = cot_climb._sql_prompt(SUPPLIER_Q, ranking, definitions=lines)
    assert "CERTIFIED DEFINITIONS" in prompt
    assert "cq_supplier_ranking" in prompt
    assert "ROUND((risk_score * 0.65) + ((lead_time_days / 60.0) * 0.35), 3)" in prompt
    # A question with no certified metric gets no definition block.
    other = "Show the CCTV camera for warehouse A"
    assert "risk_score * 0.65" not in "\n".join(
        cot_climb.certified_definitions(other, insights.retrieve_ontology(other))
    )


def _echo_certified_or_invent(prompt: str) -> str:
    """A model that uses a certified definition when handed one, else invents."""
    for line in prompt.splitlines():
        if line.startswith("- certified query cq_supplier_ranking: "):
            return line.split(": ", 1)[1]
    return INVENTED_MINMAX


def _run(sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE TABLE suppliers (supplier_id TEXT, risk_score REAL, lead_time_days INTEGER)")
        con.executemany("INSERT INTO suppliers VALUES (?,?,?)", SUPPLIERS)
        return list(con.execute(sql))
    finally:
        con.close()


@pytest.mark.asyncio
async def test_supplier_ranking_answer_uses_the_certified_formula(providers) -> None:
    providers.sql_for = _echo_certified_or_invent

    class _Bridge:
        async def ask(self, question: str) -> dict[str, Any]:
            raise AssertionError(f"ask must not run: {question}")

    env = await insights.run_insights(
        SUPPLIER_Q,
        bridge=_Bridge(),  # type: ignore[arg-type]
        ask=False,
        generate=True,
        complete=crew_fr.complete,
    )
    sql_prompts = [p for (k, _), p in zip(providers.sent, providers.prompts, strict=True) if k == "sql"]
    assert sql_prompts and "risk_score * 0.65" in sql_prompts[0]

    gen = env["generative"]
    assert env["status"] == "ABSTAIN" and env["values"] == []
    assert gen["valid"] is True
    assert gen["climb"]["final"] == DECISION_TERMINATE
    assert gen["climb"]["definitions_consumed"] is True
    text = insights.render_tool_text(env)
    assert "sql_valid: True" in text
    assert "risk_score * 0.65" in text and "lead_time_days / 60.0" in text
    assert "MIN(risk_score)" not in text

    rows = _run(gen["sql"])
    expected = sorted(
        ((sid, round(r * 0.65 + (lt / 60.0) * 0.35, 3)) for sid, r, lt in SUPPLIERS),
        key=lambda row: -row[1],
    )
    assert rows == expected
    assert [r[0] for r in rows] == ["S1", "S2", "S3", "S4"]
    # The invented score orders these suppliers differently; that is the bug.
    assert [r[0] for r in _run(INVENTED_MINMAX)] != [r[0] for r in rows]
