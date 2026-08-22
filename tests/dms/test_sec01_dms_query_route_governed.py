"""SEC-01 + P0-DEMO-02 on the route the product actually calls: POST /dms/query.

Both tickets are one defect. ``answer_engine.answer`` accepted
``verified: VerifiedManifest | None`` and minted a self-issued grant over *every*
table the semantic layer declares whenever the argument was omitted - and
``/dms/query`` and ``/mcp/call`` omitted it. ``get_session_registry()`` was
consulted only by ``/v1/contract/ask`` and drillthrough, so a session that bound
a narrow grant still got the wide self-issued one on the product path, and
P0-DEMO-02's grounding gate intersected against a grant that already contained
``transactions``. The gate was vacuous exactly where the customer stands.

Measured before the fix, with a session bound to ``q3_sales_export`` only:

    POST /dms/query {"question": "total revenue in my uploaded file"}
    -> 200, badge "governed_metric", rows [{"revenue_myr": 80375993.99}]

That is the demo-fatal number from Netie's own synthetic warehouse, reported
about a file the customer uploaded.

Every assertion here is on the HTTP response envelope (R-0001, CLAUDE.md
section 8). The eight pre-existing SEC-01 tests all call ``answer()`` in
process, which is precisely why this hole survived them.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from CortexOS.execution.manifest import (
    JwksCache,
    Manifest,
    ManifestVerifier,
    PathNotAllowed,
    SqlNotAnalyzable,
    StatementNotAllowed,
    canonical_manifest_bytes,
)

# The demo-warehouse figure P0-DEMO-02 was filed for. It must never reach a
# session that did not bind ``transactions``, under any badge.
DEMO_WAREHOUSE_REVENUE = "80375993.99"
DEMO_WAREHOUSE_REVENUE_GROUPED = "80,375,993"

# The three questions the ticket measured.
UPLOAD_QUESTIONS = [
    "total revenue in my uploaded file",
    "what is the total amount in the Q3 sales export",
    "top 3 customers by amount",
]

NARROW_SESSION = "sec01-http-upload"
WIDE_SESSION = "sec01-http-warehouse"


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _jwk(kid: str, private: Ed25519PrivateKey) -> dict[str, object]:
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "alg": "EdDSA",
        "use": "sig",
        "kid": kid,
        "x": _b64u(raw),
    }


@pytest.fixture
def dms_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A live ``/dms/query`` app with two real signed session bindings.

    ``NARROW_SESSION`` bound to an uploaded workbook only; ``WIDE_SESSION``
    bound to the demo warehouse. Same app, same route, different grants - the
    only variable is the manifest, which is the point.
    """
    from fastapi.testclient import TestClient

    from CortexOS.api.app import create_app
    from CortexOS.dms.answer_engine import clear_session
    from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
    from CortexOS.execution.session_manifests import (
        get_session_registry,
        reset_session_registry_for_tests,
    )
    from CortexOS.execution.submit import set_verifier_for_tests
    from packs.dms.security.rate_limit import reset_limiter

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")

    issuer = Ed25519PrivateKey.generate()
    cache = JwksCache(path=tmp_path / "jwks.json")
    cache.install({"keys": [_jwk("int-1", issuer)]})
    verifier = ManifestVerifier(cache)
    set_verifier_for_tests(verifier)
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    reset_limiter(10_000)

    def _bind(session_id: str, row_predicates: dict[str, str]) -> None:
        now = datetime.now(timezone.utc)
        manifest = Manifest(
            session_id=session_id,
            org_id="acme",
            pool_id="pool-a",
            issuer_key_id="int-1",
            allowed_paths=["/data/pool/acme/**"],
            row_predicates=row_predicates,
            issued_at=now.isoformat(),
            expires_at=(now + timedelta(minutes=5)).isoformat(),
            signature="",
        )
        manifest.signature = _b64u(issuer.sign(canonical_manifest_bytes(manifest)))
        get_session_registry().bind(verifier.verify(manifest))
        clear_session(session_id)

    _bind(NARROW_SESSION, {"q3_sales_export": "TRUE"})
    _bind(WIDE_SESSION, {"transactions": "TRUE"})

    client = TestClient(create_app())
    client.bind_session = _bind  # type: ignore[attr-defined]
    yield client

    set_verifier_for_tests(None)
    reset_session_registry_for_tests()
    reset_limiter()


def _ask(client, question: str, session_id: str) -> dict[str, Any]:
    resp = client.post("/dms/query", json={"question": question, "session_id": session_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_no_demo_warehouse_number(body: dict[str, Any]) -> None:
    text = body.get("answer") or ""
    assert DEMO_WAREHOUSE_REVENUE not in text
    assert DEMO_WAREHOUSE_REVENUE_GROUPED not in text
    assert body.get("rows") in ([], None), body.get("rows")
    assert body.get("source_table") != "transactions"


# -- (a) the P0: a narrow session must not be answered from the demo warehouse -
@pytest.mark.parametrize("question", UPLOAD_QUESTIONS)
def test_narrow_session_is_not_answered_from_the_demo_warehouse(
    dms_http, question: str
) -> None:
    """The measured P0, asserted on the envelope POST /dms/query returns."""
    body = _ask(dms_http, question, NARROW_SESSION)

    # A green badge over refusal prose is itself a P0 (CLAUDE.md section 8).
    assert body["badge"] not in ("governed_metric", "certified", "query_skill")
    assert body["badge"] in ("abstain", "refused")
    assert body["route"] in ("needs_clarification", "refused")
    assert body.get("sql_used") is None
    _assert_no_demo_warehouse_number(body)


def test_narrow_session_envelope_names_the_sources_it_can_answer_over(
    dms_http,
) -> None:
    """P0-DEMO-02 clause: say which sources it CAN answer over."""
    body = _ask(dms_http, "what is the total amount in the Q3 sales export", NARROW_SESSION)

    assert "q3_sales_export" in body["answer"].lower()
    assert body.get("granted_sources") == ["q3_sales_export"]


def test_narrow_session_refusal_names_the_ungranted_table(dms_http) -> None:
    """An operator has to see which source was refused, and why."""
    body = _ask(dms_http, "total revenue in my uploaded file", NARROW_SESSION)

    assert "transactions" in (body.get("assumptions") or "").lower()


# -- the mechanism: /dms/query must resolve the session's bound manifest ------
def test_bound_session_governs_the_dms_query_route(dms_http) -> None:
    """The root cause, asserted directly.

    ``/dms/query`` never called ``get_session_registry().resolve``, so a bound
    session still ran under the wide self-issued grant. If this reports
    ``local-self-issued`` for a session that really did bind a manifest, the
    grounding gate above is intersecting against the wrong set.
    """
    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)

    assert body["grant_kind"] == "session"
    assert body["granted_sources"] == ["transactions"]


# -- (c) R-0005: a granted session must still get its answer ------------------
def test_granted_session_still_answers_a_governed_metric(dms_http) -> None:
    """A control that refuses legitimate work is a failure, not a win.

    Deliberately a GOVERNED_METRIC question. The two pre-existing R-0005
    controls both use "Top 5 selling SKUs by revenue", which resolves at the
    CERTIFIED layer and so never exercises ``route_to_metric`` - the layer the
    P0 actually travelled through.
    """
    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)

    assert body["badge"] == "governed_metric"
    assert body["route"] == "sql"
    assert body["rows"], "a granted governed metric must still return rows"
    assert DEMO_WAREHOUSE_REVENUE in body["answer"], body["answer"]
    assert body["rows"][0]["revenue_myr"] == pytest.approx(80375993.99)


def test_granted_session_still_answers_a_certified_query(dms_http) -> None:
    """The certified layer must survive the change too (R-0005)."""
    body = _ask(dms_http, "Top 5 selling SKUs by revenue", WIDE_SESSION)

    assert body["badge"] == "certified"
    assert len(body["rows"]) == 5
    assert "SKU-" in body["answer"]


def test_an_ungrounded_served_turn_refuses_instead_of_widening(dms_http) -> None:
    """P0-DEMO-02, decided: the demo does not get to be the reason this leaks.

    This test used to assert the opposite - that an unbound session still
    answers, with `grant_kind == "local-self-issued"` - and it was written as a
    deliberate R-0005 control so a bare demo kept working. It was also, in
    effect, a green certification of an open P0: "total revenue in my uploaded
    file" returned MYR 80,375,993.99 out of the demo warehouse under a governed
    badge, and this test said that was intended.

    The founder retired the demo constraint, so grounding wins. A served turn
    that nothing grants now abstains and says what would ground it. Nothing is
    refused that was legitimately answerable - there was never a grant here to
    answer from.
    """
    body = _ask(dms_http, "total revenue in my uploaded file", "never-bound-session")

    assert body["badge"] == "abstain", body
    assert body["rows"] == []
    assert body["sql_used"] is None
    assert body["grant_kind"] == "none"
    assert "80375993" not in str(body).replace(",", "")
    assert "space" in body["answer"].lower(), body["answer"]


def test_the_ungrounded_refusal_reaches_every_served_door(dms_http) -> None:
    """/mcp/call is a door too, and it was the one most likely to be forgotten.

    Both surfaces resolve through the same `resolve_grant`, so this asserts the
    policy travelled with it rather than being set on whichever route someone
    remembered.
    """
    r = dms_http.post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {
                "question": "total revenue in my uploaded file",
                "session_id": "never-bound-mcp",
            },
        },
    )

    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["badge"] == "abstain", result
    assert result["rows"] == []
    assert "80375993" not in str(result).replace(",", "")


def test_an_expired_binding_refuses_rather_than_widening(
    dms_http, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most dangerous fallback of the three, and now it is not a fallback.

    This asserted that an expired grant widened back to the local mint and said
    so on the envelope - honest, but a wide grant honestly labelled is still
    wide, and an expiry is exactly when a caller has least reason to expect one.
    On a served door it now refuses.

    In-process callers still degrade to the mint, because `require_grounding`
    defaults to False and the corpus reads the local warehouse legitimately.
    """
    from CortexOS.execution import session_manifests

    def _expired(self, session_id, *, now=None):
        raise session_manifests.SessionExpired("session binding expired")

    monkeypatch.setattr(
        session_manifests.SessionManifestRegistry, "resolve", _expired, raising=True
    )

    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)

    assert body["badge"] == "abstain", body
    assert body["rows"] == []
    assert body["grant_kind"] == "none"
    assert "expired" in (body.get("assumptions") or "").lower()


# -- (b) the three named refusal types stay observable on the envelope --------
def test_unparseable_row_predicate_surfaces_sql_not_analyzable(dms_http) -> None:
    """End to end, nothing mocked: a grant whose predicate does not parse.

    The table IS granted, so the grounding gate passes and the turn reaches
    ``enforce_manifest``, which refuses inside ``_wrap_with_predicate``. The
    caller must receive that as a typed refusal envelope, not an HTTP 500.
    """
    dms_http.bind_session("sec01-bad-predicate", {"transactions": "NOT A PREDICATE ((("})

    body = _ask(dms_http, "what is our total revenue", "sec01-bad-predicate")

    assert body["badge"] == "refused"
    assert body["route"] == "refused"
    assert body["violations_blocked"] == ["SqlNotAnalyzable"]
    assert body["rows"] == []
    assert body.get("sql_used") is None
    _assert_no_demo_warehouse_number(body)


@pytest.mark.parametrize(
    "exc",
    [
        PathNotAllowed("reads '/etc/passwd', which is outside allowed_paths"),
        StatementNotAllowed("UPDATE is not a read"),
        SqlNotAnalyzable("could not analyse"),
    ],
    ids=["PathNotAllowed", "StatementNotAllowed", "SqlNotAnalyzable"],
)
def test_every_manifest_refusal_type_reaches_the_caller_as_an_envelope(
    dms_http, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    """Cortex#6 acceptance clause: the three refusal types stay distinguishable.

    The refusal *conditions* are covered by ``tests/test_execution/`` against
    the enforcer itself; what is asserted here is the product envelope, which
    is the half that was missing. Raising from the executor is the honest seam:
    it is exactly where ``enforce_manifest`` raises.
    """
    import CortexOS.execution.submit as submit_mod

    def _refuse(verified, sql, **kwargs):
        raise exc

    monkeypatch.setattr(submit_mod, "execute_sql", _refuse)

    body = _ask(dms_http, "what is our total revenue", WIDE_SESSION)

    assert body["badge"] == "refused"
    assert body["violations_blocked"] == [type(exc).__name__]
    assert type(exc).__name__ in body["answer"]
    assert body["rows"] == []


# -- (5) the generic abstain must name the session's real sources -------------
def test_generic_abstain_names_the_bound_sources_not_demo_questions(
    dms_http,
) -> None:
    """"average order size in my spreadsheet" hits the generic abstain, which
    used to suggest demo-warehouse questions this session would refuse - a dead
    end dressed up as help."""
    body = _ask(dms_http, "what is the average order size in my spreadsheet", NARROW_SESSION)

    assert body["badge"] == "abstain"
    assert body["rows"] == []
    assert "q3_sales_export" in body["answer"].lower()
    assert body.get("granted_sources") == ["q3_sales_export"]


# -- the second door: the legacy delayed/late bridge --------------------------
# ``answer_question`` bridges an abstain into a pre-Q2 ranked-SQL path for
# delayed/late questions, and that path resolved its own grant. Measured with a
# session bound to ``q3_sales_export`` only, before this was closed:
#
#   POST /dms/query {"question": "which shipments are late in my uploaded file"}
#   -> 200, route "sql", source_table "shipments", 5 demo-warehouse rows
#
# Same P0 as above, one door along, which is why the fix is a single resolver
# rather than a lookup added to each entry point.
@pytest.mark.parametrize(
    "question",
    [
        "which shipments are late in my uploaded file",
        "show me the most delayed shipments",
        "list late deliveries",
    ],
)
def test_legacy_delayed_bridge_cannot_escape_the_session_grant(
    dms_http, question: str
) -> None:
    body = _ask(dms_http, question, NARROW_SESSION)

    assert body.get("source_table") != "shipments"
    assert body["rows"] == []
    assert body["badge"] in ("abstain", "refused")
    assert body["grant_kind"] == "session"


def test_legacy_delayed_bridge_still_serves_a_granted_session(dms_http) -> None:
    """R-0005: the pre-Q2 demo query must keep working where it is granted."""
    dms_http.bind_session("sec01-shipments", {"shipments": "TRUE"})

    body = _ask(dms_http, "show me the most delayed shipments", "sec01-shipments")

    assert body["rows"], "a granted delayed-shipments question must still answer"
    assert body["source_table"] == "shipments"
    assert body["grant_kind"] == "session"
    assert "SHP-" in body["answer"]


def test_the_legacy_bridge_is_not_a_way_around_the_grounding_refusal(
    dms_http,
) -> None:
    """The second executor has to refuse for the same reason as the first.

    `answer_question` falls through to a legacy ranked-SQL path when the engine
    abstains on a "most delayed" question. That bridge opens its own connection,
    so a grounding refusal enforced only on the engine path would hold on one
    executor and leak on the other - which is how the original SEC-01 defect
    survived its first fix.

    This previously asserted the opposite, that an unbound demo session still
    got rows out of `shipments` under a minted grant.
    """
    body = _ask(dms_http, "show me the most delayed shipments", "never-bound-delayed")

    assert body["badge"] == "abstain", body
    assert body["rows"] == []
    assert body["source_table"] is None
    assert body["grant_kind"] == "none"


# -- (3) the MCP tool call inherits the same grant ----------------------------
def test_mcp_call_tool_runs_under_the_bound_session_grant(dms_http) -> None:
    """``/mcp/call`` reached ``answer_fn`` with no manifest and inherited the
    same wide self-issued grant. Same route, same treatment."""
    resp = dms_http.post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {
                "question": "total revenue in my uploaded file",
                "session_id": NARROW_SESSION,
            },
        },
    )

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["badge"] in ("abstain", "refused")
    assert result["grant_kind"] == "session"
    _assert_no_demo_warehouse_number(result)


def test_mcp_call_tool_still_answers_a_granted_session(dms_http) -> None:
    """R-0005 on the MCP path."""
    resp = dms_http.post(
        "/mcp/call",
        json={
            "name": "answer_engine.answer",
            "arguments": {"question": "what is our total revenue", "session_id": WIDE_SESSION},
        },
    )

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["badge"] == "governed_metric"
    assert result["rows"]
    assert DEMO_WAREHOUSE_REVENUE in result["answer"]
