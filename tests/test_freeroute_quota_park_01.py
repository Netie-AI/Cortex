"""FREEROUTE-QUOTA-PARK-01 (F136a, Refs #211): a quota-spent provider is parked.

When groq's provider-wide daily token quota is spent, OpenVault stops walking
every groq hop and reports each one with ``health="quota_exhausted"``, the
circuit closed and no ``park_until`` (OpenVault vault/fallback.py
FallbackManager.status; quota.py quota_view). On base 304bf843 ``_hop_parked``
read only ``circuit`` and ``park_until``, so Cortex kept asking for groq ids
such as ``openai/gpt-oss-120b``. OpenVault then fell through to nvidia and
served its strongest catalog id instead: a slow call and a pin mismatch.

The scripted OpenVault below is tests/freeroute_fake.py with the chat route
replaced by OpenVault's non-strict walk: a hop that is circuit-open, parked, or
quota-exhausted is skipped (fallback.py, the ``quota_blocks`` check), and the
first hop left serves the requested id when it catalogues it, otherwise its own
first catalog id. Catalog ids are copied from OpenVault vault/providers.py.
No socket is opened; the conftest guard fails any live :5000 connect.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from CortexOS.crew import insights
from CortexOS.integrations import freeroute as fr
from CortexOS.integrations import openvault_client
from packs.dms.security.rate_limit import reset_limiter
from tests.test_crew.test_insights import ScriptedBridge, _certified_engine

GROQ = ("openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b")
NVIDIA = (
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-4-340b-instruct",
    "nvidia/llama-3.1-nemotron-ultra-253b-v1",
)
SQL = "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
MSGS = [{"role": "user", "content": "how many skus"}]
BEARER = "ov_quotaparkcaller"


def _health_hop(fake: Any, provider: str, priority: int, health: str) -> dict[str, Any]:
    row = dict(fake.hop(provider, priority))
    row["health"] = health
    if health == "quota_exhausted":
        row["quota_reset_at"] = "2026-11-07T00:00:00Z"
    return row


def _walkable(row: dict[str, Any]) -> bool:
    """OpenVault's walk: skip circuit-open, parked and quota-spent hops."""
    return not (
        row.get("circuit") == "open"
        or row.get("park_until")
        or row.get("health") == "quota_exhausted"
    )


@pytest.fixture()
def quota_spent_openvault(armed_openvault, monkeypatch):
    """groq's daily quota is spent (circuit closed, no park_until); nvidia is healthy."""
    fake = armed_openvault
    fake.hops = [
        _health_hop(fake, "groq", 10, "quota_exhausted"),
        _health_hop(fake, "nvidia", 20, "ok"),
    ]
    fake.catalogue = {"groq": list(GROQ), "nvidia": list(NVIDIA)}
    fake.asked = []

    def transport(method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        if str(path).split("?", 1)[0] != "/v1/chat/completions":
            return fake(method, path, **kwargs)
        body = dict(kwargs.get("body") or {})
        fake.calls.append({"method": method, "path": path, "body": body, "base": kwargs.get("base")})
        requested = str(body.get("model") or "")
        served_provider, served = "", ""
        for row in fake.hops:
            pool = fake.catalogue.get(str(row.get("provider")), [])
            if not pool or not _walkable(row):
                continue
            served_provider = str(row["provider"])
            served = requested if requested in pool else pool[0]
            break
        fake.asked.append((requested, served, body.get("strict") is True))
        if not served:
            return 503, {"error": {"message": "no hop", "type": "openvault_no_keys"}}
        return 200, {
            "model": served,
            "choices": [{"message": {"role": "assistant", "content": SQL}}],
            "served_provider": served_provider,
            "served_model": served,
            "served_local": False,
        }

    monkeypatch.setattr(openvault_client, "request_json", transport)
    return fake


# -- _hop_parked -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "parked"),
    [
        ({"circuit": "closed", "park_until": None, "health": "quota_exhausted"}, True),
        ({"circuit": "closed", "health": "quota_exhausted"}, True),
        ({"circuit": "open", "park_until": None, "health": "ok"}, True),
        ({"circuit": "closed", "park_until": 1893456000.0, "health": "ok"}, True),
        ({"circuit": "closed", "park_until": None, "health": "ok"}, False),
        ({"circuit": "closed", "park_until": None, "health": "unknown"}, False),
        ({"circuit": "closed", "park_until": None}, False),
    ],
)
def test_hop_parked_reads_quota_exhausted_health(row: dict[str, Any], parked: bool) -> None:
    assert fr._hop_parked(row) is parked


# -- candidates / pick -------------------------------------------------------------


def test_quota_exhausted_provider_gives_no_candidate(quota_spent_openvault) -> None:
    """The defect case: groq circuit closed, no park_until, health quota_exhausted."""
    groq_row = quota_spent_openvault.hops[0]
    assert groq_row["circuit"] == "closed" and groq_row["park_until"] is None
    arm = fr.arming(fresh=True)
    assert arm.armed is True
    by_provider = {h["provider"]: h for h in arm.hops_public}
    assert by_provider["groq"]["parked"] is True
    assert by_provider["nvidia"]["parked"] is False
    models, source = fr.candidates(arm)
    assert source == "live hops x OpenVault catalogue"
    assert not set(models) & set(GROQ), models
    assert models == NVIDIA[: fr.PER_PROVIDER]
    picked = fr.pick("crew-insights-sql", arm)
    assert picked.requested not in GROQ
    assert picked.requested == NVIDIA[0]


def test_complete_is_served_by_the_requested_model(quota_spent_openvault, monkeypatch) -> None:
    """No pin mismatch: what Cortex asks for is what OpenVault serves."""
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    out = fr.complete("crew-insights-sql", list(MSGS))
    assert out.ok is True
    assert out.text == SQL
    assert out.stamp.requested == NVIDIA[0]
    assert out.stamp.served == out.stamp.requested
    assert out.stamp.honored is True
    assert out.stamp.served_provider == "nvidia"
    assert [(r, s) for r, s, _ in quota_spent_openvault.asked] == [(NVIDIA[0], NVIDIA[0])]
    # Fix (2) is not in this PR: the strict default stays off.
    assert "strict" not in quota_spent_openvault.chat_calls[-1]["body"]


# -- Insights generate (the customer answer) ---------------------------------------


@pytest.mark.asyncio
async def test_insights_generate_never_asks_for_a_quota_spent_id(
    quota_spent_openvault, monkeypatch
) -> None:
    """Rendered answer, rows, and the generate stamp: served == requested, no groq id."""
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    bridge = ScriptedBridge(_certified_engine())
    env = await insights.run_insights("how many skus", bridge=bridge, ask=True, generate=True)

    assert env["status"] == "CERTIFIED", env.get("refuse_reason") or env.get("answer")
    assert "12" in (env.get("answer") or "")
    assert env["values"] == [{"sku_count": 12}]
    stamp = env["generative"]["stamp"]
    assert stamp["requested"] not in GROQ
    assert stamp["served"] == stamp["requested"]
    assert stamp["honored"] is True
    asked = quota_spent_openvault.asked
    assert asked, "generate made no model call"
    assert all(requested not in GROQ for requested, _, _ in asked), asked
    assert all(served == requested for requested, served, _ in asked), asked


def test_http_insights_generate_served_model_equals_requested(
    quota_spent_openvault, monkeypatch, tmp_path
) -> None:
    """POST /v1/insights, the route DMS calls. The envelope names the model that served."""
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv(fr.MODELS_ENV, raising=False)
    monkeypatch.delenv(fr.STRICT_ENV, raising=False)
    reset_limiter(per_minute=240)
    from CortexOS.api.app import create_app

    quota_spent_openvault.identities[BEARER] = "caller-quota-park"
    # No ``with``: the app lifespan would refresh JWKS from OpenVault's own port.
    client = TestClient(create_app(), headers={"X-API-Key": "dms-demo-admin-key"})
    res = client.post(
        "/v1/insights",
        json={"intent": "how many skus", "ask": False, "generate": True},
        headers={"Authorization": f"Bearer {BEARER}"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["model_called"] is True
    # Generate-only never certifies a figure: the answer abstains with no values.
    assert body["status"] == "ABSTAIN"
    assert body["values"] == []
    assert body["served_provider"] == "nvidia"
    assert body["served_model"] not in GROQ
    stamp = body["generative"]["stamp"]
    assert stamp["requested"] == stamp["served"] == body["served_model"]
    asked = quota_spent_openvault.asked
    assert asked and all(requested not in GROQ for requested, _, _ in asked), asked
    assert all(served == requested for requested, served, _ in asked), asked
