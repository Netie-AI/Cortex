"""C-LOOP-A (#303) on the served envelope: POST /v1/contract/ask with a DMS payload.

Assertions are on the HTTP response DMS receives (R-0001): rendered answer
text, rows, badge, served_* and the step lines. The model is the scripted
OpenVault (tests/freeroute_fake.py) or the recorded C-STAMP capture
(tests/dms/fixtures/c_stamp_289); no test reaches a live model.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import socket
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cortex_contract.answer import AskRequest

from CortexOS.dms import plan_sql_ask
from CortexOS.integrations import freeroute
from CortexOS.memory import space_memory as sm
from CortexOS.plan_sql.generator import TASK_PLAN, TASK_SQL, ModelStep, StepStamp
from tests.dms.test_c7_02_manifest_before_explain import _badge, dms_http  # noqa: F401

SESSION = "c-loop-a-303"
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
PLAN_TEXT = (
    "1. Read transactions.\n"
    "2. Group by txn_type and count rows.\n"
    "3. Order by the count, largest first."
)
GOOD_SQL = "SELECT txn_type, COUNT(*) AS n FROM transactions GROUP BY txn_type ORDER BY n DESC"
PROVIDER = "groq"
MODEL = "openai/gpt-oss-120b"
CAPTURE = Path(__file__).resolve().parents[1] / "dms" / "fixtures" / "c_stamp_289"
CAPTURE_BODY_SHA256 = "1e3e7cb43f91f816079cfd66befc9f73761ec6e8d3fca3fbae3e667f6977a3fc"


@pytest.fixture(autouse=True)
def _plan_sql_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, "1")
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    sm.reset_space_memory_for_tests()
    yield
    sm.reset_space_memory_for_tests()


@pytest.fixture
def execute_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from CortexOS.execution import submit

    real = submit.execute_sql
    calls: list[str] = []

    def spy(verified, sql, **kwargs):  # noqa: ANN001
        calls.append(sql)
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", spy)
    return calls


def _stronger_route(fake, route: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish ``routes.stronger`` on the scripted status body. Not a request field.

    ``__call__`` is looked up on the class, so the transport function is replaced.
    """
    fake.stronger_route = route
    from CortexOS.integrations import openvault_client

    current = openvault_client.request_json

    def _call(method, path, **kwargs):  # noqa: ANN001
        status, body = current(method, path, **kwargs)
        if str(path).split("?", 1)[0].endswith("/api/freeroute/status") and isinstance(body, dict):
            body = dict(body)
            body["routes"] = {"stronger": fake.stronger_route}
            return status, body
        return status, body

    monkeypatch.setattr(openvault_client, "request_json", _call)


def _reply(fake, content: str, *, provider: str = PROVIDER, model: str = MODEL) -> None:
    """Constructed (not recorded) reply carrying OpenVault's served_* fields."""
    fake.replies.append(
        (
            200,
            {
                "model": model,
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "served_provider": provider,
                "served_model": model,
                "served_local": False,
            },
        )
    )


def _ran(spy: list[str]) -> list[str]:
    """SQL the model produced. Sample probes are ``SELECT DISTINCT``."""
    return [sql for sql in spy if not sql.lstrip().upper().startswith("SELECT DISTINCT")]


def _ask(
    client,
    *,
    payload: dict[str, Any] | None = PAYLOAD,
    question: str = QUESTION,
    **extra: Any,
):
    body: dict[str, Any] = {"question": question, "session_id": SESSION, **extra}
    if payload is not None:
        body["dms_payload"] = payload
    return client.post("/v1/contract/ask", json=body)


def _json(resp) -> dict[str, Any]:
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_not_answered(body: dict[str, Any], code: str) -> None:
    assert _badge(body).lower() == "abstain", body
    assert body["rows"] in ([], None)
    assert body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["served_provider"] is None and body["served_model"] is None
    assert code in body["answer"], body["answer"]
    assert any(code in line for line in body["assumptions"]), body["assumptions"]
    assert body["plan_sql_rung"] is None
    assert not any(line.startswith("rung:") for line in body["assumptions"])


def _assert_rung(body: dict[str, Any], rung: str) -> None:
    """The producing rung is on the wire, not only on the dropped step list."""
    assert body["plan_sql_rung"] == rung
    assert f"rung: {rung}" in body["assumptions"]
    step = next(line for line in body["assumptions"] if line.startswith("step rung:"))
    assert f"({rung})" in step
    if rung == plan_sql_ask.RUNG_RECONFIRM:
        assert body["provenance"]["badge"] == "reconfirm"
        assert body["rows"] in ([], None)
        assert body["sql_used"] is None
        return
    assert body["provenance"]["badge"] == "session"
    rows = body["rows"] or []
    assert rows and body["row_count"] == len(rows)
    assert not all(all(value is None for value in row.values()) for row in rows)


def _route_rows() -> list[tuple[Any, ...]]:
    path = freeroute.store_path()
    if not path.exists():
        return []
    con = sqlite3.connect(str(path))
    try:
        return con.execute("SELECT task, split, verdict FROM routes ORDER BY ts").fetchall()
    finally:
        con.close()


# -- flag off: byte-identical --------------------------------------------------


def _deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = itertools.count()
    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(int=next(counter)))
    monkeypatch.setattr(
        "CortexOS.execution.drillthrough.time", SimpleNamespace(time=lambda: 1_900_000_000.0)
    )


def _engine_bytes(client, monkeypatch: pytest.MonkeyPatch, **kwargs: Any) -> bytes:
    from CortexOS.dms.answer_engine import clear_session

    clear_session(SESSION)
    _deterministic(monkeypatch)
    resp = client.post(
        "/v1/contract/ask",
        json={"question": "what is our total revenue", "session_id": SESSION, **kwargs},
    )
    assert resp.status_code == 200, resp.text
    return resp.content


@pytest.mark.parametrize(
    ("flag", "with_payload"),
    [("0", True), ("", True), ("1", False)],
    ids=["flag_0_with_payload", "flag_unset_with_payload", "flag_on_without_payload"],
)
def test_flag_off_or_no_payload_ask_is_byte_identical(
    dms_http, monkeypatch: pytest.MonkeyPatch, flag: str, with_payload: bool  # noqa: F811
) -> None:
    def tripwire(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("plan+SQL path entered with the flag off or no payload")

    monkeypatch.setattr(plan_sql_ask, "plan_sql_answer", tripwire)
    monkeypatch.delenv(plan_sql_ask.ENABLED_ENV, raising=False)
    dms_http.bind_session(SESSION, GRANT)
    baseline = _engine_bytes(dms_http, monkeypatch)
    body = json.loads(baseline)
    assert body["rows"] and body["answer"] and body["drillthrough_token"]

    if flag:
        monkeypatch.setenv(plan_sql_ask.ENABLED_ENV, flag)
    extra = {"dms_payload": PAYLOAD} if with_payload else {}
    assert _engine_bytes(dms_http, monkeypatch, **extra) == baseline


# -- plan, then SQL, through FreeRoute ------------------------------------------


def test_payload_ask_plans_then_writes_sql_via_freeroute_with_stamped_steps(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, f"```sql\n{GOOD_SQL};\n```")
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    rows = body["rows"]
    assert rows and {r["txn_type"] for r in rows} and all(int(r["n"]) > 0 for r in rows)
    for row in rows:
        assert f"txn_type={row['txn_type']}, n={row['n']}" in body["answer"]
    assert body["row_count"] == len(rows)
    assert body["sql_used"].startswith(GOOD_SQL)
    assert _ran(execute_spy) == [body["sql_used"]]
    assert len(execute_spy) > 1  # distinct-value probes ran before the answer SQL
    assert body["provenance"]["layer"] == "plan_sql"
    assert body["provenance"]["badge"] == "session"
    assert "Not validated for accuracy" in body["provenance"]["assumptions"]
    assert body["drillthrough_token"]
    assert body["served_provider"] == PROVIDER and body["served_model"] == MODEL
    assert body["served_local"] is False
    assert [s["member"] for s in body["contributing_sources"]] == ["transactions"]

    lines = body["assumptions"]
    assert lines[:3] == [
        "plan: 1. Read transactions.",
        "plan: 2. Group by txn_type and count rows.",
        "plan: 3. Order by the count, largest first.",
    ]
    by_step = {line.split(":", 1)[0]: line for line in lines if line.startswith("step ")}
    assert list(by_step) == [
        "step grant",
        "step payload",
        "step sample",
        "step plan",
        "step sql",
        "step gate",
        "step execute",
        "step rung",
    ]
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)
    for step in ("step plan", "step sql"):
        assert "served_by=openvault-freeroute" in by_step[step]
        assert f"served_provider={PROVIDER}" in by_step[step]
        assert f"served_model={MODEL}" in by_step[step]
    for step in ("step grant", "step payload", "step gate", "step execute"):
        assert "served_by=cortex:" in by_step[step]
        assert "served_provider=none" in by_step[step]

    calls = armed_openvault.chat_calls
    assert len(calls) == 2
    assert armed_openvault.non_openvault_calls == []
    plan_prompt = calls[0]["body"]["messages"][1]["content"]
    sql_prompt = calls[1]["body"]["messages"][1]["content"]
    assert "transactions(txn_type VARCHAR -- IN or OUT, sku VARCHAR, quantity_kg DOUBLE)" in plan_prompt
    assert "COLUMN VALUES" in plan_prompt
    assert "transactions.txn_type:" in plan_prompt
    assert any(token in plan_prompt for token in ("'IN'", "'OUT'", "'ADJUST'", "'WRITE_OFF'"))
    assert "Do not write SQL" in calls[0]["body"]["messages"][0]["content"]
    assert "PLAN:\n1. Read transactions." in sql_prompt
    assert [r[0] for r in _route_rows()] == [TASK_PLAN, TASK_SQL]
    assert [r[2] for r in _route_rows()] == [None, "gate_pass"]


def test_payload_ask_with_join_replays_recorded_openvault_response(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    """SQL step replays the unedited C-STAMP capture (Groq, openai/gpt-oss-120b)."""
    raw = (CAPTURE / "resp_body.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == CAPTURE_BODY_SHA256
    recorded = json.loads(raw)
    _reply(armed_openvault, "1. Read inventory.\n2. Return five skus.")
    armed_openvault.replies.append((200, recorded))
    dms_http.bind_session(SESSION, {"inventory": "TRUE", "transactions": "TRUE"})
    payload = {
        "tables": [
            {"name": "inventory", "columns": [{"name": "sku"}, {"name": "quantity_kg"}]},
            {"name": "transactions", "columns": [{"name": "sku"}, {"name": "txn_type"}]},
        ],
        "joins": [
            {
                "left_table": "transactions",
                "left_column": "sku",
                "right_table": "inventory",
                "right_column": "sku",
                "relation": "many_to_one",
            }
        ],
    }

    body = _json(_ask(dms_http, payload=payload))

    assert body["sql_used"].rstrip(";").startswith("SELECT sku FROM inventory LIMIT 5")
    assert body["rows"] and len(body["rows"]) <= 5
    for row in body["rows"]:
        assert f"sku={row['sku']}" in body["answer"]
    assert body["served_provider"] == recorded["served_provider"] == "groq"
    assert body["served_model"] == recorded["served_model"] == "openai/gpt-oss-120b"
    sql_line = next(line for line in body["assumptions"] if line.startswith("step sql:"))
    assert "served_provider=groq served_model=openai/gpt-oss-120b" in sql_line
    prompt = armed_openvault.chat_calls[0]["body"]["messages"][1]["content"]
    assert "- transactions.sku = inventory.sku (many_to_one)" in prompt


# -- no overclaim: a plan_sql answer is never L2_VALIDATED ----------------------

# Every token through which an envelope becomes L2_VALIDATED: the L2 badge
# itself, and what contract_routes maps onto DMS's L2_VALIDATED (_FLAT_BADGE).
_L2_TOKENS = ("l2_validated", "query_skill", "generated")


def _overclaims(body: dict[str, Any]) -> list[str]:
    prov = body.get("provenance") or {}
    found = [
        f"{field}={value}"
        for field, value in (
            ("badge", body.get("badge")),
            ("layer", body.get("layer")),
            ("provenance.badge", prov.get("badge")),
            ("provenance.layer", prov.get("layer")),
        )
        if str(value or "").lower() in _L2_TOKENS
    ]
    if "l2_validated" in json.dumps(body).lower():
        found.append("L2_VALIDATED in envelope")
    return found


class _L2PortTripwire:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"plan_sql ask touched the L2 port ({name})")


def test_overclaim_detector_flags_a_real_l2_envelope() -> None:
    assert _overclaims({"layer": "generated", "badge": "L2_VALIDATED"})
    assert _overclaims({"provenance": {"layer": "plan_sql", "badge": "query_skill"}})
    assert _overclaims({"answer": "ok", "assumptions": ["badge L2_VALIDATED"]})
    assert _overclaims({"provenance": {"layer": "plan_sql", "badge": "session"}}) == []


@pytest.mark.parametrize("l2_flag", ["", "1"], ids=["l2_off", "l2_on"])
def test_plan_sql_session_success_never_stamps_l2_validated(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, l2_flag: str  # noqa: F811
) -> None:
    from CortexOS.dms import l2_generation

    monkeypatch.setenv("DMS_L2_ENABLED", l2_flag)
    monkeypatch.setattr(l2_generation, "_port", _L2PortTripwire())
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    resp = _ask(dms_http)
    body = _json(resp)

    assert body["rows"] and body["sql_used"].startswith(GOOD_SQL)
    assert body["provenance"]["layer"] == "plan_sql"
    assert body["provenance"]["badge"] == "session"
    assert _overclaims(body) == [], _overclaims(body)
    assert b"l2_validated" not in resp.content.lower()
    assert "Not validated for accuracy" in body["provenance"]["assumptions"]
    assert "Not validated for accuracy" in body["assumptions"][-1]
    assert len(armed_openvault.chat_calls) == 2


def test_response_without_served_stamp_is_refused(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    armed_openvault.reply(PLAN_TEXT)
    dms_http.bind_session(SESSION, GRANT)
    body = _json(_ask(dms_http))
    _assert_not_answered(body, plan_sql_ask.ROUTE_STAMP_MISSING)
    assert "served_provider and served_model" in body["answer"]
    assert len(armed_openvault.chat_calls) == 1
    assert _ran(execute_spy) == []


# -- must-fail 1: no model path but OpenVault FreeRoute ---------------------------


class DirectProviderGenerator:
    """Stands in for code that calls a provider SDK itself.

    It returns usable text and a well-formed stamp naming a provider, but the
    stamp never went through ``freeroute.complete``, so FreeRoute never
    journaled it.
    """

    def _step(self, kind: str, text: str) -> ModelStep:
        route = freeroute.RouteStamp(
            call_id=f"direct-{kind}",
            task=kind,
            requested="gpt-4o",
            served="gpt-4o",
            status=200,
            usable=True,
            served_provider="openai",
            served_model="gpt-4o",
        )
        return ModelStep(
            kind,
            True,
            text,
            "",
            StepStamp.from_route(kind, route, ""),
            route,
            (text,) if kind == "plan" else (),
        )

    def plan(self, request):  # noqa: ANN001
        return self._step("plan", "1. Count transactions by type.")

    def sql(self, request, plan, *, prior_violations=()):  # noqa: ANN001
        return self._step("sql", GOOD_SQL)


def test_must_fail_direct_provider_call_is_refused_not_executed(
    dms_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[str]  # noqa: F811
) -> None:
    monkeypatch.setattr(plan_sql_ask, "default_generator", DirectProviderGenerator)
    dms_http.bind_session(SESSION, GRANT)
    body = _json(_ask(dms_http))
    _assert_not_answered(body, plan_sql_ask.NOT_FREEROUTE)
    assert "not served through OpenVault FreeRoute" in body["answer"]
    assert _ran(execute_spy) == []


def test_must_fail_provider_env_keys_never_bypass_openvault(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, execute_spy: list[str]  # noqa: F811
) -> None:
    for name, value in (
        ("OPENAI_API_KEY", "sk-" + "r" * 30),
        ("ANTHROPIC_API_KEY", "sk-ant-" + "q" * 30),
        ("GROQ_API_KEY", "gsk_" + "p" * 30),
    ):
        monkeypatch.setenv(name, value)
    armed_openvault.sealed = True
    connects: list[Any] = []
    guarded = socket.socket.connect

    def record(self, address):  # noqa: ANN001
        connects.append(address)
        return guarded(self, address)

    monkeypatch.setattr(socket.socket, "connect", record)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert _badge(body).lower() == "abstain"
    assert body["rows"] in ([], None) and body["sql_used"] is None
    assert plan_sql_ask.MODEL_UNAVAILABLE in body["answer"]
    assert "vault is sealed" in body["answer"]
    assert armed_openvault.chat_calls == []
    assert armed_openvault.non_openvault_calls == []
    assert {c["path"] for c in armed_openvault.calls} == {"/api/freeroute/status"}
    assert connects == []
    assert _ran(execute_spy) == []


# -- must-fail 2: SQL that violates the gate is refused, not executed -------------


@pytest.mark.parametrize(
    ("sql", "code", "needle"),
    [
        (
            "SELECT read_csv_auto('/etc/passwd') FROM transactions",
            plan_sql_ask.MANIFEST_REFUSED,
            "MANIFEST:",
        ),
        (
            "SELECT sku FROM inventory LIMIT 5",
            plan_sql_ask.MANIFEST_REFUSED,
            "MANIFEST:",
        ),
    ],
    ids=["file_read_function", "table_outside_grant"],
)
def test_must_fail_gate_violating_sql_is_refused_not_executed(
    armed_openvault, dms_http, execute_spy: list[str], sql: str, code: str, needle: str  # noqa: F811
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, sql)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    _assert_not_answered(body, code)
    assert needle in body["answer"]
    assert _ran(execute_spy) == []
    assert all("read_csv" not in sql.lower() for sql in execute_spy)
    assert len(armed_openvault.chat_calls) == 2
    assert [r[2] for r in _route_rows()] == [None, "gate_fail"]


def test_must_fail_sql_outside_payload_is_refused_even_inside_grant(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    outside = "SELECT sku FROM inventory LIMIT 5"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, outside)
    _reply(armed_openvault, outside)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, outside)
    _reply(armed_openvault, "WHY: inventory was not selected\nCLOSEST: how many transactions of each type")
    dms_http.bind_session(SESSION, {"transactions": "TRUE", "inventory": "TRUE"})

    body = _json(_ask(dms_http))

    assert _badge(body) == "reconfirm", body
    assert body["rows"] in ([], None)
    assert body["reconfirm"]["closest_question"]
    assert _ran(execute_spy) == []
    assert all("inventory" not in sql.lower() for sql in execute_spy)
    prompts = [
        call["body"]["messages"][1]["content"] for call in armed_openvault.chat_calls
    ]
    assert any(plan_sql_ask.OUTSIDE_PAYLOAD in prompt for prompt in prompts)
    assert len(armed_openvault.chat_calls) > 2


def test_multi_statement_or_dml_never_reaches_execute(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, "SELECT * FROM transactions; DROP TABLE transactions")
    dms_http.bind_session(SESSION, GRANT)
    body = _json(_ask(dms_http))
    _assert_not_answered(body, plan_sql_ask.NO_SELECT)
    assert _ran(execute_spy) == []
    assert len(armed_openvault.chat_calls) == 2


# -- must-fail 3: the payload cannot widen the signed grant -----------------------


_WIDEN_CASES = {
    "extra_table": (
        GRANT,
        {"tables": [{"name": "transactions"}, {"name": "inventory"}]},
        "PLAN_SQL_PAYLOAD_WIDENS_GRANT:inventory",
    ),
    "join_to_table_outside_grant": (
        GRANT,
        {
            "tables": [{"name": "transactions"}],
            "joins": [
                {
                    "left_table": "transactions",
                    "left_column": "sku",
                    "right_table": "suppliers",
                    "right_column": "supplier_id",
                }
            ],
        },
        "PLAN_SQL_PAYLOAD_WIDENS_GRANT:suppliers",
    ),
    "join_outside_payload": (
        {"transactions": "TRUE", "inventory": "TRUE"},
        {
            "tables": [{"name": "Transactions"}],
            "joins": [
                {
                    "left_table": "transactions",
                    "left_column": "sku",
                    "right_table": "inventory",
                    "right_column": "sku",
                }
            ],
        },
        "PLAN_SQL_JOIN_OUTSIDE_PAYLOAD:inventory",
    ),
}


@pytest.mark.parametrize("case", sorted(_WIDEN_CASES))
def test_must_fail_payload_cannot_widen_the_signed_grant(
    armed_openvault, dms_http, execute_spy: list[str], case: str  # noqa: F811
) -> None:
    grant, payload, needle = _WIDEN_CASES[case]
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, "SELECT sku FROM inventory LIMIT 5")
    dms_http.bind_session(SESSION, grant)

    body = _json(_ask(dms_http, payload=payload))

    _assert_not_answered(body, needle.rsplit(":", 1)[0])
    assert needle in body["answer"]
    assert armed_openvault.chat_calls == [], "no model may see a widened schema"
    assert execute_spy == []


def test_unbound_space_is_refused_before_any_model_call(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT, space_id="alpha")
    body = _json(_ask(dms_http, space_id="alpha"))
    assert body["rows"]  # control: bound Space answers
    resp = _ask(dms_http, space_id="beta")
    assert resp.status_code == 409, resp.text
    assert len(armed_openvault.chat_calls) == 2


# -- #290 scored-pack guard: no memory writes, no route learning ------------------


@pytest.mark.parametrize(
    ("scored", "split"),
    [("curated_ceo", "benchmark"), (None, "product")],
    ids=["scored_round", "product_round"],
)
def test_scored_round_writes_no_memory_and_never_trains_the_route(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, scored: str | None, split: str  # noqa: F811
) -> None:
    monkeypatch.setenv(sm.ENABLED_ENV, "1")
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT, space_id="alpha")
    extra = {"scored_pack_id": scored} if scored else {}

    body = _json(_ask(dms_http, space_id="alpha", **extra))

    assert body["rows"] and body["provenance"]["layer"] == "plan_sql"
    assert body["memory_ids_read"] == [] and body["reused"] is False
    ops = [s.served_op for s in sm.get_space_memory().stamps(space_id="alpha")]
    assert "write" not in ops
    assert sm.get_space_memory().read(space_id="alpha", kind="solution", actor="t")[0] == []
    assert [r[1] for r in _route_rows()] == [split, split]


# -- thinking loop: sample, retry, stronger model, reconfirm ----------------------


def test_must_fail_unknown_column_retries_and_does_not_execute_it(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    """A column the gate rejects is fed back and rewritten. The bad SQL never runs."""
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, "SELECT no_such_col FROM transactions")
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["rows"] and body["sql_used"].startswith(GOOD_SQL)
    assert all("no_such_col" not in sql.lower() for sql in execute_spy)
    assert _ran(execute_spy) == [body["sql_used"]]
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    assert "UNKNOWN_COLUMN:no_such_col" in retry
    assert len(armed_openvault.chat_calls) == 3
    _assert_rung(body, plan_sql_ask.rung_retry(1))


def test_must_fail_empty_result_retries_where_the_old_head_abstained(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    """The pre-thinking head abstained on zero rows after one SQL write.

    This head feeds EMPTY_RESULT back and regenerates. One scripted empty
    SELECT plus a follow-up that returns rows must answer, not abstain.
    """
    empty = (
        "SELECT txn_type, COUNT(*) AS n FROM transactions "
        "WHERE txn_type = 'NOT_A_TYPE' GROUP BY txn_type"
    )
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, empty)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["rows"] and body["provenance"]["badge"] == "session"
    assert plan_sql_ask.EMPTY_RESULT not in body["answer"]
    assert len(armed_openvault.chat_calls) == 3
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    assert plan_sql_ask.EMPTY_RESULT in retry
    assert "NOT_A_TYPE" in retry
    assert _ran(execute_spy)[-1] == body["sql_used"]
    _assert_rung(body, plan_sql_ask.rung_retry(1))


def test_implausible_all_null_rows_are_fed_back(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from CortexOS.execution import submit

    real = submit.execute_sql
    state = {"nulls": True}

    def fake(verified, sql, **kwargs):  # noqa: ANN001
        if state["nulls"] and "COUNT(*)" in sql:
            state["nulls"] = False
            return ([{"n": None}], 0.0, 0.0)
        return real(verified, sql, **kwargs)

    monkeypatch.setattr(submit, "execute_sql", fake)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["rows"] and body["provenance"]["badge"] == "session"
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    assert plan_sql_ask.IMPLAUSIBLE_RESULT in retry
    _assert_rung(body, plan_sql_ask.rung_retry(1))


def test_exhausted_retries_then_a_stronger_openvault_model(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    # Not the next groq catalogue id. Cortex must ask for the route OV configured.
    configured = "openai/gpt-oss-20b"
    _stronger_route(armed_openvault, configured, monkeypatch)
    bad = "SELECT no_such_col FROM transactions"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, PLAN_TEXT, model=configured)
    _reply(armed_openvault, GOOD_SQL, model=configured)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["rows"] and body["sql_used"].startswith(GOOD_SQL)
    models = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert len(models) == 5
    assert models[0] == models[1] == models[2]
    assert models[3] == models[4] == configured
    assert models[3] != models[0]
    assert "qwen/qwen3.6-27b" not in models
    retry = armed_openvault.chat_calls[2]["body"]["messages"][1]["content"]
    strong = armed_openvault.chat_calls[4]["body"]["messages"][1]["content"]
    assert "UNKNOWN_COLUMN:no_such_col" in retry
    assert "UNKNOWN_COLUMN:no_such_col" in strong
    assert any(line.startswith("step escalate:") for line in body["assumptions"])
    assert any(line.startswith("step plan-strong:") for line in body["assumptions"])
    assert "served_by=openvault-freeroute" in next(
        line for line in body["assumptions"] if line.startswith("step plan-strong:")
    )
    assert armed_openvault.non_openvault_calls == []
    _assert_rung(body, plan_sql_ask.RUNG_STRONG)


def test_still_unresolved_reconfirm_names_why_and_the_closest_question(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    bad = "SELECT no_such_col FROM transactions"
    why = "the selected column is not in the table"
    closest = "how many transactions of each type"
    configured = "openai/gpt-oss-20b"
    _stronger_route(armed_openvault, configured, monkeypatch)
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, PLAN_TEXT, model=configured)
    _reply(armed_openvault, bad, model=configured)
    _reply(armed_openvault, f"WHY: {why}\nCLOSEST: {closest}", model=configured)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["provenance"]["badge"] == "reconfirm"
    assert body["route"] == "reconfirm"
    assert body["rows"] in ([], None) and body["sql_used"] is None
    assert body["drillthrough_token"] is None
    assert body["reconfirm"] == {"why": why, "closest_question": closest}
    assert why in body["answer"] and closest in body["answer"]
    assert "not found in the database" in body["answer"]
    assert _ran(execute_spy) == []
    assert any(line.startswith("step reconfirm-strong:") for line in body["assumptions"])
    assert body["served_provider"] == PROVIDER and body["served_model"] == configured
    _assert_rung(body, plan_sql_ask.RUNG_RECONFIRM)


def test_ladder_rung_stamps_appear_on_the_answer(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Each producing rung is on the customer envelope Check reads.

    Tokens: plan, error-fed-retry-N, stronger-model, reconfirm.
    A session rung has rows. An empty or all-null result is not a session
    answer, so this path does not add a WRONG.
    """
    bad = "SELECT no_such_col FROM transactions"
    configured = "openai/gpt-oss-20b"
    _stronger_route(armed_openvault, configured, monkeypatch)
    dms_http.bind_session(SESSION, GRANT)

    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    planned = _json(_ask(dms_http, question="how many of each type"))
    _assert_rung(planned, "plan")
    planned_model = armed_openvault.chat_calls[0]["body"]["model"]

    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, GOOD_SQL)
    retried = _json(_ask(dms_http, question="count the types again"))
    _assert_rung(retried, "error-fed-retry-1")

    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, PLAN_TEXT, model=configured)
    _reply(armed_openvault, GOOD_SQL, model=configured)
    stronger = _json(_ask(dms_http, question="count types on the stronger model"))
    _assert_rung(stronger, "stronger-model")
    models = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert models[0] == planned_model
    assert configured in models

    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, PLAN_TEXT, model=configured)
    _reply(armed_openvault, bad, model=configured)
    _reply(
        armed_openvault,
        "WHY: no grounded column\nCLOSEST: how many rows are in transactions",
        model=configured,
    )
    asked = _json(_ask(dms_http, question="count types until reconfirm"))
    _assert_rung(asked, "reconfirm")
    assert {planned["plan_sql_rung"], retried["plan_sql_rung"], stronger["plan_sql_rung"], asked["plan_sql_rung"]} == {
        "plan",
        "error-fed-retry-1",
        "stronger-model",
        "reconfirm",
    }


def test_confirm_yes_runs_the_closest_question(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    closest = "how many transactions of each type"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, question=closest, plan_sql_confirm="yes"))

    assert body["rows"] and body["provenance"]["badge"] == "session"
    assert body["reconfirm"] is None
    _assert_rung(body, plan_sql_ask.RUNG_PLAN)
    assert any(line.startswith("step confirm:") for line in body["assumptions"])
    assert "confirm" in next(line for line in body["assumptions"] if line.startswith("step confirm:"))


def test_confirm_no_answers_not_found_in_the_database(
    armed_openvault, dms_http, execute_spy: list[str]  # noqa: F811
) -> None:
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, plan_sql_confirm="no"))

    assert body["answer"] == "not found in the database"
    assert body["provenance"]["badge"] == "session"
    assert body["rows"] in ([], None) and body["sql_used"] is None
    assert body["reconfirm"] is None
    assert body["plan_sql_rung"] is None
    assert armed_openvault.chat_calls == []
    assert execute_spy == []


def test_pii_wording_is_not_an_abstain_and_is_not_masked(
    armed_openvault, dms_http  # noqa: F811
) -> None:
    question = "list operator emails and phone numbers"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, GOOD_SQL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http, question=question))

    assert body["rows"] and body["provenance"]["badge"] == "session"
    assert "abstain" not in body["answer"].lower()
    plan_prompt = armed_openvault.chat_calls[0]["body"]["messages"][1]["content"]
    assert "emails" in plan_prompt and "phone numbers" in plan_prompt


@pytest.mark.parametrize("field", ["model", "strict", "provider"])
def test_unknown_request_field_is_422(
    armed_openvault, dms_http, field: str  # noqa: F811
) -> None:
    """Cortex does not model ``model`` or ``strict``. Unknown keys are 422."""
    assert field not in AskRequest.model_fields
    resp = dms_http.post(
        "/v1/contract/ask",
        json={"question": QUESTION, "session_id": SESSION, "dms_payload": PAYLOAD, field: "gpt"},
    )
    assert resp.status_code == 422, resp.text
    assert field in resp.text
    assert armed_openvault.chat_calls == []
    assert armed_openvault.non_openvault_calls == []


def test_model_outside_allowlist_is_a_named_refusal_not_another_provider(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """OV route id that is not in the FreeRoute allowlist is refused. No substitute."""
    missing = "not-a-catalogue-model"
    _stronger_route(armed_openvault, missing, monkeypatch)
    bad = "SELECT no_such_col FROM transactions"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["provenance"]["badge"] == "abstain"
    assert freeroute.MODEL_NOT_ALLOWLISTED in body["answer"]
    assert missing in body["answer"]
    assert _ran(execute_spy) == []
    asked = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert missing not in asked
    assert "qwen/qwen3.6-27b" not in asked
    assert armed_openvault.non_openvault_calls == []


def test_unserved_pin_reconfirms_and_names_the_missing_model(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """OV served a different model than the pinned route. That answer is not used."""
    configured = "openai/gpt-oss-20b"
    _stronger_route(armed_openvault, configured, monkeypatch)
    bad = "SELECT no_such_col FROM transactions"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    # Stronger plan and SQL: OV stamps a different model than the one it was asked for.
    _reply(armed_openvault, PLAN_TEXT, model=MODEL)
    _reply(armed_openvault, GOOD_SQL, model=MODEL)
    _reply(armed_openvault, "WHY: ignored substitute\nCLOSEST: ignored substitute", model=MODEL)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["provenance"]["badge"] == "reconfirm"
    assert freeroute.MODEL_NOT_SERVED in body["answer"] or any(
        freeroute.MODEL_NOT_SERVED in line for line in body["assumptions"]
    )
    assert configured in body["answer"] or any(configured in line for line in body["assumptions"])
    assert "ignored substitute" not in body["answer"]
    assert _ran(execute_spy) == []
    assert body["sql_used"] is None
    strong = [
        call["body"]["model"]
        for call in armed_openvault.chat_calls
        if call["body"]["model"] == configured
    ]
    assert strong
    served = next(line for line in body["assumptions"] if line.startswith("step plan-strong:"))
    assert f"served_model={MODEL}" in served
    assert f"served_provider={PROVIDER}" in served
    assert armed_openvault.non_openvault_calls == []


def test_unset_stronger_route_does_not_pick_a_provider(
    armed_openvault, dms_http, execute_spy: list[str], monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    _stronger_route(armed_openvault, "", monkeypatch)
    bad = "SELECT no_such_col FROM transactions"
    _reply(armed_openvault, PLAN_TEXT)
    _reply(armed_openvault, bad)
    _reply(armed_openvault, bad)
    dms_http.bind_session(SESSION, GRANT)

    body = _json(_ask(dms_http))

    assert body["plan_sql_rung"] == plan_sql_ask.RUNG_RECONFIRM
    assert freeroute.NO_STRONGER_ROUTE in " ".join(body["assumptions"])
    asked = [call["body"]["model"] for call in armed_openvault.chat_calls]
    assert asked
    assert set(asked) == {MODEL}
    assert _ran(execute_spy) == []
    assert armed_openvault.non_openvault_calls == []
