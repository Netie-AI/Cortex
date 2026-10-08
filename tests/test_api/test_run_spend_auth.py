"""RUN-AUTH-01: model-reaching run routes fail closed before any adapter call.

Spend is steward. A viewer, including actor ``api_viewer``, is a named 403.
Unknown and ambiguous roles do not spend.

``POST /api/engine/run`` uses ``require_spend`` (``get_caller``), so
``DMS_AUTH_DISABLED`` still resolves to admin, matching parent.
``POST /run`` and workflow run/resume use ``require_spend_key``. Those routes
did not call ``get_caller`` on parent, so the flag must not open them.
Constructor ``/run`` uses its own spend dependency. It still ignores the
flag, and it is the only place that reads the ``cortex_api_key`` cookie.

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
from packs.dms.constructor_routes import (
    ORIGIN_DENIED,
    ConstructorOriginConfigError,
    constructor_presented_caller,
    load_constructor_origin_allowlist,
    require_constructor_viewer,
)
from packs.dms.security.api_auth import Caller, get_caller, get_presented_caller, parse_api_keys

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


def _boot(
    monkeypatch: pytest.MonkeyPatch,
    pack: str | None,
    netie_pack: str | None,
    *,
    keys: str = KEYS,
    origin: str | None = None,
):
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
    monkeypatch.delenv("CONSTRUCTOR_ORIGIN_ALLOWLIST", raising=False)
    if origin is not None:
        monkeypatch.setenv("CONSTRUCTOR_ORIGIN_ALLOWLIST", origin)
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


def test_ambiguous_key_does_not_resolve(monkeypatch, sentinels):
    """Ambiguous secret cannot spend. Posts every spend route, so removing the
    route guard (no dependency) makes this red even if the parser still drops
    the key. On the earlier guard-removed run this test stayed green because
    it only inspected ``parse_api_keys`` and never hit a route.
    """
    callers = parse_api_keys("viewer:same-key;steward:same-key")
    assert "same-key" not in callers

    app = _boot(monkeypatch, "dms", None, keys="viewer:same-key;steward:same-key")
    problems: list[str] = []
    with TestClient(app) as client:
        for label, path, body, _dms in SPEND_ROUTES:
            before_n = _calls(sentinels)
            res = _post(client, path, body, {"X-API-Key": "same-key"})
            if res.status_code == 200:
                _wait_model(sentinels, before_n, seconds=3.0)
            if res.status_code != 401 or _calls(sentinels) != before_n:
                problems.append(
                    f"ambiguous {label} status={res.status_code} "
                    f"model={_calls(sentinels) - before_n} body={res.text[:160]}"
                )
    assert not problems, "\n".join(problems)


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
    injected.dependency_overrides[get_presented_caller] = _bad_caller
    injected.dependency_overrides[constructor_presented_caller] = _bad_caller
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

        before = _calls(sentinels)
        res = _post(
            client,
            "/run",
            {**RUN_BODY, "run_id": "spend_bearer"},
            {"Authorization": "Bearer sk-admin-test"},
        )
        _assert_spent(res, sentinels, before, "admin bearer POST /run")


def _assert_flag_closed(res, sentinels, before_n: int, before_sock: int, label: str, problems: list[str]) -> None:
    if res.status_code == 200:
        _wait_model(sentinels, before_n, seconds=3.0)
    if (
        res.status_code not in (401, 403, 404)
        or _calls(sentinels) != before_n
        or sentinels["socket"] != before_sock
    ):
        problems.append(
            f"{label} status={res.status_code} model={_calls(sentinels) - before_n} "
            f"socket={sentinels['socket'] - before_sock} body={res.text[:180]}"
        )


@pytest.mark.parametrize(
    ("label", "path", "body", "dms_only"),
    SPEND_ROUTES,
    ids=[row[0] for row in SPEND_ROUTES],
)
def test_auth_disabled_does_not_widen_spend(monkeypatch, sentinels, label, path, body, dms_only):
    """Flag on, no key. Not wider than parent.

    Parent opened ``/run`` and both workflow routes with no auth, and opened
    ``/api/engine/run`` because ``get_caller`` honors the flag. Head keeps
    that engine opening. The other four did not consult the flag, so head
    refuses them (narrower than parent's open ``/run`` and workflow routes).
    Constructor unauth was already 401 on parent and stays 401 here.
    """
    app = _boot(monkeypatch, "dms", None)
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    problems: list[str] = []
    with TestClient(app) as client:
        before_n = _calls(sentinels)
        before_sock = sentinels["socket"]
        res = _post(client, path, body)
        if path == "/api/engine/run":
            if res.status_code == 200 and _calls(sentinels) == before_n:
                _wait_model(sentinels, before_n)
            _drain_workflows()
            if res.status_code != 200 or _calls(sentinels) <= before_n:
                problems.append(
                    f"{label} flag status={res.status_code} "
                    f"model={_calls(sentinels) - before_n} body={res.text[:180]}"
                )
        else:
            _assert_flag_closed(res, sentinels, before_n, before_sock, f"{label} flag", problems)
            if path == "/cortex/constructor/run" and res.status_code != 401:
                problems.append(f"{label} flag status={res.status_code} want 401")
            if path != "/cortex/constructor/run" and res.status_code != 401:
                problems.append(f"{label} flag status={res.status_code} want 401")
    assert not problems, "\n".join(problems)


def test_auth_disabled_constructor_unauth_is_404_off_dms(monkeypatch, sentinels):
    """Non-dms pack does not mount constructor. Flag on, no key, 404, sentinel 0."""
    app = _boot(monkeypatch, "ruma", None)
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    with TestClient(app) as client:
        before_n = _calls(sentinels)
        res = _post(client, "/cortex/constructor/run", CTOR_BODY)
        assert res.status_code == 404, res.text
        assert _calls(sentinels) == before_n


def test_auth_disabled_viewer_still_cannot_spend_where_flag_is_ignored(monkeypatch, sentinels):
    """Flag on plus a viewer key. Engine still spends (parent get_caller).
    The other routes ignore the flag and keep the steward refusal.
    """
    app = _boot(monkeypatch, "dms", None)
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    viewer = {"X-API-Key": "sk-viewer-test"}
    problems: list[str] = []
    with TestClient(app) as client:
        for label, path, body, _dms in SPEND_ROUTES:
            before_n = _calls(sentinels)
            before_sock = sentinels["socket"]
            res = _post(client, path, body, viewer)
            if path == "/api/engine/run":
                if res.status_code == 200 and _calls(sentinels) == before_n:
                    _wait_model(sentinels, before_n)
                _drain_workflows()
                if res.status_code != 200 or _calls(sentinels) <= before_n:
                    problems.append(
                        f"viewer {label} flag status={res.status_code} "
                        f"model={_calls(sentinels) - before_n}"
                    )
                continue
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
                    f"viewer {label} flag status={res.status_code} detail={detail!r} "
                    f"model={_calls(sentinels) - before_n}"
                )
    assert not problems, "\n".join(problems)


_COOKIE_HEADER_ROUTES = (
    ("POST /run", "/run", RUN_BODY),
    ("POST /api/workflows/run", "/api/workflows/run", WF_BODY),
    ("POST /api/workflows/resume", "/api/workflows/resume", RESUME_BODY),
    ("POST /api/engine/run", "/api/engine/run", ENGINE_BODY),
)
_MUTATING_CONSTRUCTOR = (
    "/cortex/constructor/fetch",
    "/cortex/constructor/ghost",
    "/cortex/constructor/recommend",
    "/cortex/constructor/generate",
    "/cortex/constructor/run",
)
_ALLOWED_ORIGIN = "https://allowed.com"


def _detail(res) -> str:
    try:
        return str(res.json().get("detail"))
    except Exception:
        return res.text[:180]


def test_cookie_only_is_not_a_credential_off_constructor(monkeypatch, sentinels):
    """Steward cookie and no header. These routes do not read the cookie.

    Parent ``get_caller`` accepted only X-API-Key and Bearer, so engine
    cookie-only was 401. Head keeps that. The spend gates on ``/run`` and
    the workflow routes also ignore the cookie, so they are 401 with
    sentinel 0. A cookie fallback in ``get_caller`` would spend.
    """
    app = _boot(monkeypatch, "dms", None)
    problems: list[str] = []
    with TestClient(app) as client:
        client.cookies.set("cortex_api_key", "sk-steward-test")
        for label, path, body in _COOKIE_HEADER_ROUTES:
            before_n = _calls(sentinels)
            before_sock = sentinels["socket"]
            res = _post(client, path, body)
            if res.status_code == 200:
                _wait_model(sentinels, before_n, seconds=3.0)
            if (
                res.status_code != 401
                or _calls(sentinels) != before_n
                or sentinels["socket"] != before_sock
            ):
                problems.append(
                    f"cookie {label} status={res.status_code} detail={_detail(res)!r} "
                    f"model={_calls(sentinels) - before_n} "
                    f"socket={sentinels['socket'] - before_sock}"
                )
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://evil.example",
        "https://evil-allowed.com",
        "https://allowed.com.evil",
        "https://allowed.com:8443",
        "http://allowed.com",
    ],
)
def test_cookie_constructor_run_rejects_bad_origin(monkeypatch, sentinels, origin):
    app = _boot(monkeypatch, "dms", None, origin=_ALLOWED_ORIGIN)
    with TestClient(app) as client:
        client.cookies.set("cortex_api_key", "sk-steward-test")
        headers = {"Referer": _ALLOWED_ORIGIN}
        if origin is not None:
            headers["Origin"] = origin
        before_n = _calls(sentinels)
        res = _post(client, "/cortex/constructor/run", CTOR_BODY, headers)
        if res.status_code == 200:
            _wait_model(sentinels, before_n, seconds=3.0)
        assert res.status_code == 403, res.text
        assert _detail(res) == ORIGIN_DENIED
        assert _calls(sentinels) == before_n


def test_cookie_constructor_mutations_reject_missing_origin(monkeypatch, sentinels):
    app = _boot(monkeypatch, "dms", None, origin=_ALLOWED_ORIGIN)
    problems: list[str] = []
    with TestClient(app) as client:
        client.cookies.set("cortex_api_key", "sk-steward-test")
        for path in _MUTATING_CONSTRUCTOR:
            before_n = _calls(sentinels)
            res = _post(client, path, CTOR_BODY)
            if res.status_code != 403 or _detail(res) != ORIGIN_DENIED or _calls(sentinels) != before_n:
                problems.append(
                    f"{path} status={res.status_code} detail={_detail(res)!r} "
                    f"model={_calls(sentinels) - before_n}"
                )
    assert not problems, "\n".join(problems)


def test_cookie_allowlisted_origin_serves_constructor_run(monkeypatch, sentinels):
    app = _boot(monkeypatch, "dms", None, origin=_ALLOWED_ORIGIN)
    with TestClient(app) as client:
        client.cookies.set("cortex_api_key", "sk-steward-test")
        before = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            CTOR_BODY,
            {"Origin": _ALLOWED_ORIGIN},
        )
        _assert_spent(res, sentinels, before, "allowlisted cookie constructor /run")
        before = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            {**CTOR_BODY, "run_id": "ctor_port"},
            {"Origin": "https://allowed.com:443"},
        )
        _assert_spent(res, sentinels, before, "default-port cookie constructor /run")


def test_header_key_ignores_origin_and_cookie_cannot_upgrade(monkeypatch, sentinels):
    app = _boot(monkeypatch, "dms", None, origin=_ALLOWED_ORIGIN)
    with TestClient(app) as client:
        before = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            CTOR_BODY,
            {"X-API-Key": "sk-steward-test"},
        )
        _assert_spent(res, sentinels, before, "steward key, no Origin")

        before = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            {**CTOR_BODY, "run_id": "ctor_bearer"},
            {"Authorization": "Bearer sk-admin-test", "Origin": "https://evil.example"},
        )
        _assert_spent(res, sentinels, before, "admin bearer, foreign Origin")

        client.cookies.set("cortex_api_key", "sk-steward-test")
        before_n = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            CTOR_BODY,
            {"X-API-Key": "sk-viewer-test", "Origin": _ALLOWED_ORIGIN},
        )
        assert res.status_code == 403, res.text
        assert _detail(res) == VIEWER_REFUSAL
        assert _calls(sentinels) == before_n


def test_star_allowlist_is_rejected_at_load(monkeypatch):
    with pytest.raises(ConstructorOriginConfigError):
        load_constructor_origin_allowlist("*")
    with pytest.raises(ConstructorOriginConfigError):
        load_constructor_origin_allowlist("https://allowed.com,*")
    with pytest.raises(ConstructorOriginConfigError):
        load_constructor_origin_allowlist("https://*")
    with pytest.raises(ConstructorOriginConfigError):
        _boot(monkeypatch, "dms", None, origin="*")


@pytest.mark.parametrize("origin", ["https://allowed.com:99999", "https://allowed.com:not-a-port"])
def test_malformed_origin_port_is_named_403(monkeypatch, sentinels, origin):
    """``urlsplit().port`` raises ValueError. That must be the named 403, not a 500."""
    app = _boot(monkeypatch, "dms", None, origin=_ALLOWED_ORIGIN)
    with TestClient(app) as client:
        client.cookies.set("cortex_api_key", "sk-steward-test")
        before_n = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            CTOR_BODY,
            {"Origin": origin, "Referer": _ALLOWED_ORIGIN},
        )
        assert res.status_code == 403, res.text
        assert _detail(res) == ORIGIN_DENIED
        assert _calls(sentinels) == before_n


def test_empty_origin_allowlist_denies_cookie_constructor_post(monkeypatch, sentinels):
    """Empty ``CONSTRUCTOR_ORIGIN_ALLOWLIST`` plus a cookie-only constructor POST is 403."""
    app = _boot(monkeypatch, "dms", None, origin="")
    with TestClient(app) as client:
        client.cookies.set("cortex_api_key", "sk-steward-test")
        before_n = _calls(sentinels)
        res = _post(
            client,
            "/cortex/constructor/run",
            CTOR_BODY,
            {"Origin": "https://app.netie.ai"},
        )
        assert res.status_code == 403, res.text
        assert _detail(res) == ORIGIN_DENIED
        assert _calls(sentinels) == before_n


def test_constructor_cookie_path_is_cortex(monkeypatch):
    """The session cookie path stays ``/cortex``. A wider path would send it to ``/api``."""
    from http.cookies import SimpleCookie

    from packs.dms.constructor_routes import PREFIX

    assert PREFIX == "/cortex"
    app = _boot(monkeypatch, "dms", None)
    with TestClient(app) as client:
        res = client.post(
            "/cortex/session",
            json={"key": "sk-steward-test"},
            follow_redirects=False,
        )
    assert res.status_code == 303, res.text
    jar = SimpleCookie()
    jar.load(res.headers["set-cookie"])
    assert jar["cortex_api_key"]["path"] == "/cortex"


_WF_CONTROL = (
    ("POST /api/workflows/cancel", "/api/workflows/cancel", {"task_id": "no-such-task"}),
    ("POST /api/workflows/clear", "/api/workflows/clear", {}),
    ("POST /api/workflows/recognize", "/api/workflows/recognize", {"prompt": "summarise the ledger"}),
    (
        "POST /api/workflows/hardware",
        "/api/workflows/hardware",
        {"hardware": {"vram_gb": 80, "marker": "attacker"}},
    ),
)


def test_workflow_control_posts_require_steward_header(monkeypatch):
    """Cancel, clear, recognize, and hardware take a header key, steward or above.

    No cookie branch. ``DMS_AUTH_DISABLED`` does not open them.
    """
    from CortexOS.execution import workflow_runner

    workflow_runner.set_hardware({})
    app = _boot(monkeypatch, "dms", None)
    problems: list[str] = []
    try:
        with TestClient(app) as client:
            for label, path, body in _WF_CONTROL:
                res = _post(client, path, body)
                if res.status_code != 401:
                    problems.append(f"no-auth {label} status={res.status_code} body={res.text[:160]}")
                res = _post(client, path, body, {"X-API-Key": "sk-viewer-test"})
                if res.status_code != 403 or _detail(res) != VIEWER_REFUSAL:
                    problems.append(
                        f"viewer {label} status={res.status_code} detail={_detail(res)!r}"
                    )
                client.cookies.set("cortex_api_key", "sk-steward-test")
                res = _post(client, path, body)
                if res.status_code != 401:
                    problems.append(f"cookie {label} status={res.status_code} body={res.text[:160]}")
                client.cookies.clear()
            if workflow_runner.get_hardware() != {}:
                problems.append(f"hardware changed before a steward post: {workflow_runner.get_hardware()}")

            monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
            for label, path, body in _WF_CONTROL:
                res = _post(client, path, body)
                if res.status_code != 401:
                    problems.append(f"flag-on {label} status={res.status_code} body={res.text[:160]}")

            steward = {"X-API-Key": "sk-steward-test"}
            res = _post(client, "/api/workflows/cancel", {"task_id": "no-such-task"}, steward)
            if res.status_code != 404:
                problems.append(f"steward cancel status={res.status_code} body={res.text[:160]}")
            res = _post(client, "/api/workflows/clear", {}, steward)
            if res.status_code != 200:
                problems.append(f"steward clear status={res.status_code} body={res.text[:160]}")
            res = _post(client, "/api/workflows/recognize", {"prompt": "summarise the ledger"}, steward)
            if res.status_code != 200:
                problems.append(f"steward recognize status={res.status_code} body={res.text[:160]}")
            res = _post(
                client,
                "/api/workflows/hardware",
                {"hardware": {"vram_gb": 8, "marker": "steward"}},
                steward,
            )
            if res.status_code != 200 or workflow_runner.get_hardware().get("marker") != "steward":
                problems.append(
                    f"steward hardware status={res.status_code} "
                    f"stored={workflow_runner.get_hardware()}"
                )
    finally:
        workflow_runner.set_hardware({})
    assert not problems, "\n".join(problems)


def test_unauth_hardware_does_not_reach_later_run_or_resume(monkeypatch):
    """An unauthenticated hardware POST must not change what /run or /resume receives.

    Both routes pass ``workflow_runner.get_hardware()`` into the runner.
    """
    from CortexOS.execution import workflow_runner

    workflow_runner.set_hardware({"marker": "before"})
    seen: dict[str, dict] = {}

    def _start(*_args, **kwargs):
        seen["run"] = dict(kwargs.get("hardware") or {})
        return {"ok": True, "task_id": "stub", "run_id": "stub"}

    def _resume(*_args, **kwargs):
        seen["resume"] = dict(kwargs.get("hardware") or {})
        return {"ok": False, "error": "unknown run: stub"}

    monkeypatch.setattr(workflow_runner, "start", _start)
    monkeypatch.setattr(workflow_runner, "resume", _resume)
    app = _boot(monkeypatch, "dms", None)
    problems: list[str] = []
    try:
        with TestClient(app) as client:
            res = _post(
                client,
                "/api/workflows/hardware",
                {"hardware": {"vram_gb": 1, "marker": "attacker"}},
            )
            stored = dict(workflow_runner.get_hardware())
            if res.status_code != 401 or stored.get("marker") != "before":
                problems.append(
                    f"unauth hardware status={res.status_code} body={res.text[:180]} "
                    f"get_hardware={stored}"
                )
            steward = {"X-API-Key": "sk-steward-test"}
            run = _post(client, "/api/workflows/run", WF_BODY, steward)
            resume = _post(client, "/api/workflows/resume", {"task_id": "stub"}, steward)
            if seen.get("run", {}).get("marker") != "before":
                problems.append(f"later /run received hardware={seen.get('run')} status={run.status_code}")
            if seen.get("resume", {}).get("marker") != "before":
                problems.append(
                    f"later /resume received hardware={seen.get('resume')} status={resume.status_code}"
                )
    finally:
        workflow_runner.set_hardware({})
    assert not problems, "\n".join(problems)


# Every registered route under the constructor and workflow surface must be
# in this table. Scope is ``/cortex`` and ``/cortex/*``, ``/api/workflows*``,
# ``POST /run``, and engine ``POST /api/engine/run``. A route in that scope
# that is missing here fails the walker.
_LISTED_ROUTES = frozenset(
    {
        ("GET", "/cortex"),
        ("GET", "/cortex/"),
        ("GET", "/cortex/login"),
        ("GET", "/cortex/constructor"),
        ("GET", "/cortex/constructor/"),
        ("GET", "/cortex/constructor/ontology"),
        ("GET", "/cortex/constructor/{name}"),
        ("POST", "/cortex/constructor/issue-key"),
        ("POST", "/cortex/constructor/fetch"),
        ("POST", "/cortex/constructor/ghost"),
        ("POST", "/cortex/constructor/recommend"),
        ("POST", "/cortex/constructor/generate"),
        ("POST", "/cortex/constructor/run"),
        ("POST", "/cortex/session"),
        ("POST", "/cortex/session/clear"),
        ("GET", "/api/workflows"),
        ("GET", "/api/workflows/tasks"),
        ("GET", "/api/workflows/task/{task_id}"),
        ("GET", "/api/workflows/task/{task_id}/events"),
        ("POST", "/api/workflows/run"),
        ("POST", "/api/workflows/resume"),
        ("POST", "/api/workflows/cancel"),
        ("POST", "/api/workflows/clear"),
        ("POST", "/api/workflows/recognize"),
        ("POST", "/api/workflows/hardware"),
        ("POST", "/run"),
        ("POST", "/api/engine/run"),
    }
)
# Model call, run creation, cost write, or store write. A viewer floor on any
# of these is a failure. recognize is steward by Lead and is in the table, but
# recognize() does not call a model and does not write a store.
_SPEND_OR_WRITE = frozenset(
    {
        ("POST", "/run"),
        ("POST", "/api/engine/run"),
        ("POST", "/api/workflows/run"),
        ("POST", "/api/workflows/resume"),
        ("POST", "/api/workflows/cancel"),
        ("POST", "/api/workflows/clear"),
        ("POST", "/api/workflows/hardware"),
        ("POST", "/cortex/constructor/run"),
    }
)
_ROLE_NAMES = frozenset({"viewer", "steward", "admin"})


def _dependency_floors(route) -> list[str]:
    """Role strings closed over by the route's own dependencies.

    ``require_constructor_viewer`` is a plain function, not a closure, so it
    is matched by identity. Scanning only ``app.routes`` misses routes that
    live on an included router.
    """
    dep = getattr(route, "dependant", None)
    if dep is None:
        return []
    floors: list[str] = []
    for sub in dep.dependencies:
        call = sub.call
        if call is require_constructor_viewer:
            floors.append("viewer")
            continue
        for cell in getattr(call, "__closure__", None) or ():
            try:
                val = cell.cell_contents
            except ValueError:
                continue
            if isinstance(val, str) and val in _ROLE_NAMES:
                floors.append(val)
    return floors


def _iter_registered_routes(app):
    from fastapi.routing import APIRoute, _IncludedRouter

    def walk(routes, prefix: str):
        for route in routes:
            if isinstance(route, _IncludedRouter):
                child = prefix + (route.include_context.prefix or "")
                yield from walk(route.original_router.routes, child)
                continue
            if not isinstance(route, APIRoute):
                continue
            path = prefix + route.path
            for method in sorted(route.methods or ()):
                if method == "HEAD":
                    continue
                yield method, path, _dependency_floors(route)

    yield from walk(app.router.routes, "")


def _in_walker_scope(path: str) -> bool:
    """Constructor surface, workflow routes, ``POST /run``, and engine ``/run``."""
    if path in {"/run", "/api/engine/run"}:
        return True
    return path == "/cortex" or path.startswith("/cortex/") or path.startswith("/api/workflows")


def _walker_problems(app) -> list[str]:
    """Unlisted in-scope routes, and spend/write routes on a viewer floor."""
    found: dict[tuple[str, str], list[str]] = {}
    problems: list[str] = []
    for method, path, floors in _iter_registered_routes(app):
        if not _in_walker_scope(path):
            continue
        key = (method, path)
        if key not in _LISTED_ROUTES:
            problems.append(f"unlisted {method} {path}")
            continue
        found[key] = floors
    for method, path in sorted(_LISTED_ROUTES):
        floors = found.get((method, path))
        if floors is None:
            problems.append(f"walker missed {method} {path}")
            continue
        if (method, path) in _SPEND_OR_WRITE and (
            "viewer" in floors or not ({"steward", "admin"} & set(floors))
        ):
            problems.append(f"{method} {path} floors={floors}")
        if (method, path) == ("POST", "/api/workflows/recognize") and (
            "viewer" in floors or "steward" not in floors
        ):
            problems.append(f"POST /api/workflows/recognize floors={floors}")
    return problems


def test_spend_or_write_routes_are_not_on_viewer(monkeypatch):
    """Fail on an unlisted constructor or workflow route, or a viewer spend floor.

    The table is ``_LISTED_ROUTES``. A registered route under ``/cortex``,
    ``/api/workflows``, ``POST /run``, or ``POST /api/engine/run`` that is not
    in that table fails, and the message names the route. Spend or write
    routes still fail when their floor is viewer or is not steward or admin.
    recognize stays steward and is not a spend/write route. Included routers
    count: constructor routes and engine ``/run`` are not on the flat list.
    """
    assert _SPEND_OR_WRITE <= _LISTED_ROUTES
    app = _boot(monkeypatch, "dms", None)
    problems = _walker_problems(app)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(("label", "path", "body"), _WF_CONTROL, ids=[row[0] for row in _WF_CONTROL])
def test_steward_cookie_without_header_is_401_on_workflow_control(
    monkeypatch, sentinels, label, path, body
):
    """A steward cookie and no header is not a credential on these four POSTs.

    recognize stays steward. The cookie path is ``/cortex``, and these routes
    do not read it. Status 401, model sentinel stays 0.
    """
    from CortexOS.execution import workflow_runner

    workflow_runner.set_hardware({"marker": "before"})
    app = _boot(monkeypatch, "dms", None)
    before = _calls(sentinels)
    try:
        with TestClient(app) as client:
            client.cookies.set("cortex_api_key", "sk-steward-test")
            res = _post(client, path, body)
        assert res.status_code == 401, f"{label} status={res.status_code} body={res.text[:180]}"
        assert _calls(sentinels) == before, f"{label} sentinel moved {_calls(sentinels) - before}"
        assert workflow_runner.get_hardware().get("marker") == "before", label
    finally:
        workflow_runner.set_hardware({})
