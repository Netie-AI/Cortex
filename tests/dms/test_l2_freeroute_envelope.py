"""Cortex #211 follow-up G1: engine generative-ask on the one FreeRoute core.

Every case drives the real DMS port (DmsL2Generation + sql_generator) through
attempt_l2 and answer() against the scripted OpenVault, and asserts what the
customer receives: rendered answer text, rows, badge (R-0001). Refusal cases
must name the OpenVault cause instead of "L2 not wired" or a bare NO_CANDIDATE
(R-0011). Armed cases assert chat calls happened, so none passes on a refusal.
"""

from __future__ import annotations

import socket
import sqlite3
from typing import Any

import pytest

from CortexOS.dms import l2_generation
from CortexOS.integrations import freeroute
from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401

QUESTION = "list some skus from inventory stock"
TOKEN = "ov_" + "Hq3Lm8Rt5Wy2Kd7Np4Vx"


@pytest.fixture(autouse=True)
def _l2_real_port(monkeypatch):
    from bench.accuracy import _ensure_db_loaded
    from packs.dms.generative.l2_adapter import DmsL2Generation

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.delenv("DMS_L2_SHADOW", raising=False)
    port = DmsL2Generation()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)
    monkeypatch.setattr("CortexOS.dms.answer_engine.match_certified", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.route_to_metric", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine.undefined_subject", lambda q: None)
    monkeypatch.setattr("CortexOS.dms.answer_engine._shape_refusal", lambda q: None)
    monkeypatch.setattr("packs.dms.semantic.query_skills.find", lambda *a, **k: None)
    monkeypatch.setattr("packs.dms.semantic.catalog_answer.is_catalog_intent", lambda q: False)
    yield


def _ask(**kwargs: Any) -> dict[str, Any]:
    from CortexOS.dms.answer_engine import answer

    return answer(QUESTION, **kwargs)


def _assert_abstain_names(env: dict[str, Any], needle: str) -> None:
    assert env["badge"] == "abstain", env
    assert env["rows"] == []
    assert env.get("sql_used") is None
    assert needle in (env.get("answer") or ""), env.get("answer")
    assert needle in (env.get("assumptions") or "")


def _store_rows() -> list[tuple[Any, ...]]:
    path = freeroute.store_path()
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    try:
        return con.execute("select requested, served, status, verdict, shadow from routes").fetchall()
    finally:
        con.close()


# -- (a)-(f) refusals are named in the customer answer ----------------------------


def test_a_sealed_vault_is_named_and_nothing_is_sent(armed_openvault) -> None:
    armed_openvault.sealed = True
    env = _ask()
    _assert_abstain_names(env, "vault is sealed")
    assert "FreeRoute not armed" in env["answer"]
    assert armed_openvault.chat_calls == []


def test_a_sealed_vault_on_the_contract_route(armed_openvault, dms_http) -> None:  # noqa: F811
    armed_openvault.sealed = True
    dms_http.bind_session("fr-sealed", {"inventory": "TRUE"})
    resp = dms_http.post("/v1/contract/ask", json={"question": QUESTION, "session_id": "fr-sealed"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _badge(body).lower() == "abstain"
    assert body.get("rows") in ([], None)
    assert "vault is sealed" in (body.get("answer") or "")
    assert armed_openvault.chat_calls == []


def test_b_no_pooled_keys_is_named(armed_openvault) -> None:
    armed_openvault.pooled = 0
    _assert_abstain_names(_ask(), "pools no keys")


def test_c_cortex_only_pooled_row_does_not_arm(armed_openvault) -> None:
    armed_openvault.hops = [armed_openvault.hop("cortex", 10)]
    _assert_abstain_names(_ask(), "no spendable FreeRoute hop")
    assert armed_openvault.chat_calls == []


def test_d_leave_gate_denial_is_named_not_bare_no_candidate(armed_openvault) -> None:
    armed_openvault.gate_allowed = False
    env = _ask()
    _assert_abstain_names(env, "leave-machine gate denied")
    assert "vault is sealed" in env["answer"]
    assert armed_openvault.chat_calls == []
    assert armed_openvault.gate_calls and armed_openvault.gate_calls[0]["action"] == "leave"


@pytest.mark.parametrize(
    ("status", "error_type", "needle"),
    [
        (503, "openvault_no_keys", "no candidate hop (HTTP 503)"),
        (502, "openvault_fallback_exhausted", "fallback exhausted (HTTP 502)"),
    ],
)
def test_e_openvault_refusal_status_is_named(armed_openvault, status, error_type, needle) -> None:
    for _ in range(3):
        armed_openvault.reply(status=status, error_type=error_type, message="all hops failed")
    env = _ask()
    _assert_abstain_names(env, needle)
    assert len(armed_openvault.chat_calls) >= 1


def test_f_rejected_credential_is_named_and_not_retried(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("CORTEX_FREEROUTE_TOKEN", TOKEN)
    armed_openvault.identities[TOKEN] = "key_cortex"
    armed_openvault.reply(status=401, error_type="auth", message="key revoked")
    env = _ask()
    _assert_abstain_names(env, "rejected the credential (HTTP 401)")
    assert len(armed_openvault.chat_calls) == 1
    status_before = len(armed_openvault.status_calls)
    again = _ask()
    _assert_abstain_names(again, "rejected the credential (HTTP 401)")
    assert len(armed_openvault.status_calls) == status_before
    assert len(armed_openvault.chat_calls) == 1
    assert "Hq3Lm8Rt5Wy2Kd7Np4Vx" not in str(env) + str(again)


# -- (g)-(i) armed serves, verdict credit, manifest refusal ------------------------


def test_g_armed_serve_names_served_model_and_credits_plausibility(armed_openvault) -> None:
    # Lowercase SQL: the gate's safe_sql differs from the model text, so verdict
    # credit must travel by call id, not by SQL string.
    armed_openvault.reply("select sku from inventory limit 5")
    env = _ask()
    assert len(armed_openvault.chat_calls) == 1
    assert env["layer"] == "generated", env
    assert env["badge"] == "L2_VALIDATED"
    rows = env.get("rows") or []
    assert rows
    for row in rows:
        assert str(row.get("sku") or "") in (env.get("answer") or "")
    body = armed_openvault.chat_calls[0]["body"]
    models, _ = freeroute.candidates(freeroute.arming())
    assert body["model"] in models and body["model"] != "gpt-4o-mini"
    assert "metadata" not in body
    assert "X-Cortex-Identity" not in armed_openvault.chat_calls[0]["headers"]
    served = armed_openvault.served_for(body["model"])
    assumptions = env.get("assumptions") or ""
    assert f"asked {body['model']}, served {served}" in assumptions
    assert "n=" not in assumptions and "score" not in assumptions
    assert _store_rows() == [(body["model"], served, 200, "plausible", 0)]


def test_h_implausible_serve_abstains_and_is_credited(armed_openvault, monkeypatch) -> None:
    from CortexOS.dms import l2_plausibility

    real = l2_plausibility.assess_plausibility

    def trip(*args, **kwargs):
        got = real(*args, **kwargs)
        return l2_plausibility.PlausibilityResult(
            ok=False, code="implausible_empty", reason="empty-success: forced for test"
        ) if got is not None else got

    monkeypatch.setattr(l2_plausibility, "assess_plausibility", trip)
    armed_openvault.reply("select sku from inventory limit 5")
    env = _ask()
    assert len(armed_openvault.chat_calls) == 1
    assert env["badge"] == "abstain" and env["rows"] == []
    assert "empty-success" in (env.get("answer") or "")
    rows = _store_rows()
    assert len(rows) == 1 and rows[0][3] == "implausible"


def test_i_manifest_refusal_stays_refused_with_bounded_calls(armed_openvault, dms_http) -> None:  # noqa: F811
    dms_http.bind_session("fr-manifest", {"transactions": "TRUE"})
    armed_openvault.default_content = "SELECT sku FROM inventory LIMIT 5"
    resp = dms_http.post("/dms/query", json={"question": QUESTION, "session_id": "fr-manifest"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("route") == "refused"
    assert body.get("layer") == "refused"
    assert _badge(body) == "refused"
    assert body.get("rows") in ([], None)
    assert body.get("sql_used") is None
    assert body.get("violations_blocked")
    assert 1 <= len(armed_openvault.chat_calls) <= 3


# -- (j)-(k) custody and the doc-RAG branch ----------------------------------------


def test_j_sealed_with_provider_env_keys_touches_no_other_host(armed_openvault, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + "q" * 30)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-" + "r" * 30)
    armed_openvault.sealed = True
    connects: list[Any] = []
    guarded = socket.socket.connect

    def record(self, address):
        connects.append(address)
        return guarded(self, address)

    monkeypatch.setattr(socket.socket, "connect", record)
    env = _ask()
    _assert_abstain_names(env, "vault is sealed")
    assert armed_openvault.non_openvault_calls == []
    assert [c["path"] for c in armed_openvault.calls] == ["/api/freeroute/status"]
    assert connects == []


def test_k_space_document_answer_still_serves_and_names_why_l2_did_not(
    armed_openvault, monkeypatch
) -> None:
    armed_openvault.sealed = True
    monkeypatch.setattr(
        "CortexOS.dms.query_service.rag_answer",
        lambda q: ("Stock notes say SKU-ALPHA is restocked weekly.", ["doc-7"]),
    )
    env = _ask(space_id="space-1")
    assert env["badge"] == "document"
    assert "SKU-ALPHA" in (env.get("answer") or "")
    assumptions = env.get("assumptions") or ""
    assert "L2 generation not used" in assumptions
    assert "vault is sealed" in assumptions
