"""WORKFLOW-READ-AUTH-01 (#368): the four GET /api/workflows routes are role-gated.

List, tasks, task and events returned workflow state to any caller. They now
use the same header-only steward dependency as the workflow POSTs (#358).

Every assertion is on what an HTTP caller receives (status and body). The seed
run carries a marker string; a refusal must not contain it. The no-false-positive
tests prove a steward and an admin still get the data, so a gate that refuses
everyone cannot pass.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from CortexOS.execution import workflow_runner, workflow_store

KEYS = {"viewer": "wr-viewer-key", "steward": "wr-steward-key", "admin": "wr-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())
MARKER = "WFREAD-SECRET-MARKER-368"
RUN_ID = "wf_read_auth_seed"
UNKNOWN_ID = "wf_read_auth_missing"

ROUTES = (
    ("list", "/api/workflows"),
    ("tasks", "/api/workflows/tasks"),
    ("task", f"/api/workflows/task/{RUN_ID}"),
    ("events", f"/api/workflows/task/{RUN_ID}/events"),
)
# Paths that only differ by form. Each must be refused (or not found), never 200 with data.
VARIANT_PATHS = (
    "/api/workflows/",
    "/api/workflows/tasks/",
    f"/api/workflows/task/{RUN_ID}/",
    f"/api/workflows/task/{RUN_ID}/events/",
    "/API/workflows",
    "/api/Workflows/tasks",
    f"/api/workflows/task/{RUN_ID}/EVENTS",
    f"/api/workflows//task/{RUN_ID}",
    f"/api/workflows/task/{RUN_ID}%2Fevents",
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("NETIE_PACK", raising=False)
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.setattr(workflow_store, "DB_PATH", tmp_path / "wf.db")
    workflow_store.init()
    workflow_store.create_run(RUN_ID, template_id="document_qa", title=MARKER, prompt=MARKER)
    workflow_store.start_run(RUN_ID)
    workflow_store.finish_run(RUN_ID, status="completed", result=MARKER)

    from CortexOS.api.app import create_app

    with TestClient(create_app()) as c:
        yield c


def _hdr(role: str) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]}


def _assert_refused(res, want: tuple[int, ...] = (401,)) -> None:
    assert res.status_code in want, (res.status_code, res.text[:200])
    assert MARKER not in res.text
    assert RUN_ID not in res.text


@pytest.mark.parametrize(("label", "path"), ROUTES, ids=[r[0] for r in ROUTES])
def test_no_credentials_is_401_with_no_data(client, label, path):
    _assert_refused(client.get(path))


@pytest.mark.parametrize(("label", "path"), ROUTES, ids=[r[0] for r in ROUTES])
def test_wrong_key_is_401(client, label, path):
    _assert_refused(client.get(path, headers={"X-API-Key": "not-a-key"}))
    _assert_refused(client.get(path, headers={"Authorization": "Bearer not-a-key"}))


@pytest.mark.parametrize(("label", "path"), ROUTES, ids=[r[0] for r in ROUTES])
def test_viewer_is_403_with_no_data(client, label, path):
    _assert_refused(client.get(path, headers=_hdr("viewer")), want=(403,))


@pytest.mark.parametrize(("label", "path"), ROUTES, ids=[r[0] for r in ROUTES])
def test_cookie_does_not_open_the_route(client, label, path):
    """The session cookie path is /cortex. A steward cookie must not unlock these."""
    client.cookies.set("cortex_api_key", KEYS["steward"])
    _assert_refused(client.get(path))


@pytest.mark.parametrize(("label", "path"), ROUTES, ids=[r[0] for r in ROUTES])
def test_auth_disabled_flag_does_not_open_the_route(client, monkeypatch, label, path):
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    _assert_refused(client.get(path))


def test_unknown_id_is_not_an_existence_oracle(client):
    """Unauthenticated, a missing task answers exactly like a present one."""
    for suffix in ("", "/events"):
        known = client.get(f"/api/workflows/task/{RUN_ID}{suffix}")
        unknown = client.get(f"/api/workflows/task/{UNKNOWN_ID}{suffix}")
        assert known.status_code == unknown.status_code == 401
        assert known.json() == unknown.json()
        viewer_known = client.get(f"/api/workflows/task/{RUN_ID}{suffix}", headers=_hdr("viewer"))
        viewer_unknown = client.get(
            f"/api/workflows/task/{UNKNOWN_ID}{suffix}", headers=_hdr("viewer")
        )
        assert viewer_known.status_code == viewer_unknown.status_code == 403
        assert viewer_known.json() == viewer_unknown.json()


@pytest.mark.parametrize("path", VARIANT_PATHS)
def test_path_variants_never_serve_data_without_credentials(client, path):
    res = client.get(path)
    assert res.status_code in (401, 403, 404, 405, 307, 308), (res.status_code, res.text[:200])
    assert MARKER not in res.text
    assert RUN_ID not in res.text


@pytest.mark.parametrize("method", ["head", "options", "post", "put", "delete", "patch"])
@pytest.mark.parametrize("path", [r[1] for r in ROUTES])
def test_other_methods_never_serve_data(client, method, path):
    res = getattr(client, method)(path)
    assert res.status_code in (401, 403, 404, 405), (res.status_code, res.text[:200])
    assert MARKER not in res.text


@pytest.mark.parametrize("role", ["steward", "admin"])
def test_sufficient_role_still_reads_list_and_tasks(client, role):
    listed = client.get("/api/workflows", headers=_hdr(role))
    assert listed.status_code == 200
    assert listed.json()["ok"] is True
    assert listed.json()["workflows"] == workflow_runner.list_workflows()
    tasks = client.get("/api/workflows/tasks", headers=_hdr(role))
    assert tasks.status_code == 200
    assert MARKER in tasks.text


@pytest.mark.parametrize("role", ["steward", "admin"])
def test_sufficient_role_still_reads_task_and_events(client, role):
    one = client.get(f"/api/workflows/task/{RUN_ID}", headers=_hdr(role))
    assert one.status_code == 200
    assert one.json()["task"]["id"] == RUN_ID
    assert MARKER in one.text
    events = client.get(f"/api/workflows/task/{RUN_ID}/events", headers=_hdr(role))
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert MARKER in events.text


def test_bearer_header_reads(client):
    res = client.get("/api/workflows/tasks", headers={"Authorization": f"Bearer {KEYS['steward']}"})
    assert res.status_code == 200
    assert MARKER in res.text


def test_authorised_unknown_id_is_404(client):
    """Past the gate, the lookup still answers 404 for a missing id."""
    assert client.get(f"/api/workflows/task/{UNKNOWN_ID}", headers=_hdr("steward")).status_code == 404
    assert (
        client.get(f"/api/workflows/task/{UNKNOWN_ID}/events", headers=_hdr("steward")).status_code
        == 404
    )


def test_registration_without_auth_dependency_fails_closed():
    """No spend_auth passed: every GET refuses 401, none serves."""
    from fastapi import FastAPI

    from CortexOS.api.workflow_routes import register_workflow_routes

    app = FastAPI()
    register_workflow_routes(app)
    with TestClient(app) as c:
        for _label, path in ROUTES:
            res = c.get(path)
            assert res.status_code == 401, (path, res.status_code)


def test_refusal_happens_before_the_store_is_read(client, monkeypatch):
    """No store or runner read runs for a refused caller (ported from the duplicate file)."""
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
    ):
        monkeypatch.setattr(mod, name, _trap(f"{mod.__name__.rsplit('.', 1)[-1]}.{name}"))
    for _label, path in ROUTES:
        for headers in ({}, _hdr("viewer")):
            res = client.get(path, headers=headers)
            assert res.status_code in (401, 403), f"GET {path} status={res.status_code}"
    assert touched == []
