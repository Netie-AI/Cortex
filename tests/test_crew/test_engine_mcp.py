from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.crew.engine_bridge import EngineBridge
from CortexOS.crew.mcp_client import DEFAULT_SPECS, MCPManager, load_specs
from CortexOS.crew.policy import ALLOW, CONFIRM, decide

httpx = pytest.importorskip("httpx")


@pytest.mark.asyncio
async def test_offline_engine_is_a_visible_envelope() -> None:
    bridge = EngineBridge("http://127.0.0.1:9", "demo", timeout=1.0)
    envelope = await bridge.ask("how many units?")
    assert envelope["ok"] is False
    assert envelope["badge"] == "engine_offline"
    assert "unreachable" in envelope["answer"].lower()


@pytest.mark.asyncio
async def test_engine_success_keeps_badge_and_audit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/dms/query"
        return httpx.Response(
            200,
            json={
                "answer": "42 units on hand",
                "badge": "governed",
                "audit_id": "audit-9",
                "route": "metric",
                "row_count": 1,
            },
        )

    bridge = EngineBridge(
        "http://engine.test",
        "demo",
        transport=httpx.MockTransport(handler),
    )
    envelope = await bridge.ask("units?")
    assert envelope["ok"] is True
    assert envelope["badge"] == "governed"
    assert envelope["audit_id"] == "audit-9"
    assert envelope["answer"] == "42 units on hand"


def test_default_mcp_catalog_includes_uacc_and_starts_disarmed(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    specs = load_specs(path)
    names = [s.name for s in specs]
    assert names == ["uacc", "windows-mcp", "computer-control-mcp"]
    assert all(s.armed is False for s in specs)
    assert {s["name"] for s in DEFAULT_SPECS} == set(names)
    mgr = MCPManager(path, master_on=False)
    status = mgr.status()
    assert all("CORTEX_COMPUTER_CONTROL" in row["status"] for row in status)


def test_uacc_screenshot_is_read_only_when_armed() -> None:
    decision, _ = decide("screenshot", server="uacc", armed=True, master_on=True)
    assert decision == ALLOW
    decision, _ = decide("click", server="uacc", armed=True, master_on=True)
    assert decision == CONFIRM
    decision, reason = decide("click", server="uacc", armed=True, master_on=False)
    assert decision != ALLOW
    assert "CORTEX_COMPUTER_CONTROL" in reason
