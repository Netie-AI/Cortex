"""RUN-AUTH-01: model-reaching run routes fail closed before any adapter call.

Spend is ``require_spend`` (``require_role("steward")``). A viewer, including
actor ``api_viewer``, is a named 403. Unknown and ambiguous roles do not spend.
``DMS_AUTH_DISABLED`` is the existing test/dev short-circuit to admin.

These refusal cases fail on 0faede64: ``POST /run`` and ``POST /api/workflows/run``
have no auth dependency, and ``/api/engine/run`` plus ``/cortex/constructor/run``
allow a viewer to reach the runner.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from CortexOS.execution.model_router import BIG_API_PLACEHOLDER
from packs.dms.security.api_auth import Caller, get_caller, parse_api_keys

KEYS = "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test"
VIEWER_REFUSAL = "Requires role 'steward' or higher (caller='viewer' actor='api_viewer')"

LLM_DAG = {
    "version": "2.0",
    "intent_hash": "h",
    "entry_node_id": "j1",
    "output_node_id": "e1",
    "nodes": [
        {
            "id": "j1",
            "kind": "llm_judged",
            "default_tier": "T3",
            "max_tier": "T3",
            "provider": BIG_API_PLACEHOLDER,
            "prompt": "spend probe",
            "max_tokens": 8,
            "cost_ceiling_myr": 10.0,
            "inputs": [],
        },
        {"id": "e1", "kind": "EMIT", "tier": 0, "inputs": ["j1"]},
    ],
}
RUN_BODY = {"dag": LLM_DAG, "run_id": "spend_probe", "context": {}}
ENGINE_BODY = {
    "prompt": "spend probe",
    "architecture_preset": "dag",
    "dag": LLM_DAG,
    "session_id": "spend-probe",
}
WF_BODY = {
    "template_id": "document_qa",
    "goal": "page one",
    "variables": {"question": "page one"},
}
RESUME_BODY = {"task_id": "wf_does_not_exist"}
CTOR_BODY = {
    "nodes": [
        {"id": "a1", "kind": "agent", "note": "say ok"},
        {"id": "out", "kind": "app"},
    ],
    "edges": [{"from": "a1", "to": "out"}],
}

# (label, path, body, mounted only when the active pack is dms)
SPEND_ROUTES = (
    ("POST /run", "/run", RUN_BODY, False),
    ("POST /api/workflows/run", "/api/workflows/run", WF_BODY, False),
    ("POST /api/workflows/resume", "/api/workflows/resume", RESUME_BODY, False),
    ("POST /api/engine/run", "/api/engine/run", ENGINE_BODY, False),
    ("POST /cortex/constructor/run", "/cortex/constructor/run", CTOR_BODY, True),
)

PACKS = (
    pytest.param(None, None, "ruma", id="unset"),
    pytest.param("ruma", None, "ruma", id="ruma"),
    pytest.param("crm", None, "crm", id="crm"),
    pytest.param("dms", None, "dms", id="dms"),
    pytest.param("nosuch", None, "nosuch", id="nosuch"),
    pytest.param("ruma", "dms", "dms", id="NETIE_PACK=dms"),
)


def _calls(counts: dict[str, int]) -> int:
    return counts["litellm"] + counts["freeroute"]


def _snap(counts: dict[str, int]) -> dict[str, int]:
    return dict(counts)


def _boot(monkeypatch: pytest.MonkeyPatch, pack: str | None, netie_pack: str | None, *, keys: str = KEYS):
    import CortexOS.config as cfg

    monkeypatch.setattr(cfg, "get_config_path", lambda: Path("/tmp/no-such-netie-config.toml"))
    if pack is None:
        monkeypatch.delenv("PACK", raising=False)
    else:
        monkeypatch.setenv("PACK", pack)
    if netie_pack is None:
        monkeypatch.delenv("NETIE_PACK", raising=False)
    else:
        monkeypatch.setenv("NETIE_PACK", netie_pack)
    monkeypatch.setenv("DMS_API_KEYS", keys)
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    for name in ("CortexOS.config", "netie.config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "_cached_config"):
            mod._cached_config = None
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(per_minute=1000)
    from CortexOS.api.app import create_app

    return create_app()


@pytest.fixture
def sentinels(monkeypatch: pytest.MonkeyPatch):
    """Replace litellm.acompletion and FreeRoute, and block sockets."""
    import litellm

    from CortexOS.integrations import freeroute

    counts = {"litellm": 0, "freeroute": 0, "socket": 0}

    async def _acompletion(*_args, **_kwargs):
        counts["litellm"] += 1
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }

    def _complete(*_args, **_kwargs):
        counts["freeroute"] += 1
        return "ok"

    def _connect(_self, _address):
        counts["socket"] += 1
        raise OSError("sockets blocked")

    monkeypatch.setattr(litellm, "acompletion", _acompletion)
    monkeypatch.setattr(freeroute, "complete", _complete)
    monkeypatch.setattr(socket.socket, "connect", _connect)
    try:
        yield counts
    finally:
        _drain_workflows()


def _drain_workflows() -> None:
    from CortexOS.execution import workflow_runner

    deadline = time.time() + 8.0
    while time.time() < deadline:
        with workflow_runner._LOCK:
            active = list(workflow_runner._ACTIVE.items())
        if not active:
            return
        for run_id, fut in active:
            if not fut.done():
                workflow_runner.cancel(run_id)
            try:
                fut.result(timeout=0.2)
            except Exception:
                pass
        time.sleep(0.05)


def _wait_model(counts: dict[str, int], before: int, *, seconds: float = 6.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline and _calls(counts) == before:
        time.sleep(0.05)


def _post(client: TestClient, path: str, body: dict, headers: dict | None = None):
    return client.post(path, json=body, headers=headers or {})


@pytest.mark.parametrize(("pack", "netie_pack", "effective"), PACKS)
def test_unauth_spend_routes_make_zero_model_calls(
    monkeypatch, sentinels, pack, netie_pack, effective
):
    app = _boot(monkeypatch, pack, netie_pack)
    problems: list[str] = []
    with TestClient(app) as client:
        for label, path, body, dms_only in SPEND_ROUTES:
            if dms_only and effective != "dms":
                before = _snap(sentinels)
                res = _post(client, path, body)
                if res.status_code != 404 or _calls(sentinels) != _calls(before):
                    problems.append(
                        f"{label} pack={effective} status={res.status_code} "
                        f"model={_calls(sentinels) - _calls(before)} (want 404 and 0)"
                    )
                continue
            before_n = _calls(sentinels)
            before_sock = sentinels["socket"]
            res = _post(client, path, body)
            if res.status_code == 200:
                _wait_model(sentinels, before_n, seconds=3.0)
            if res.status_code not in (401, 403) or _calls(sentinels) != before_n or sentinels["socket"] != before_sock:
                problems.append(
                    f"{label} pack={effective or 'unset'} status={res.status_code} "
                    f"model={_calls(sentinels) - before_n} "
                    f"socket={sentinels['socket'] - before_sock} body={res.text[:180]}"
                )
    assert not problems, "\n".join(problems)


def test_ambiguous_key_does_not_resolve():
    callers = parse_api_keys("viewer:same-key;steward:same-key")
    assert "same-key" not in callers


def test_viewer_and_bad_roles_cannot_spend(monkeypatch, sentinels):
    """Viewer api_viewer is a named refusal. Unknown and ambiguous roles fail closed."""
    app = _boot(monkeypatch, "dms", None)
    problems: list[str] = []
    with TestClient(app) as client:
        viewer = {"X-API-Key": "sk-viewer-test"}
        for label, path, body, _dms in SPEND_ROUTES:
            before_n = _calls(sentinels)
            before_sock = sentinels["socket"]
            res = _post(client, path, body, viewer)
            if res.status_code == 200:
                _wait_model(sentinels, before_n, seconds=3.0)
            detail = ""
            try:
                detail = str(res.json().get("detail"))
            except Exception:
                detail = res.text[:180]
            if (
                res.status_code != 403
                or detail != VIEWER_REFUSAL
                or _calls(sentinels) != before_n
                or sentinels["socket"] != before_sock
            ):
                problems.append(
                    f"viewer {label} status={res.status_code} detail={detail!r} "
                    f"model={_calls(sentinels) - before_n} "
                    f"socket={sentinels['socket'] - before_sock}"
                )

        client.cookies.set("cortex_api_key", "sk-viewer-test")
        before_n = _calls(sentinels)
        res = _post(client, "/run", RUN_BODY)
        detail = ""
        try:
            detail = str(res.json().get("detail"))
        except Exception:
            detail = res.text[:180]
        if res.status_code != 403 or detail != VIEWER_REFUSAL or _calls(sentinels) != before_n:
            problems.append(
                f"viewer cookie POST /run status={res.status_code} detail={detail!r} "
                f"model={_calls(sentinels) - before_n}"
            )
        client.cookies.clear()

        unknown = {"X-API-Key": "sk-oracle-test"}
        for label, path, body, _dms in SPEND_ROUTES:
            before_n = _calls(sentinels)
            res = _post(client, path, body, unknown)
            if res.status_code != 401 or _calls(sentinels) != before_n:
                problems.append(
                    f"unknown {label} status={res.status_code} model={_calls(sentinels) - before_n}"
                )

    amb = _boot(monkeypatch, "dms", None, keys="viewer:same-key;steward:same-key")
    with TestClient(amb) as client:
        for label, path, body, _dms in SPEND_ROUTES:
            before_n = _calls(sentinels)
            res = _post(client, path, body, {"X-API-Key": "same-key"})
            if res.status_code != 401 or _calls(sentinels) != before_n:
                problems.append(
                    f"ambiguous {label} status={res.status_code} "
                    f"model={_calls(sentinels) - before_n} body={res.text[:160]}"
                )

    injected = _boot(monkeypatch, "dms", None)

    async def _bad_caller() -> Caller:
        return Caller(role="viewer steward", actor="api_ambiguous")  # type: ignore[arg-type]

    injected.dependency_overrides[get_caller] = _bad_caller
    with TestClient(injected) as client:
        for label, path, body, _dms in SPEND_ROUTES:
            before_n = _calls(sentinels)
            res = _post(client, path, body)
            detail = ""
            try:
                detail = str(res.json().get("detail"))
            except Exception:
                detail = res.text[:180]
            if res.status_code != 403 or "viewer steward" not in detail or _calls(sentinels) != before_n:
                problems.append(
                    f"injected {label} status={res.status_code} detail={detail!r} "
                    f"model={_calls(sentinels) - before_n}"
                )
    assert not problems, "\n".join(problems)


def _assert_spent(res, counts, before: int, label: str) -> None:
    if res.status_code == 200 and _calls(counts) == before:
        _wait_model(counts, before)
    _drain_workflows()
    assert res.status_code == 200, f"{label} status={res.status_code} body={res.text[:240]}"
    assert _calls(counts) > before, f"{label} did not reach a model call"


def test_steward_and_admin_can_spend(monkeypatch, sentinels):
    app = _boot(monkeypatch, "dms", None)
    with TestClient(app) as client:
        for role, key in (("steward", "sk-steward-test"), ("admin", "sk-admin-test")):
            headers = {"X-API-Key": key}
            before = _calls(sentinels)
            res = _post(client, "/run", {**RUN_BODY, "run_id": f"spend_{role}"}, headers)
            _assert_spent(res, sentinels, before, f"{role} POST /run")

            before = _calls(sentinels)
            res = _post(client, "/api/engine/run", {**ENGINE_BODY, "session_id": f"eng-{role}"}, headers)
            _assert_spent(res, sentinels, before, f"{role} POST /api/engine/run")

            before = _calls(sentinels)
            res = _post(client, "/cortex/constructor/run", CTOR_BODY, headers)
            _assert_spent(res, sentinels, before, f"{role} POST /cortex/constructor/run")

            before = _calls(sentinels)
            res = _post(client, "/api/workflows/run", WF_BODY, headers)
            _assert_spent(res, sentinels, before, f"{role} POST /api/workflows/run")

            before = _calls(sentinels)
            res = _post(client, "/api/workflows/resume", RESUME_BODY, headers)
            assert res.status_code == 400, res.text
            assert _calls(sentinels) == before

        client.cookies.set("cortex_api_key", "sk-steward-test")
        before = _calls(sentinels)
        res = _post(client, "/run", {**RUN_BODY, "run_id": "spend_cookie"})
        _assert_spent(res, sentinels, before, "steward cookie POST /run")
        client.cookies.clear()

        before = _calls(sentinels)
        res = _post(
            client,
            "/run",
            {**RUN_BODY, "run_id": "spend_bearer"},
            {"Authorization": "Bearer sk-admin-test"},
        )
        _assert_spent(res, sentinels, before, "admin bearer POST /run")


def test_dms_auth_disabled_short_circuits_to_admin(monkeypatch, sentinels):
    """Existing get_caller behavior: the flag is admin, so spend routes open."""
    app = _boot(monkeypatch, "ruma", None)
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    with TestClient(app) as client:
        before = _calls(sentinels)
        res = _post(client, "/run", {**RUN_BODY, "run_id": "auth_disabled"})
        _assert_spent(res, sentinels, before, "DMS_AUTH_DISABLED POST /run")
