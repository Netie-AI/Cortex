"""C7-05 Crew serve-on-miss gates and parent-value proof."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from cortex_contract.execution import Manifest
from fastapi.testclient import TestClient

from CortexOS.crew import insights
from CortexOS.execution import warehouse
from CortexOS.execution.manifest import VerifiedManifest
from CortexOS.execution.pool import PoolConfig, reset_read_pool_for_tests
from CortexOS.execution.session_manifests import (
    get_session_registry,
    reset_session_registry_for_tests,
)

SESSION = "c7-05-l2-serve"
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
PHASE1B_FIXTURE = (
    Path(__file__).parent / "fixtures" / "c7_05_phase1b_l2_cases.json"
)
PHASE1B = json.loads(PHASE1B_FIXTURE.read_text(encoding="utf-8"))
PHASE1B_CASES = PHASE1B["cases"]


class _MissBridge:
    session_id = SESSION
    space_id = None

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def ask(self, question: str) -> dict[str, Any]:
        self.asked.append(question)
        return {
            "ok": True,
            "answer": "Engine abstained. No number invented.",
            "badge": "abstain",
            "layer": "l1",
            "metric_id": "sku_count",
            "audit_id": None,
            "row_count": 0,
            "rows": [],
            "sources": ["inventory"],
            "sql_used": None,
        }


def _bind() -> None:
    reset_session_registry_for_tests()
    reset_read_pool_for_tests(PoolConfig("default", 4, 5.0, 30.0))
    now = datetime.now(timezone.utc)
    get_session_registry().bind(
        VerifiedManifest(
            manifest=Manifest(
                session_id=SESSION,
                org_id="acme",
                pool_id="default",
                issuer_key_id="int-1",
                allowed_paths=["/data/pool/acme/**"],
                row_predicates={
                    "inventory": "1=1",
                    "locations": "1=1",
                    "shipments": "1=1",
                    "suppliers": "1=1",
                    "transactions": "1=1",
                },
                issued_at=now.isoformat(),
                expires_at=(now + timedelta(minutes=5)).isoformat(),
                signature="not-checked-here",
            ),
            issuer_kid="int-1",
            verified_at=now,
        )
    )


def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    from CortexOS.crew import freeroute

    monkeypatch.setattr(
        freeroute,
        "arming",
        lambda: {
            "ok": True,
            "armed": True,
            "vault_live": True,
            "local_keys": True,
            "cloud_keys": False,
            "detail": "test",
            "live_5000_ci": False,
        },
    )


def _complete(sql: str = SQL):
    async def fake(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, prompt, kwargs
        return {
            "ok": True,
            "text": sql,
            "identity": "cortex:crew:generative-ask",
            "route": {"label": "requested-only", "model": "requested-only"},
            "stamp": {
                "call_id": f"call-{purpose}",
                "task": "crew-insights-sql",
                "requested": "requested-only",
                "served_provider": "vault-provider",
                "served_model": "vault-model",
                "served_local": True,
                "served_reason": "",
            },
        }

    return fake


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_session_registry_for_tests()
    yield
    reset_session_registry_for_tests()


@pytest.fixture(scope="module")
def phase1b_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the checked-in minimal fixture; absence or malformed SQL must fail."""
    assert len(PHASE1B_CASES) == 15
    assert PHASE1B["source"]["evidence_scope"].startswith(
        "test-only, writer-authored UNIT evidence"
    )
    assert all(row["sql_origin"] == "hand-written, same shape" for row in PHASE1B_CASES)
    path = tmp_path_factory.mktemp("c7-05-phase1b") / "phase1b.duckdb"
    con = warehouse.get_connection(path)
    try:
        for statement in PHASE1B["setup_sql"]:
            con.execute(statement)
    finally:
        con.close()
    return path


def _row_multiset(rows: list[dict[str, Any]]) -> list[str]:
    return sorted(
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        for row in rows
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    PHASE1B_CASES,
    ids=[row["id"] for row in PHASE1B_CASES],
)
async def test_phase1b_wrong_shape_replays_reach_real_l2_and_never_serve_wrong(
    monkeypatch: pytest.MonkeyPatch,
    phase1b_db: Path,
    case: dict[str, Any],
) -> None:
    """Replay all published Phase 1b stand-in shapes through the real miss path."""
    from CortexOS.execution import submit

    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(submit, "DEFAULT_DB", phase1b_db)

    calls = 0
    try:
        l2_serve = importlib.import_module("CortexOS.crew.l2_serve")
    except ModuleNotFoundError:
        # Parent 279cbd85 has no Insights L2 call site. Continue through its
        # real path so the assertion below fails with calls=0, not ImportError.
        l2_serve = None
    if l2_serve is not None:
        real_serve = l2_serve.serve_on_miss

        def counted_serve(**kwargs: Any) -> dict[str, Any] | None:
            nonlocal calls
            calls += 1
            return real_serve(**kwargs)

        monkeypatch.setattr(l2_serve, "serve_on_miss", counted_serve)
    body = await insights.run_insights(
        case["question"],
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(case["sql"]),
    )

    assert calls == 1, "the real L2 miss call must run; a badge is not proof"
    assert body["answer_step"] == case["answer_step"], body["answer"]
    if body["status"] == "ABSTAIN":
        assert body["values"] == []
        assert body["answer_step"] in {
            "manifest_check",
            "explain",
            "plausibility",
            "plan_shape",
        }
    else:
        assert body["status"] == "CERTIFIED"
        assert body["layer"] == "generated"
        assert body["badge"] == "L2_VALIDATED"
        assert _row_multiset(body["values"]) == _row_multiset(case["oracle"])


def test_l2_defaults_off_and_no_checked_in_setting_enables_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from CortexOS.crew import l2_serve
    from CortexOS.dms import answer_engine, l2_generation

    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    assert l2_serve.enabled() is False
    assert l2_generation._env_on("DMS_L2_ENABLED") is False
    assert l2_generation.attempt_l2("default-off direct check") is None

    calls: list[dict[str, Any]] = []
    real_attempt = l2_generation.attempt_l2

    def observed_attempt(question: str, **kwargs: Any) -> Any:
        calls.append(dict(kwargs))
        return real_attempt(question, **kwargs)

    monkeypatch.setattr(l2_generation, "attempt_l2", observed_attempt)
    answer = answer_engine.answer(
        "Correlate supplier ESG scores with weather anomalies",
        session_id="c7-05-answer-engine-default-off",
    )
    assert calls == [{"verified": None, "promote": False}]
    assert answer.get("layer") != "generated"
    assert answer.get("badge") != "L2_VALIDATED"

    assignment = re.compile(
        r"""^\s*(?:-\s*)?(?:export\s+)?["']?DMS_L2_ENABLED["']?\s*"""
        r"""(?:=|:)\s*["']?(?:1|true|yes)["']?\s*(?:#.*)?$""",
        re.IGNORECASE,
    )
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z"], text=True
    ).split("\0")
    enabled_by: list[str] = []
    for relative in tracked:
        if not relative:
            continue
        path = Path(relative)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        if any(assignment.match(line) for line in lines):
            enabled_by.append(relative)
    assert enabled_by == [], f"checked-in configuration enables L2: {enabled_by}"


def test_flag_unset_http_insights_envelope_matches_parent_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Pin the complete POST envelope measured on parent 279cbd85."""
    from CortexOS.crew.engine_bridge import LocalEngineBridge
    from packs.dms.security.rate_limit import reset_limiter

    async def fake_ask(self, question: str) -> dict[str, Any]:  # noqa: ARG001
        return {
            "ok": True,
            "answer": "There are 12 skus.",
            "badge": "governed_metric",
            "layer": "governed_metric",
            "metric_id": "sku_count",
            "audit_id": "audit-sku",
            "row_count": 1,
            "rows": [{"sku_count": 12}],
            "sources": ["inventory"],
            "sql_used": SQL,
            "truncated": False,
        }

    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv(
        "CORTEX_FREEROUTE_SCOREBOARD", "/tmp/c7-05-http-flag-off-v1.db"
    )
    monkeypatch.setattr(LocalEngineBridge, "ask", fake_ask)
    reset_limiter(per_minute=240)

    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/v1/insights",
            json={"intent": "how many skus", "ask": True, "generate": False},
        )
    assert response.status_code == 200, response.text
    raw = json.dumps(
        response.json(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "378442dd9b839fe7a2be3eb1b40992ef9114a80ad9c81b9d88154345cc59e4db"
    )


@pytest.mark.asyncio
async def test_flag_off_is_byte_identical_for_same_main_path(monkeypatch) -> None:
    _armed(monkeypatch)
    monkeypatch.delenv("DMS_L2_ENABLED", raising=False)
    first = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    monkeypatch.setenv("DMS_L2_ENABLED", "0")
    second = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["status"] == "ABSTAIN"
    assert first["values"] == []


@pytest.mark.asyncio
async def test_parent_wrong_value_now_serves_gated_value_and_real_stamp(
    monkeypatch,
) -> None:
    """On 279cbd85 this reaches the old miss and returns values=[] (wrong value)."""
    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    calls: list[str] = []

    def execute_sql(verified, sql, *, explain_gate=None):  # noqa: ANN001
        del verified
        assert sql == SQL
        assert explain_gate is True
        calls.extend(["manifest_check", "explain"])
        return [{"sku_count": 4}], 0.0, 0.1

    def plausible(question, sql, rows, **kwargs):  # noqa: ANN001
        del question, kwargs
        assert sql == SQL
        assert rows == [{"sku_count": 4}]
        calls.append("plausibility")
        from CortexOS.dms.l2_plausibility import PlausibilityResult

        return PlausibilityResult(ok=True)

    monkeypatch.setattr("CortexOS.execution.submit.execute_sql", execute_sql)
    monkeypatch.setattr("CortexOS.dms.l2_plausibility.assess_plausibility", plausible)
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )

    assert calls == ["manifest_check", "explain", "plausibility"], (
        body["answer"],
        (body.get("generative") or {}).get("sql"),
    )
    assert body["values"] == [{"sku_count": 4}]
    assert body["status"] == "CERTIFIED"
    assert body["layer"] == "generated"
    assert body["badge"] == "L2_VALIDATED"
    assert body["answer_step"] == "l2_plausibility"
    assert "sku_count=4" in body["answer"]
    assert body["model_called"] is True
    assert body["served_provider"] == "vault-provider"
    assert body["served_model"] == "vault-model"
    assert body["served_local"] is True
    assert body["served_provider"] != "requested-only"


@pytest.mark.asyncio
async def test_wrong_columns_abstain_at_named_plan_shape(monkeypatch) -> None:
    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda verified, sql, *, explain_gate=None: ([{"extra": 4}], 0.0, 0.1),
    )
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["answer_step"] == "plan_shape", body["answer"]
    assert "expected columns=['sku_count']" in body["answer"]


@pytest.mark.asyncio
async def test_certified_measure_mismatch_abstains_before_execute(monkeypatch) -> None:
    _armed(monkeypatch)
    _bind()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    executed: list[str] = []
    monkeypatch.setattr(
        "CortexOS.execution.submit.execute_sql",
        lambda *args, **kwargs: executed.append("called"),
    )
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete("SELECT COUNT(*) AS sku_count FROM inventory"),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert executed == []
    assert body["status"] == "ABSTAIN"
    assert body["answer_step"] == "l2_plan"
    assert "certified_measure_not_used:cq_sku_count" in body["answer"]


@pytest.mark.asyncio
async def test_missing_real_route_stamp_abstains_by_name(monkeypatch) -> None:
    _armed(monkeypatch)
    monkeypatch.setenv("DMS_L2_ENABLED", "1")

    async def no_stamp(messages=None, *, purpose="", prompt="", **kwargs):  # noqa: ANN001
        del messages, purpose, prompt, kwargs
        return {"ok": True, "text": SQL}

    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=no_stamp,
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["answer_step"] == "route_stamp"
    assert body["model_called"] is True
    assert "RouteStamp call_id" in body["answer"]


@pytest.mark.asyncio
async def test_unbound_manifest_abstains_by_name(monkeypatch) -> None:
    _armed(monkeypatch)
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    body = await insights.run_insights(
        "how many skus",
        bridge=_MissBridge(),
        ask=True,
        generate=True,
        complete=_complete(),
        query_plan={"measure": "sku_count", "group_by": [], "filters": []},
    )
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["answer_step"] == "manifest_check"
    assert "SessionUnbound" in body["answer"]


class _HeldoutBridge:
    session_id = SESSION
    space_id = None

    async def ask(self, question: str) -> dict[str, Any]:
        from CortexOS.dms.answer_engine import answer

        return answer(question)


async def _score_insights_heldout(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enable_l2: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Score the frozen corpus through run_insights and its real L2 call site."""
    from bench.accuracy import _ensure_db_loaded
    from bench.heldout import load_heldout, score_envelope, summarize
    from CortexOS.dms.answer_engine import clear_session

    _ensure_db_loaded()
    _armed(monkeypatch)
    _bind()
    # Keep the fingerprint's path environmental field identical across the
    # detached parent worktree and this checkout for byte-for-byte comparison.
    monkeypatch.setenv(
        "CORTEX_FREEROUTE_SCOREBOARD", "/tmp/c7-05-fixed-routes.db"
    )
    monkeypatch.setenv("DMS_L2_ENABLED", "1" if enable_l2 else "0")
    clear_session()

    # This fixed, non-oracle probe makes every item reach the real gate stack
    # without pretending that this environment has a live FreeRoute model.
    complete = _complete("SELECT 1 AS probe FROM inventory")
    items = load_heldout()
    envelopes: dict[str, dict[str, Any]] = {}
    scored = []
    for item in items:
        envelope = await insights.run_insights(
            item.question,
            bridge=_HeldoutBridge(),
            ask=True,
            generate=True,
            complete=complete,
        )
        envelopes[item.id] = envelope
        scored.append(score_envelope(item, envelope))

    report = summarize(scored)
    raw = json.dumps(
        envelopes, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    report["envelopes_sha256"] = hashlib.sha256(raw).hexdigest()
    report["l2_named_steps"] = sum(
        bool(envelope.get("answer_step")) for envelope in envelopes.values()
    )
    return report, envelopes


@pytest.mark.asyncio
async def test_c7_04_corpus_real_insights_path_has_no_new_wrong(monkeypatch) -> None:
    off, off_envelopes = await _score_insights_heldout(monkeypatch, enable_l2=False)
    on, on_envelopes = await _score_insights_heldout(monkeypatch, enable_l2=True)

    for report in (off, on):
        assert report["g_abs_recall"] == 1.0
        assert report["incorrect_rate"] == 0.0
        assert report["g_env_violations"] == 0
        assert report["totals"]["incorrect"] == 0

    assert off["l2_named_steps"] == 0
    assert on["l2_named_steps"] > 0
    assert (
        off["envelopes_sha256"]
        == "f5b041bd6df0ef16852d9175a8a4567713f48b48579903a261f04f25d434d845"
    )
    assert off_envelopes["ma_workday_payroll_cube"]["values"] == []
    assert on_envelopes["ma_workday_payroll_cube"]["values"] == []
    assert on_envelopes["ma_workday_payroll_cube"]["answer_step"] == "plan_shape"


def test_c7_04_preexisting_misroute_value_unchanged_pending_288(
    monkeypatch,
) -> None:
    """Pin the parent's wrong value until Cortex #288 replaces it with abstention."""
    from bench.accuracy import _ensure_db_loaded
    from bench.heldout import load_heldout, score_envelope
    from CortexOS.dms.answer_engine import answer, clear_session

    item = next(row for row in load_heldout() if row.id == "ma_workday_payroll_cube")
    _ensure_db_loaded()
    clear_session()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    envelope = answer(item.question)
    rows = envelope["rows"]
    digest = hashlib.sha256(
        json.dumps(
            rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
    ).hexdigest()

    scored = score_envelope(item, envelope)
    assert scored.outcome == "incorrect"
    assert len(rows) == 1000
    assert rows[0] == {"quantity_kg": 1.0, "sku": "SKU-00168"}
    assert rows[-1] == {"quantity_kg": 20.0, "sku": "SKU-00186"}
    assert digest == "5837092823105b19163897d7063ecdd7ebe08c536dcdcd3b86a8f63dc106caaa"
