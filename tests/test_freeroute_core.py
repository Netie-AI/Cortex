"""One FreeRoute layer (Cortex #211 follow-up): arming, credential, measured route.

Every case runs against the scripted OpenVault in tests/freeroute_fake.py. Armed
cases assert the chat call count so none can pass on a refusal branch.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path

import pytest

from CortexOS.integrations import freeroute as fr

ROOT = Path(__file__).resolve().parents[1]
HOP = None  # bound per test from the fixture (see _bind_hop)
SECRET_BODY = "Zq8vN2kR7tY4wP1mX6cB9dF3"  # no provider prefix: a substring blocklist misses it
TOKEN = "ov_" + SECRET_BODY


@pytest.fixture(autouse=True)
def _bind_hop(fake_openvault):
    global HOP
    HOP = fake_openvault.hop
    yield


def _sql_ok(text: str) -> bool:
    return "select" in text.lower() and "from" in text.lower()


# -- arming ---------------------------------------------------------------------


def test_switch_off_is_named_and_makes_no_call(fake_openvault) -> None:
    arm = fr.arming()
    assert arm.armed is False
    assert "CORTEX_FREEROUTE=0" in arm.reason
    assert fake_openvault.calls == []
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False and "FreeRoute not armed" in out.reason
    assert fake_openvault.chat_calls == []


@pytest.mark.parametrize(
    ("setup", "needle"),
    [
        (lambda f: setattr(f, "sealed", True), "vault is sealed"),
        (lambda f: setattr(f, "sealed", None), "did not report seal state"),
        (lambda f: setattr(f, "sealed", "false"), "did not report seal state"),
        (lambda f: setattr(f, "pooled", 0), "pools no keys"),
        (lambda f: setattr(f, "status_code", 500), "answered HTTP 500"),
        (
            lambda f: (setattr(f, "hops", [HOP("cortex", 10)]), f.catalogue.pop("cortex", None)),
            "no spendable FreeRoute hop",
        ),
    ],
)
def test_arming_refusals_are_named(armed_openvault, setup, needle) -> None:
    setup(armed_openvault)
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert needle in arm.reason
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False and needle in out.reason
    assert armed_openvault.chat_calls == []


def test_unreachable_names_url(armed_openvault, monkeypatch) -> None:
    from CortexOS.integrations import openvault_client

    monkeypatch.setattr(openvault_client, "request_json", lambda *a, **k: (0, None))
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "unreachable at http://127.0.0.1:5000" in arm.reason


def test_env_keys_ollama_and_vault_rows_never_arm(armed_openvault, monkeypatch) -> None:
    """G3 regression: the #214 rule armed on any of these with zero pooled keys."""
    for name in ("DEEPSEEK_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.setenv(name, "sk-" + "x" * 30)
    monkeypatch.setenv("CREW_ALLOW_OLLAMA", "1")
    armed_openvault.pooled = 0
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "pools no keys" in arm.reason
    assert armed_openvault.non_openvault_calls == []
    assert [c["path"] for c in armed_openvault.calls] == ["/api/freeroute/status"]


def test_armed_control_and_parked_hops(armed_openvault) -> None:
    arm = fr.arming(fresh=True)
    assert arm.armed is True
    assert arm.reason.startswith("armed: 3 pooled keys, 2 spendable hops")
    assert "loopback tier" in arm.reason
    armed_openvault.hops = [HOP("groq", 10, circuit="open"), HOP("cerebras", 40, parked=True)]
    arm = fr.arming(fresh=True)
    assert arm.armed is True
    assert "circuit-open or parked" in arm.reason


def test_credential_shape_rules(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", "sk-abcdefghijklmnop")
    assert "not an OpenVault ov_ key" in fr.arming(fresh=True).reason
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", TOKEN)
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://vault.example.com")
    assert "needs https" in fr.arming(fresh=True).reason
    monkeypatch.delenv("CORTEX_FREEROUTE_TOKEN")
    assert "needs an ov_ key" in fr.arming(fresh=True).reason
    monkeypatch.setenv("CREW_OPENVAULT_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("OPENVAULT_BASE_URL", "http://127.0.0.1:5001")
    reason = fr.arming(fresh=True).reason
    assert "OPENVAULT_BASE_URL=http://127.0.0.1:5001" in reason
    assert "CREW_OPENVAULT_URL=http://127.0.0.1:5000" in reason
    assert armed_openvault.calls == []


def test_ratelimit_local_identity_is_not_verification(armed_openvault, monkeypatch) -> None:
    """OpenVault answers 200 identity 'local' for an unknown bearer on loopback."""
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", TOKEN)
    arm = fr.arming(fresh=True)
    assert arm.armed is False
    assert "CORTEX_FREEROUTE_TOKEN" in arm.reason and "POST /api/apikeys" in arm.reason
    before = len(armed_openvault.status_calls)
    assert fr.arming(fresh=True).armed is False
    assert len(armed_openvault.status_calls) == before  # rejection cached, no re-probe
    assert fr.identity()["key_id"] == ""


def test_verified_key_arms_and_sends_bearer_never_identity_header(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", TOKEN)
    armed_openvault.identities[TOKEN] = "key_abc"
    arm = fr.arming(fresh=True)
    assert arm.armed is True and "as api_key" in arm.reason
    who = fr.identity()
    assert who["key_id"] == "key_abc" and who["verified_by"] == "openvault"
    out = fr.complete("t", [{"role": "user", "content": "hi"}], accept=_sql_ok)
    assert out.ok is True
    assert len(armed_openvault.chat_calls) == 1
    spent = armed_openvault.calls_to("/api/freeroute/ratelimit") + armed_openvault.chat_calls
    assert spent and all(c["headers"].get("Authorization") == f"Bearer {TOKEN}" for c in spent)
    # The seal/pool probe is unauthenticated in OpenVault; the key is not sent where it is not needed.
    assert all("Authorization" not in c["headers"] for c in armed_openvault.status_calls)
    assert all("X-Cortex-Identity" not in c["headers"] for c in armed_openvault.calls)
    dumped = json.dumps([who, arm.public(), out.stamp.public(), out.reason, fr.public_status("t")])
    assert SECRET_BODY not in dumped


def test_chat_401_disarms_that_credential_only(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", TOKEN)
    armed_openvault.identities[TOKEN] = "key_abc"
    assert fr.arming(fresh=True).armed is True
    armed_openvault.reply(status=401, error_type="auth", message="revoked")
    out = fr.complete("t", [{"role": "user", "content": "hi"}])
    assert out.ok is False and "rejected the credential (HTTP 401)" in out.reason
    assert len(armed_openvault.chat_calls) == 1  # no bearer-less retry
    assert fr.arming().armed is False

    # A relayed caller's 401 never disarms Cortex's own credential.
    fr.reset()
    assert fr.arming(fresh=True).armed is True
    relayed = fr.complete("t", [{"role": "user", "content": "hi"}], bearer="ov_callerxxxxxxxx")
    assert relayed.ok is False and "HTTP 401" in relayed.reason
    assert armed_openvault.chat_calls[-1]["headers"]["Authorization"] == "Bearer ov_callerxxxxxxxx"
    assert fr.arming().armed is True


def test_relay_without_bearer_sends_nothing(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", TOKEN)
    armed_openvault.identities[TOKEN] = "key_abc"
    out = fr.complete("t", [{"role": "user", "content": "hi"}], bearer="")
    assert out.ok is True
    assert "Authorization" not in armed_openvault.chat_calls[-1]["headers"]
    assert out.stamp.credential == "loopback tier (unattributed)"


def test_peek_makes_no_network_call(armed_openvault) -> None:
    assert fr.peek().reason == "arming not probed yet"
    assert armed_openvault.calls == []
    fr.arming(fresh=True)
    count = len(armed_openvault.calls)
    assert fr.peek().armed is True
    assert len(armed_openvault.calls) == count


def test_redaction_and_child_env(monkeypatch) -> None:
    text = "bad ov_abcdefghijkl, sk-12345678abcd, gsk_zzz and " + "A" * 40 + " end " + "x" * 400
    out = fr.redact(text)
    for leaked in ("ov_abcdefghijkl", "sk-12345678abcd", "gsk_zzz", "A" * 40):
        assert leaked not in out
    assert len(out) <= 200
    env = fr.child_env({"CORTEX_FREEROUTE_TOKEN": TOKEN, "PATH": "x"})
    assert env == {"PATH": "x"}


# -- candidates, measured route, store --------------------------------------------


def test_candidates_come_from_live_hops_times_catalogue(armed_openvault) -> None:
    armed_openvault.hops = [HOP("groq", 10), HOP("deepseek", 20, circuit="open"), HOP("cerebras", 40)]
    arm = fr.arming(fresh=True)
    models, source = fr.candidates(arm)
    assert models == ("openai/gpt-oss-120b", "qwen/qwen3.6-27b", "gpt-oss-120b", "llama-3.3-70b")
    assert source == "live hops x OpenVault catalogue"
    assert "gpt-4o-mini" not in models
    armed_openvault.hops = [HOP("cerebras", 40)]
    models, _ = fr.candidates(fr.arming(fresh=True))
    assert models == ("gpt-oss-120b", "llama-3.3-70b")


def test_bundle_pin_and_delegation(armed_openvault, monkeypatch) -> None:
    arm = fr.arming(fresh=True)
    assert fr.candidates(arm, pin="deepseek-v4-flash", pin_source="DMS_L2_MODEL") == (
        ("deepseek-v4-flash",),
        "operator pin DMS_L2_MODEL",
    )
    monkeypatch.setenv("CORTEX_FREEROUTE_MODELS", "a, b,a")
    assert fr.candidates(arm) == (("a", "b"), "operator bundle CORTEX_FREEROUTE_MODELS")
    monkeypatch.delenv("CORTEX_FREEROUTE_MODELS")
    bare = fr.Arming(armed=True, reason="armed", url=arm.url)
    assert fr.candidates(bare) == (("auto",), "delegated to OpenVault (not measured by Cortex)")


def _only_groq_models(fake, models: list[str]) -> None:
    fake.hops = [HOP("groq", 10)]
    fake.catalogue = {"groq": list(models)}


def test_exploration_then_validity_pick_through_production_calls(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["fast-bad", "slow-good"])
    msgs = [{"role": "user", "content": "q"}]
    asked: list[str] = []
    for _ in range(4):
        out = fr.complete("gen-ask-sql", msgs, accept=_sql_ok)
        asked.append(out.stamp.requested)
        verdict = "gate_pass" if out.stamp.served == "slow-good" else "gate_fail"
        fr.note_verdict(out.stamp, verdict)
    assert asked == ["fast-bad", "fast-bad", "slow-good", "slow-good"]
    picked = fr.pick("gen-ask-sql", fr.arming())
    assert picked.requested == "slow-good"
    assert "measured best validity" in picked.reason
    stamp = fr.complete("gen-ask-sql", msgs, accept=_sql_ok).stamp
    assert stamp.requested == "slow-good" and stamp.measured_n >= 2
    assert "n=" not in stamp.line() and "score" not in stamp.line()
    assert "asked slow-good, served slow-good (live hops x OpenVault catalogue)" in stamp.line()


def test_validity_beats_latency(armed_openvault, monkeypatch) -> None:
    _only_groq_models(armed_openvault, ["quick", "careful"])
    msgs = [{"role": "user", "content": "q"}]
    clock = iter([0.0, 0.01, 10.0, 10.02, 20.0, 29.0, 40.0, 49.0, 60.0, 60.1])
    monkeypatch.setattr(fr.time, "monotonic", lambda: next(clock, 99.0))
    for _ in range(4):
        out = fr.complete("t", msgs, accept=_sql_ok)
        fr.note_verdict(out.stamp, "gate_fail" if out.stamp.served == "quick" else "gate_pass")
    assert fr.pick("t", fr.arming(fresh=True)).requested == "careful"


def test_validity_lands_on_served_model_when_not_honoured(armed_openvault) -> None:
    """OpenVault serves groq's pool[0] for an id groq does not carry."""
    armed_openvault.hops = [HOP("groq", 10)]
    msgs = [{"role": "user", "content": "q"}]
    arm = fr.arming(fresh=True)
    for _ in range(2):
        out = fr.complete("t", msgs, accept=_sql_ok, pin="deepseek-v4-pro", pin_source="DMS_L2_MODEL")
        assert out.stamp.requested == "deepseek-v4-pro"
        assert out.stamp.served == "openai/gpt-oss-120b" and out.stamp.honored is False
        fr.note_verdict(out.stamp, "gate_pass")
    rows = fr.scoreboard("t")
    assert {(r["requested"], r["served"]) for r in rows} == {("deepseek-v4-pro", "openai/gpt-oss-120b")}
    assert all(r["requested"] != "openvault" for r in rows)
    monkeypatch_bundle = fr._stats("t", ["deepseek-v4-pro"])["deepseek-v4-pro"]
    assert monkeypatch_bundle.honored_rate == 0.0
    assert monkeypatch_bundle.served_most == "openai/gpt-oss-120b"
    assert monkeypatch_bundle.score is not None
    assert arm.armed


def test_not_honoured_is_named_in_pick_reason(armed_openvault, monkeypatch) -> None:
    armed_openvault.hops = [HOP("groq", 10)]
    monkeypatch.setenv("CORTEX_FREEROUTE_MODELS", "deepseek-v4-pro,qwen/qwen3.6-27b")
    msgs = [{"role": "user", "content": "q"}]
    for _ in range(4):
        out = fr.complete("t", msgs, accept=_sql_ok)
        fr.note_verdict(out.stamp, "gate_pass" if out.stamp.requested == "deepseek-v4-pro" else "gate_fail")
    picked = fr.pick("t", fr.arming(fresh=True))
    assert picked.requested == "deepseek-v4-pro"
    assert "not honored (served openai/gpt-oss-120b)" in picked.reason


@pytest.mark.parametrize("status", [400, 402, 429, 502, 503])
def test_refusal_statuses_are_recorded_unscored(armed_openvault, status) -> None:
    _only_groq_models(armed_openvault, ["m1"])
    armed_openvault.reply(status=status, error_type="x", message="upstream said sk-abcdefgh1234 no")
    out = fr.complete("t", [{"role": "user", "content": "q"}])
    assert out.ok is False and f"HTTP {status}" in out.reason
    assert "sk-abcdefgh1234" not in out.reason
    rows = fr.scoreboard("t")
    assert rows and rows[0]["scored"] == 0
    assert fr._stats("t", ["m1"])["m1"].score is None


def test_repeated_refusals_make_candidate_ineligible(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["dead", "alive"])
    msgs = [{"role": "user", "content": "q"}]
    for _ in range(3):
        armed_openvault.reply(status=503, error_type="openvault_no_keys")
    asked = [fr.complete("t", msgs, pin="dead").stamp.requested for _ in range(3)]
    assert asked == ["dead", "dead", "dead"]
    picked = fr.pick("t", fr.arming())
    assert picked.requested == "alive"
    assert "ineligible: dead" in picked.reason


def test_unscored_only_candidate_is_never_exploited(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["never-answers", "answers"])
    msgs = [{"role": "user", "content": "q"}]
    for _ in range(2):
        armed_openvault.reply(status=429, error_type="rate_limited")
        fr.complete("t", msgs, pin="never-answers")
    for _ in range(2):
        out = fr.complete("t", msgs, pin="answers", accept=_sql_ok)
        fr.note_verdict(out.stamp, "gate_fail")
    picked = fr.pick("t", fr.arming())
    assert picked.requested == "answers"


def test_store_persists_across_reset_and_processes(armed_openvault, tmp_path) -> None:
    _only_groq_models(armed_openvault, ["m1"])
    out = fr.complete("t", [{"role": "user", "content": "q"}], accept=_sql_ok)
    fr.note_verdict(out.stamp, "plausible")
    fr.reset()
    path = fr.store_path()
    code = (
        "import sqlite3,sys; con=sqlite3.connect(sys.argv[1]);"
        "print(con.execute('select requested, served, verdict from routes').fetchall())"
    )
    got = subprocess.run([sys.executable, "-c", code, str(path)], capture_output=True, text=True, check=True)
    assert "('m1', 'm1', 'plausible')" in got.stdout


def test_plausibility_overrides_gate_but_gate_never_overrides_plausibility(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["m1"])
    stamp = fr.complete("t", [{"role": "user", "content": "q"}], accept=_sql_ok).stamp
    fr.note_verdict(stamp, "gate_pass")
    fr.note_verdict(stamp.call_id, "implausible")
    fr.note_verdict(stamp, "gate_pass")
    con = sqlite3.connect(str(fr.store_path()))
    assert con.execute("select verdict from routes").fetchone()[0] == "implausible"


def test_concurrent_writers_lose_no_rows(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["m1"])
    msgs = [{"role": "user", "content": "q"}]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: fr.complete("t", msgs, pin="m1"), range(20)))
    con = sqlite3.connect(str(fr.store_path()))
    assert con.execute("select count(*) from routes").fetchone()[0] == 20


def test_learn_off_writes_nothing_and_store_error_is_visible(armed_openvault, monkeypatch, tmp_path) -> None:
    _only_groq_models(armed_openvault, ["m1"])
    monkeypatch.setenv("CORTEX_FREEROUTE_LEARN", "0")
    fr.complete("t", [{"role": "user", "content": "q"}])
    assert not fr.store_path().exists()
    monkeypatch.delenv("CORTEX_FREEROUTE_LEARN")
    blocker = tmp_path / "a_file"
    blocker.write_text("x")
    monkeypatch.setenv("CORTEX_FREEROUTE_SCOREBOARD", str(blocker / "routes.db"))
    stamp = fr.complete("t", [{"role": "user", "content": "q"}]).stamp
    assert "route store unavailable" in fr.public_status("t")["store_error"]
    assert "route store unavailable" in stamp.line()


def test_complete_body_accept_and_leave_gate(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["m1"])
    msgs = [{"role": "user", "content": "q"}]

    def boom(_text: str) -> bool:
        raise ValueError("validator crashed")

    out = fr.complete("t", msgs, accept=boom, temperature=0.0)
    assert out.ok is False and out.stamp.usable is False
    body = armed_openvault.chat_calls[-1]["body"]
    assert "metadata" not in body and body["temperature"] == 0.0 and body["model"] == "m1"

    armed_openvault.gate_allowed = False
    before = len(armed_openvault.chat_calls)
    with fr.journal() as stamps:
        denied = fr.complete("t", msgs, egress="leave")
    assert denied.ok is False
    assert denied.reason == "OpenVault leave-machine gate denied: vault is sealed"
    assert len(armed_openvault.chat_calls) == before
    assert armed_openvault.gate_calls[-1]["action"] == "leave"
    assert "not sent (OpenVault leave-machine gate denied" in fr.last_line(stamps)


def test_replay_transport_is_labelled_not_openvault_across_threads(armed_openvault) -> None:
    _only_groq_models(armed_openvault, ["m1"])

    def replay(method, path, **kwargs):
        return 200, {"model": "m1", "choices": [{"message": {"content": "SELECT 1 FROM t"}}]}

    msgs = [{"role": "user", "content": "q"}]
    with fr.journal(shadow=True) as stamps, fr.use_transport(replay, "replay:abc"):
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=1) as pool:
            out = pool.submit(ctx.run, fr.complete, "t", msgs).result()
    assert out.stamp.line().startswith("NOT OpenVault FreeRoute (replay:abc): ")
    assert stamps and stamps[0].call_id == out.stamp.call_id
    assert armed_openvault.chat_calls == []
    con = sqlite3.connect(str(fr.store_path()))
    assert con.execute("select impl, shadow from routes").fetchone() == ("replay:abc", 1)


def test_public_status_never_reserves_hop_rows(armed_openvault) -> None:
    status = fr.public_status("t")
    dumped = json.dumps(status)
    assert "kid-groq" not in dumped
    assert "GROQ_API_KEY" not in dumped
    assert "gsk_leakedkeyfragment123" not in dumped
    assert status["arming"]["hops"][0] == {"provider": "groq", "priority": 10, "circuit": "closed", "parked": False}


# -- import pin (the core must stay engine-safe) ------------------------------------

_ALLOWED_PREFIXES = ("CortexOS.integrations", "CortexOS.paths")


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    return mods


def _bad_imports(path: Path) -> list[str]:
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    bad = []
    for mod in _imports(path):
        top = mod.split(".", 1)[0]
        if top in stdlib or mod.startswith(_ALLOWED_PREFIXES):
            continue
        bad.append(mod)
    return bad


def _crew_imports(paths: list[Path]) -> list[str]:
    return [
        f"{p.relative_to(ROOT)}:{m}"
        for p in paths
        for m in _imports(p)
        if m == "CortexOS.crew" or m.startswith("CortexOS.crew.")
    ]


def test_core_imports_only_stdlib_integrations_and_paths(tmp_path) -> None:
    for rel in ("CortexOS/integrations/freeroute.py", "CortexOS/dms/sql_extract.py"):
        assert _bad_imports(ROOT / rel) == [], rel
    engine = [
        p
        for folder in ("CortexOS/integrations", "CortexOS/dms", "CortexOS/api")
        for p in (ROOT / folder).rglob("*.py")
    ]
    assert _crew_imports(engine) == []
    # The pin can fail.
    poison = tmp_path / "poison.py"
    poison.write_text("import httpx\nfrom CortexOS.crew import freeroute\n", encoding="utf-8")
    assert _bad_imports(poison) == ["httpx", "CortexOS.crew"]
    assert _crew_imports([ROOT / "CortexOS/integrations/freeroute.py"]) == []
