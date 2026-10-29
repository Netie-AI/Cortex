"""CX-STRICT-01: opt-in OpenVault strict model pin.

OpenVault #81 accepts JSON ``strict: true`` (boolean) or header
``X-OpenVault-Strict: true|1|yes``. A parked pin is HTTP 503
``pin_unavailable`` with ``error.retry_after_s`` and ``Retry-After``. A token
budget refusal is HTTP 429 with ``Retry-After``.

Collected by the existing ``python -m pytest tests/`` CI step. No new workflow.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from CortexOS.dms import plan_sql_ask
from CortexOS.integrations import freeroute as fr
from CortexOS.integrations import openvault_client
from CortexOS.memory import space_memory as sm
from CortexOS.plan_sql.generator import StepStamp
from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

PIN = "openai/gpt-oss-120b"
MESSAGES = [{"role": "user", "content": "cx-strict-01"}]


def _bytes(body: dict[str, Any]) -> bytes:
    """Bytes ``request_json`` would send: json.dumps of the dict, default separators."""
    return json.dumps(dict(body)).encode("utf-8")


def _main_body(
    messages: list[dict[str, str]],
    *,
    model: str = PIN,
    max_tokens: int = 600,
    temperature: float | None = None,
) -> dict[str, Any]:
    """The chat body main sends for this call. No strict key."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    return body


def _isolate_store(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv(fr.STORE_ENV, str(tmp_path / "routes.db"))
    fr.reset()


def _served_reply(fake, *, model: str = PIN, provider: str = "groq") -> None:
    fake.replies.append(
        (
            200,
            {
                "model": model,
                "choices": [{"message": {"role": "assistant", "content": "SELECT 1"}}],
                "served_provider": provider,
                "served_model": model,
                "served_local": False,
            },
        )
    )


def _pin_unavailable(fake, *, retry_after_s: int | None, reason: str = "parked") -> None:
    fake.replies.append(
        (
            503,
            {
                "error": {
                    "message": "pinned model has no healthy hop",
                    "type": "pin_unavailable",
                    "model": PIN,
                    "reason": reason,
                    "provider": "groq",
                    "park_reason": "rate_limited" if retry_after_s is not None else None,
                    "retry_after_s": retry_after_s,
                },
                "served_provider": None,
                "served_model": None,
                "served_local": False,
            },
        )
    )


@pytest.mark.parametrize("flag", [None, "", "0", "false", "no", "off"])
def test_flag_off_body_matches_main_bytes(armed_openvault, monkeypatch, tmp_path, flag) -> None:
    """Flag off: the posted JSON is byte-identical to the body main sends."""
    _isolate_store(monkeypatch, tmp_path)
    if flag is None:
        monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    else:
        monkeypatch.setenv(fr.STRICT_ENV, flag)
    _served_reply(armed_openvault)
    out = fr.complete("cx-strict", list(MESSAGES), pin=PIN, max_tokens=600, temperature=0.0)
    assert out.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert "strict" not in sent
    assert _bytes(sent) == _bytes(_main_body(MESSAGES, temperature=0.0))
    assert "model_requested" not in out.stamp.public()
    assert "model_served" not in out.stamp.public()


def test_flag_on_strict_field_is_json_true(armed_openvault, monkeypatch, tmp_path) -> None:
    """Flag on: ``strict`` is the JSON boolean true, and nothing else moves."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    _served_reply(armed_openvault)
    off = fr.complete("cx-strict", list(MESSAGES), pin=PIN, max_tokens=600, temperature=0.0)
    assert off.ok is True
    off_bytes = _bytes(armed_openvault.chat_calls[-1]["body"])

    monkeypatch.setenv(fr.STRICT_ENV, "1")
    _served_reply(armed_openvault)
    on = fr.complete("cx-strict", list(MESSAGES), pin=PIN, max_tokens=600, temperature=0.0)
    assert on.ok is True
    sent = armed_openvault.chat_calls[-1]["body"]
    assert sent["strict"] is True
    expected = _main_body(MESSAGES, temperature=0.0)
    expected["strict"] = True
    assert _bytes(sent) == _bytes(expected)
    without = dict(sent)
    del without["strict"]
    assert _bytes(without) == off_bytes
    assert on.stamp is not None
    assert on.stamp.model_requested == PIN
    assert on.stamp.model_served == PIN


def test_flag_on_stamp_copies_onto_the_ask_step(armed_openvault, monkeypatch, tmp_path) -> None:
    """The served step stamp the /ask path copies records both models."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    _served_reply(armed_openvault)
    out = fr.complete("cx-strict", list(MESSAGES), pin=PIN, max_tokens=600)
    step = StepStamp.from_route("plan", out.stamp, "")
    assert step.model_requested == PIN
    assert step.model_served == PIN
    public = step.public()
    assert public["model_requested"] == PIN
    assert public["model_served"] == PIN
    assert f"model_requested={PIN}" in step.line()
    assert f"model_served={PIN}" in step.line()


def test_strict_pin_unavailable_raises_with_retry_after(
    armed_openvault, monkeypatch, tmp_path
) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    _pin_unavailable(armed_openvault, retry_after_s=12)
    with pytest.raises(fr.FreeRouteStrictError) as caught:
        fr.complete("cx-strict", list(MESSAGES), pin=PIN, max_tokens=600)
    exc = caught.value
    assert exc.status == 503
    assert exc.error_type == "pin_unavailable"
    assert exc.retry_after_s == 12
    assert exc.model_requested == PIN
    assert exc.model_served is None
    assert exc.stamp is not None
    assert exc.stamp.model_requested == PIN
    assert exc.stamp.model_served is None
    assert len(armed_openvault.chat_calls) == 1
    assert armed_openvault.chat_calls[0]["body"]["strict"] is True
    assert fr.strict_error_status(exc) == 503
    detail = fr.strict_error_detail(exc)
    assert detail["retry_after_s"] == 12
    assert detail["code"] == "FreeRouteStrictError"


def test_strict_circuit_open_raises_with_null_retry(
    armed_openvault, monkeypatch, tmp_path
) -> None:
    """A circuit-open pin has no wait. It still refuses instead of falling through."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    _pin_unavailable(armed_openvault, retry_after_s=None, reason="circuit_open")
    with pytest.raises(fr.FreeRouteStrictError) as caught:
        fr.complete("cx-strict", list(MESSAGES), pin=PIN)
    assert caught.value.retry_after_s is None
    assert caught.value.error_type == "pin_unavailable"
    assert len(armed_openvault.chat_calls) == 1


def test_strict_429_body_retry_after(armed_openvault, monkeypatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    armed_openvault.replies.append(
        (
            429,
            {
                "error": {
                    "message": "FreeRoute token budget exceeded; retry later",
                    "type": "rate_limited",
                    "retry_after_s": 7,
                }
            },
        )
    )
    with pytest.raises(fr.FreeRouteStrictError) as caught:
        fr.complete("cx-strict", list(MESSAGES), pin=PIN)
    exc = caught.value
    assert exc.status == 429
    assert exc.error_type == "rate_limited"
    assert exc.retry_after_s == 7
    assert exc.model_requested == PIN
    assert exc.model_served is None
    assert fr.strict_error_status(exc) == 429
    assert len(armed_openvault.chat_calls) == 1


def test_real_429_retry_after_header_is_captured() -> None:
    """request_json drops headers. complete() still reads Retry-After off urllib."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler name
            raw = b'{"error":{"type":"rate_limited","message":"budget"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "4")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        fr.note_response_headers({})
        status, data = fr._call_noting_headers(
            openvault_client.request_json,
            "POST",
            "/v1/chat/completions",
            body={"model": PIN, "messages": list(MESSAGES), "max_tokens": 8, "stream": False},
            timeout=5,
            base=f"http://127.0.0.1:{port}",
        )
        headers = fr.take_response_headers()
    finally:
        server.shutdown()
        server.server_close()
    assert status == 429
    assert isinstance(data, dict)
    assert "retry_after_s" not in data["error"]
    assert fr._retry_after_s(data, headers) == 4


def test_strict_429_retry_after_header(armed_openvault, monkeypatch, tmp_path) -> None:
    """OV's budget 429 puts the wait on Retry-After, not on the JSON body."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    fake = armed_openvault

    def wrapped(method, path, **kwargs):
        status, data = fake(method, path, **kwargs)
        route = str(path).split("?", 1)[0]
        if route == "/v1/chat/completions" and status == 429:
            fr.note_response_headers({"Retry-After": "9"})
        return status, data

    monkeypatch.setattr(openvault_client, "request_json", wrapped)
    fake.replies.append(
        (429, {"error": {"message": "budget", "type": "rate_limited"}})
    )
    with pytest.raises(fr.FreeRouteStrictError) as caught:
        fr.complete("cx-strict", list(MESSAGES), pin=PIN)
    assert caught.value.status == 429
    assert caught.value.retry_after_s == 9


def test_flag_off_429_and_pin_unavailable_stay_completions(
    armed_openvault, monkeypatch, tmp_path
) -> None:
    """Flag off does not raise and does not add the strict field."""
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    armed_openvault.reply("", status=429, message="token budget")
    budget = fr.complete("cx-strict", list(MESSAGES), pin=PIN)
    assert budget.ok is False
    assert "429" in budget.reason
    assert "strict" not in armed_openvault.chat_calls[-1]["body"]

    _pin_unavailable(armed_openvault, retry_after_s=12)
    refused = fr.complete("cx-strict", list(MESSAGES), pin=PIN)
    assert refused.ok is False
    assert refused.stamp is not None
    assert "strict" not in armed_openvault.chat_calls[-1]["body"]


# -- /v1/contract/ask ----------------------------------------------------------


SESSION = "cx-strict-01"
QUESTION = "how many transactions of each type"
GRANT = {"transactions": "TRUE"}
PAYLOAD: dict[str, Any] = {
    "tables": [
        {
            "name": "transactions",
            "columns": [
                {"name": "txn_type", "type": "VARCHAR", "description": "IN or OUT"},
                {"name": "sku", "type": "VARCHAR"},
                {"name": "quantity_kg", "type": "DOUBLE"},
            ],
        }
    ]
}
GOOD_SQL = "SELECT txn_type, COUNT(*) AS n FROM transactions GROUP BY txn_type ORDER BY n DESC"


def _arm_ask(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _isolate_store(monkeypatch, tmp_path)
    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    monkeypatch.setenv(fr.MODELS_ENV, PIN)
    monkeypatch.setenv(fr.STRICT_ENV, "1")
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    monkeypatch.delenv("CORTEX_PLAN_SQL_VOTE", raising=False)
    sm.reset_space_memory_for_tests()


def _reply(fake, content: str) -> None:
    fake.replies.append(
        (
            200,
            {
                "model": PIN,
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "served_provider": "groq",
                "served_model": PIN,
                "served_local": False,
            },
        )
    )


def test_ask_success_stamps_model_requested_and_served(
    armed_openvault, dms_http, monkeypatch, tmp_path  # noqa: F811
) -> None:
    """A served /ask step line carries model_requested and model_served."""
    _arm_ask(monkeypatch, tmp_path)
    # GEN-GRANTED-CATALOG-01 (#386): the plan is folded into the one SQL call.
    _reply(armed_openvault, f"```sql\n{GOOD_SQL};\n```")
    dms_http.bind_session(SESSION, GRANT)
    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": QUESTION, "session_id": SESSION, "dms_payload": PAYLOAD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["served_model"] == PIN
    # No separate plan call, so no plan step line: every model step is the SQL call.
    assert not any(line.startswith("step plan:") for line in body["assumptions"])
    sql = next(line for line in body["assumptions"] if line.startswith("step sql:"))
    assert f"model_requested={PIN}" in sql
    assert f"model_served={PIN}" in sql
    assert len(armed_openvault.chat_calls) == 1
    assert all(call["body"]["strict"] is True for call in armed_openvault.chat_calls)


def test_ask_strict_refusal_does_not_fall_back(
    armed_openvault, dms_http, monkeypatch, tmp_path  # noqa: F811
) -> None:
    """A pin refusal on /ask is the typed error. No second model is called."""
    _arm_ask(monkeypatch, tmp_path)
    _pin_unavailable(armed_openvault, retry_after_s=12)
    # A fallback would consume this and serve it. It must stay queued.
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)
    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": QUESTION, "session_id": SESSION, "dms_payload": PAYLOAD},
    )
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "FreeRouteStrictError"
    assert detail["error_type"] == "pin_unavailable"
    assert detail["retry_after_s"] == 12
    assert detail["model_requested"] == PIN
    assert detail["model_served"] is None
    strict_steps = [
        step for step in detail["stamps"] if step.get("served_step") == "strict"
    ]
    assert len(strict_steps) == 1
    assert strict_steps[0]["model_requested"] == PIN
    assert strict_steps[0]["model_served"] is None
    assert len(armed_openvault.chat_calls) == 1
    assert armed_openvault.chat_calls[0]["body"]["model"] == PIN
    assert armed_openvault.chat_calls[0]["body"]["strict"] is True
