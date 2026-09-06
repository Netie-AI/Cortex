"""Assign-task router: unarmed refuse, Crew live with mocks, no second vault."""

from __future__ import annotations

import json
from typing import Any

import pytest

from CortexOS.crew import assign_router, openvault
from CortexOS.crew.assign_router import AIRGPT_PORT, HUMAN_STOP, LAW, catalog, dispatch, map_public


def _vault_row(label: str, env_key: str, *, enabled: bool = True) -> dict[str, Any]:
    return {
        "id": f"k-{label}",
        "label": env_key,
        "provider": label,
        "env_key": env_key,
        "enabled": enabled,
        "crew_label": label,
    }


def _live_vault(monkeypatch: pytest.MonkeyPatch, rows: dict[str, dict]) -> None:
    monkeypatch.setenv("CREW_OPENVAULT", "1")
    monkeypatch.setattr(
        openvault, "healthz", lambda timeout=1.5: {"ok": True, "url": "http://127.0.0.1:5000"}
    )
    monkeypatch.setattr(openvault, "require_live", lambda timeout=1.5: {"ok": True})
    monkeypatch.setattr(openvault, "vault_sources", lambda: rows)


def test_map_public_is_display_only_and_lists_every_destination() -> None:
    body = map_public()
    assert body["control"] == "display-only"
    assert body["execute"] == "POST /crew/assign"
    ids = [d["id"] for d in body["destinations"]]
    assert ids == [
        "crew",
        "claude_code",
        "claude_app",
        "cursor_cloud",
        "local_model",
        "airgpt",
    ]
    by_id = {d["id"]: d for d in body["destinations"]}
    assert by_id["crew"]["status"] == "live"
    assert by_id["local_model"]["status"] == "live"
    assert by_id["claude_code"]["status"] == "preview"
    assert by_id["claude_app"]["status"] == "preview"
    assert by_id["cursor_cloud"]["status"] == "preview"
    assert by_id["airgpt"]["status"] == "preview"
    blob = json.dumps(body)
    assert "sk-" not in blob
    assert "API_KEY" not in blob
    assert "Control does not POST assign" in body["law"]


def test_catalog_marks_unarmed_local_and_airgpt(crew_env) -> None:
    body = catalog(live=True)
    by_id = {d["id"]: d for d in body["destinations"]}
    assert by_id["crew"]["armed"] is True  # CREW_MODEL=test/fake-model
    assert by_id["local_model"]["armed"] is False
    assert by_id["claude_app"]["armed"] is False
    assert by_id["cursor_cloud"]["armed"] is False
    assert by_id["airgpt"]["armed"] is False
    assert by_id["airgpt"]["adapter"].endswith(str(AIRGPT_PORT))


@pytest.mark.asyncio
async def test_unarmed_destinations_refuse_without_fallback(rig) -> None:
    space = rig.store.create_space("HQ")
    local = await dispatch(
        rig.runtime,
        {"destination": "local_model", "brief": "run locally", "space_id": space["id"]},
    )
    assert local["ok"] is False
    assert local["status_code"] == 409
    assert "no silent fallback" in local["detail"]
    cursor = await dispatch(
        rig.runtime,
        {"destination": "cursor_cloud", "brief": "open a cloud agent"},
    )
    assert cursor["ok"] is False
    assert "unarmed" in cursor["detail"]
    app = await dispatch(
        rig.runtime,
        {"destination": "claude_app", "brief": "paste this in Claude"},
    )
    assert app["ok"] is False
    air = await dispatch(rig.runtime, {"destination": "airgpt", "brief": "thin client"})
    assert air["ok"] is False
    assert "CREW_LIVE_PROBES=0" in air["detail"] or "unarmed" in air["detail"]


@pytest.mark.asyncio
async def test_crew_unarmed_refuses_when_model_unset(rig, monkeypatch) -> None:
    monkeypatch.delenv("CREW_MODEL", raising=False)
    space = rig.store.create_space("HQ")
    out = await dispatch(
        rig.runtime,
        {"destination": "crew", "space_id": space["id"], "brief": "watch the belt", "name": "Scout"},
    )
    assert out["ok"] is False
    assert out["status_code"] == 409
    assert "no silent fallback" in out["detail"]


@pytest.mark.asyncio
async def test_crew_destination_spawns_with_mocks_and_no_second_vault(rig) -> None:
    data_dir = rig.settings.data_dir
    space = rig.store.create_space("HQ")
    out = await dispatch(
        rig.runtime,
        {
            "destination": "crew",
            "space_id": space["id"],
            "brief": "hold the line",
            "name": "Scout",
        },
    )
    assert out["ok"] is True
    assert out["destination"] == "crew"
    assert out["status"] == "live"
    assert out["executed"] is True
    assert out["agent"]["name"] == "Scout"
    scout = rig.store.get_agent_by_name(space["id"], "Scout")
    assert scout is not None
    assert scout["goal_text"] == "hold the line"
    assert not (data_dir / "keys.json").is_file()
    assert not (data_dir / "vault.json").is_file()
    assert not (data_dir / "openvault.json").is_file()
    blob = json.dumps(out)
    assert "sk-" not in blob
    assert "gsk_" not in blob
    assert LAW in out["law"]


@pytest.mark.asyncio
async def test_claude_code_declares_lane_and_refuses_live_ssh(
    rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {"anthropic": _vault_row("anthropic", "ANTHROPIC_API_KEY")},
    )
    preview = await dispatch(
        rig.runtime,
        {"destination": "claude_code", "brief": "fix the gate", "lane": "cli"},
    )
    assert preview["ok"] is True
    assert preview["executed"] is False
    assert preview["status"] == "preview"
    assert preview["handoff"]["ssh"] is False
    assert preview["handoff"]["lane"] == "cli"
    ssh = await dispatch(
        rig.runtime,
        {"destination": "claude_code", "brief": "ssh in", "live_ssh": True},
    )
    assert ssh["ok"] is False
    assert HUMAN_STOP in ssh["detail"]
    assert "live SSH" in ssh["detail"]


@pytest.mark.asyncio
async def test_cursor_cloud_handoff_and_human_stop_execute(
    rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_vault(
        monkeypatch,
        {"cursor": _vault_row("cursor", "CURSOR_API_KEY")},
    )
    handoff = await dispatch(
        rig.runtime,
        {"destination": "cursor_cloud", "brief": "review the PR"},
    )
    assert handoff["ok"] is True
    assert handoff["executed"] is False
    assert handoff["handoff"]["kind"] == "cursor_cloud_agent"
    assert handoff["handoff"]["execute"] is False
    spend = await dispatch(
        rig.runtime,
        {"destination": "cursor_cloud", "brief": "review the PR", "execute": True},
    )
    assert spend["ok"] is False
    assert HUMAN_STOP in spend["detail"]
    assert "cloud agent" in spend["detail"].lower()


@pytest.mark.asyncio
async def test_local_model_live_when_loopback_armed(
    rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("CREW_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    out = await dispatch(
        rig.runtime,
        {"destination": "local_model", "brief": "summarize notes"},
    )
    assert out["ok"] is True
    assert out["destination"] == "local_model"
    assert out["status"] == "live"
    assert out["executed"] is False
    assert out["chosen"]["label"] == "openai-compatible"


@pytest.mark.asyncio
async def test_airgpt_preview_when_probe_ok(rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        assign_router,
        "airgpt_probe",
        lambda: {"ok": True, "url": "http://127.0.0.1:8765", "detail": "HTTP 200"},
    )
    out = await dispatch(rig.runtime, {"destination": "airgpt", "brief": "ask AirGPT"})
    assert out["ok"] is True
    assert out["status"] == "preview"
    assert out["executed"] is False
    assert out["handoff"]["port"] == 8765
    assert "sk-" not in json.dumps(out)
    assert out["handoff"]["note"].startswith("Thin client")


@pytest.mark.asyncio
async def test_unknown_destination_is_400(rig) -> None:
    out = await dispatch(rig.runtime, {"destination": "langgraph", "brief": "no"})
    assert out["ok"] is False
    assert out["status_code"] == 400
    assert "unknown destination" in out["detail"]
