"""WORKFLOW-READ-AUTH-01 (#368): the four ``/api/workflows`` GETs need a header key.

List, tasks, task, and events take the same dependency as the POSTs on the
same registrar (``require_spend_key``: header key, steward or above, no cookie,
``DMS_AUTH_DISABLED`` not honored). A caller without it is refused before the
store is read, so a known task id and an unknown one answer the same.

Every refusal case here fails on main 304bf84, where the four GETs have no
dependency and answer 200 or 404 to anyone.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

KEYS = "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test"
VIEWER_REFUSAL = "Requires role 'steward' or higher (caller='viewer' actor='api_viewer')"
KNOWN = "wf_read_auth_known"
UNKNOWN = "wf_read_auth_no_such_task"
# Stored on the seeded run. It must never appear in a refused response.
MARKER = "MARKER-368-run-title"
TEMPLATE_MARKER = "document_qa"

READ_ROUTES = (
    ("list", "/api/workflows"),
    ("tasks", "/api/workflows/tasks"),
    ("task known", f"/api/workflows/task/{KNOWN}"),
    ("task unknown", f"/api/workflows/task/{UNKNOWN}"),
    ("events known", f"/api/workflows/task/{KNOWN}/events"),
    ("events unknown", f"/api/workflows/task/{UNKNOWN}/events"),
)
IDS = [row[0] for row in READ_ROUTES]

# Each is a caller that holds no steward header key.
NO_CREDENTIAL = (
    ("no header", {}, None, False),
    ("unknown key", {"X-API-Key": "sk-not-a-key"}, None, False),
    ("unknown bearer", {"Authorization": "Bearer sk-not-a-key"}, None, False),
    ("steward cookie only", {}, "sk-steward-test", False),
    ("DMS_AUTH_DISABLED=1", {}, None, True),
)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The real ``create_app`` on pack dms, with one finished run in a temp store."""
    import CortexOS.config as cfg
    from CortexOS.execution import workflow_store

    monkeypatch.setattr(cfg, "get_config_path", lambda: Path("/tmp/no-such-netie-config.toml"))
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("NETIE_PACK", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", KEYS)
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    for name in ("CortexOS.config", "netie.config"):
        mod = sys.modules.get(name)
        if mod is not None and hasattr(mod, "_cached_config"):
            mod._cached_config = None
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(per_minute=10000)

    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "runs.db")
    workflow_store.init()
    workflow_store.create_run(KNOWN, template_id="document_qa", title=MARKER, prompt=MARKER)
    workflow_store.finish_run(KNOWN, status="completed", result=MARKER)
    assert workflow_store.get_run(KNOWN), "seed run missing"
    assert not workflow_store.get_run(UNKNOWN)

    from CortexOS.api.app import create_app

    return create_app()


def _detail(res) -> str:
    try:
        return str(res.json().get("detail") or "")
    except Exception:
        return ""


def _leaks(res) -> list[str]:
    """Anything in a refused body that came from the store or the registry."""
    text = res.text
    found = [tok for tok in (MARKER, KNOWN, TEMPLATE_MARKER, "unknown task") if tok in text]
    try:
        body = res.json()
    except Exception:
        return found + ["non-json body"]
    extra = sorted(set(body) - {"detail"}) if isinstance(body, dict) else ["non-object body"]
    return found + extra


@pytest.mark.parametrize(("label", "path"), READ_ROUTES, ids=IDS)
def test_read_routes_refuse_a_caller_without_a_header_key(app, monkeypatch, label, path):
    """401 and no data, for every way of arriving without a valid header key."""
    problems: list[str] = []
    with TestClient(app) as client:
        for who, headers, cookie, flag in NO_CREDENTIAL:
            if flag:
                monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
            if cookie:
                client.cookies.set("cortex_api_key", cookie)
            res = client.get(path, headers=headers)
            client.cookies.clear()
            if res.status_code != 401:
                problems.append(f"{who}: status={res.status_code} body={res.text[:160]}")
            if _leaks(res):
                problems.append(f"{who}: leaked {_leaks(res)} body={res.text[:160]}")
    assert not problems, f"GET {path}\n" + "\n".join(problems)


@pytest.mark.parametrize(("label", "path"), READ_ROUTES, ids=IDS)
def test_read_routes_refuse_a_viewer_with_named_403(app, label, path):
    with TestClient(app) as client:
        res = client.get(path, headers={"X-API-Key": "sk-viewer-test"})
    assert res.status_code == 403, f"GET {path} status={res.status_code} body={res.text[:160]}"
    assert _detail(res) == VIEWER_REFUSAL
    assert not _leaks(res), f"GET {path} leaked {_leaks(res)}"


@pytest.mark.parametrize("suffix", ["", "/events"], ids=["task", "events"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": "sk-not-a-key"}, {"X-API-Key": "sk-viewer-test"}],
    ids=["no header", "unknown key", "viewer"],
)
def test_task_id_is_not_an_existence_oracle_below_steward(app, headers, suffix):
    """A known id and an unknown id are the same response to a refused caller."""
    with TestClient(app) as client:
        known = client.get(f"/api/workflows/task/{KNOWN}{suffix}", headers=headers)
        unknown = client.get(f"/api/workflows/task/{UNKNOWN}{suffix}", headers=headers)
    assert known.status_code in (401, 403), known.text[:160]
    assert (known.status_code, known.content) == (unknown.status_code, unknown.content)
    assert known.headers.get("content-type") == unknown.headers.get("content-type")
    assert known.headers.get("content-length") == unknown.headers.get("content-length")


def test_refusal_happens_before_the_store_is_read(app, monkeypatch):
    """No store or runner read runs for a refused caller."""
    from CortexOS.execution import workflow_runner, workflow_store

    touched: list[str] = []

    def _trap(name):
        def _call(*_args, **_kwargs):
            touched.append(name)
            raise AssertionError(f"{name} reached without a steward key")

        return _call

    for mod, name in (
        (workflow_runner, "list_workflows"),
        (workflow_runner, "snapshot"),
        (workflow_runner, "get_task"),
        (workflow_store, "get_run"),
        (workflow_store, "list_runs"),
        (workflow_store, "stream"),
    ):
        monkeypatch.setattr(mod, name, _trap(f"{mod.__name__.rsplit('.', 1)[-1]}.{name}"))
    with TestClient(app, raise_server_exceptions=False) as client:
        for _label, path in READ_ROUTES:
            for headers in ({}, {"X-API-Key": "sk-viewer-test"}):
                res = client.get(path, headers=headers)
                assert res.status_code in (401, 403), f"GET {path} status={res.status_code}"
    assert touched == []


@pytest.mark.parametrize("key", ["sk-steward-test", "sk-admin-test"], ids=["steward", "admin"])
@pytest.mark.parametrize("scheme", ["x-api-key", "bearer"])
def test_steward_and_admin_still_read(app, key, scheme):
    """The gate must not block the caller it is meant to admit."""
    headers = {"X-API-Key": key} if scheme == "x-api-key" else {"Authorization": f"Bearer {key}"}
    with TestClient(app) as client:
        res = client.get("/api/workflows", headers=headers)
        assert res.status_code == 200, res.text[:200]
        ids = [w.get("id") for w in res.json()["workflows"]]
        assert "document_qa" in ids, ids

        res = client.get("/api/workflows/tasks", headers=headers)
        assert res.status_code == 200, res.text[:200]
        finished = res.json()["finished"]
        assert [t.get("id") for t in finished] == [KNOWN], finished
        assert MARKER in res.text

        res = client.get(f"/api/workflows/task/{KNOWN}", headers=headers)
        assert res.status_code == 200, res.text[:200]
        assert res.json()["task"]["id"] == KNOWN
        assert MARKER in res.text

        res = client.get(f"/api/workflows/task/{UNKNOWN}", headers=headers)
        assert res.status_code == 404 and _detail(res) == "unknown task", res.text[:200]

        res = client.get(f"/api/workflows/task/{KNOWN}/events", headers=headers)
        assert res.status_code == 200, res.text[:200]
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.text.startswith('data: {"type": "snapshot"') and MARKER in res.text

        res = client.get(f"/api/workflows/task/{UNKNOWN}/events", headers=headers)
        assert res.status_code == 404 and _detail(res) == "unknown task", res.text[:200]


def test_registrar_without_a_dependency_fails_closed_on_reads():
    """``register_workflow_routes(app)`` with no ``spend_auth`` serves no read."""
    from CortexOS.api.workflow_routes import register_workflow_routes

    bare = FastAPI()
    register_workflow_routes(bare)
    with TestClient(bare) as client:
        for _label, path in READ_ROUTES:
            res = client.get(path, headers={"X-API-Key": "sk-steward-test"})
            assert res.status_code == 401, f"GET {path} status={res.status_code}"
            assert not _leaks(res), f"GET {path} leaked {_leaks(res)}"


def test_every_workflow_route_carries_the_steward_key_dependency(app):
    """A route added to this surface without the dependency fails here.

    Matches the dependency by identity, on every method of every route whose
    path starts with ``/api/workflows``, case-insensitively.
    """
    from fastapi.routing import APIRoute

    from packs.dms.security.api_auth import require_spend_key

    seen: list[str] = []
    problems: list[str] = []
    for route in app.router.routes:
        path = getattr(route, "path", "")
        if not path.lower().startswith("/api/workflows"):
            continue
        methods = sorted(getattr(route, "methods", None) or ())
        seen.append(f"{','.join(methods)} {path}")
        if not isinstance(route, APIRoute):
            problems.append(f"non-APIRoute {path}")
            continue
        calls = [sub.call for sub in route.dependant.dependencies]
        if require_spend_key not in calls:
            problems.append(f"{','.join(methods)} {path} has no steward key dependency")
    assert len(seen) == 10, seen
    assert not problems, "\n".join(problems)
