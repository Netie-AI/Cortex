"""Every route on the real app needs a credential unless a table here says why not (#263).

The test walks the route table of ``create_app()`` on pack ``dms`` with every
optional route module that is installed. Each ``(method, path)`` lands in
exactly one bucket:

* ``PUBLIC``: open on purpose, one reason per route;
* ``OUT_OF_SCOPE``: open today and owned by another ticket or a product
  decision, one reason per route; counted, never silently passed;
* ``REDIRECTS_TO_LOGIN``: the constructor HTML pages check the key inside the
  handler and send an anonymous caller to ``/cortex/login``;
* everything else: an anonymous request is refused with 401 or 403, no route
  handler runs, and nothing downstream is called.

A route added later without a gate is in none of the tables and fails here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api_key_isolation import TEST_STEWARD_KEY, TEST_VIEWER_KEY

# (method, path) -> why it is open.
PUBLIC: dict[tuple[str, str], str] = {
    ("GET", "/health"): "liveness probe; returns a fixed status, no data",
    ("GET", "/health/db"): "readiness probe for the database pool; no rows",
    ("GET", "/health/features"): "which optional extras are installed; no data",
    ("GET", "/openapi.json"): "FastAPI schema; whether to hide it is a product decision",
    ("GET", "/docs"): "FastAPI docs page; same decision as /openapi.json",
    ("GET", "/docs/oauth2-redirect"): "FastAPI docs helper page",
    ("GET", "/redoc"): "FastAPI docs page; same decision as /openapi.json",
    ("GET", "/cortex/login"): "the login form itself",
    ("POST", "/cortex/session"): "login: exchanges a key for the cookie; refuses a bad key with 401",
    ("POST", "/cortex/session/clear"): "logout: only deletes the caller's own cookie",
    ("POST", "/cortex/constructor/issue-key"): "loopback-only mint, refused in the handler for any other peer",
}

# (method, path) -> owner. Open today, not fixed by #263, counted.
OUT_OF_SCOPE: dict[tuple[str, str], str] = {
    **{
        (m, p): "#265 contract routes (founder GO pending)"
        for m, p in (
            ("POST", "/v1/contract/ask"),
            ("POST", "/v1/contract/submit"),
            ("POST", "/v1/contract/ledger/append"),
            ("POST", "/v1/contract/ledger/verify"),
            ("POST", "/v1/contract/drillthrough"),
            ("GET", "/v1/contract/tools"),
            ("POST", "/v1/contract/jwks/refresh"),
        )
    },
    **{
        (m, p): "product decision: DMS calls /v1/insights with the demo viewer key; insights has its own caller-key rule"
        for m, p in (
            ("GET", "/v1/insights"),
            ("GET", "/v1/insights/"),
            ("POST", "/v1/insights"),
            ("POST", "/v1/insights/"),
            ("GET", "/v1/insights/identity"),
            ("GET", "/v1/insights/keys"),
            ("GET", "/v1/insights/ontology"),
        )
    },
    ("POST", "/search"): "product decision: ruma listing search; whether anonymous shoppers may search",
    **{
        (m, p): "product decision: Clicks devices post without an API key; device auth is a protocol change"
        for m, p in (
            ("POST", "/v1/telemetry"),
            ("POST", "/v1/telemetry/register"),
            ("GET", "/v1/telemetry/health"),
        )
    },
}

# Constructor HTML: the handler checks the key and redirects an anonymous caller.
REDIRECTS_TO_LOGIN: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/cortex"),
        ("GET", "/cortex/"),
        ("GET", "/cortex/constructor"),
        ("GET", "/cortex/constructor/"),
        ("GET", "/cortex/constructor/{name}"),
    }
)

# Downstream calls that must not happen for an anonymous caller. Each is the
# function the #263 triage reached without credentials, or a store write.
_DOWNSTREAM: tuple[tuple[str, str], ...] = (
    ("CortexOS.dms.query_service", "answer_question"),
    ("CortexOS.dms.query_service", "propose_edits"),
    ("CortexOS.dms.query_service", "list_audit_entries"),
    ("CortexOS.dms.warehouse_db", "preview_table"),
    ("CortexOS.dms.warehouse_db", "table_row_counts"),
    ("CortexOS.dms.entry_analyser", "add_inventory_entry"),
    ("CortexOS.dms.entry_analyser", "analyse_raw_entry"),
    ("CortexOS.execution.race_router", "auto_route"),
    ("CortexOS.execution.routine_scheduler", "run_once"),
    ("CortexOS.execution.routine_scheduler", "tick"),
    ("CortexOS.execution.routine_scheduler", "create_from_goal"),
    ("CortexOS.execution.routine_scheduler", "list_routines"),
    ("CortexOS.execution.enterprise_goal", "list_goals"),
    ("CortexOS.execution.commitments", "record_from_text"),
    ("packs.dms.chat.threads", "create_thread"),
    ("packs.dms.chat.threads", "append_message"),
    ("packs.dms.chat.threads", "list_messages"),
    ("packs.dms.tasks.gate", "create_task_event"),
    ("packs.dms.tasks.gate", "acknowledge_event"),
    ("packs.dms.vision.intake", "intake_item"),
    ("packs.dms.vision.intake", "confirm_item_dims"),
    ("packs.dms.vision.movement", "scan_move"),
    ("packs.dms.vision.locations", "build_location"),
)


def _iter_routes(app: Any) -> Iterator[tuple[str, str]]:
    """Yield ``(method, path)`` for every route, expanding included routers. No HEAD."""
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from starlette.routing import Mount, Route, WebSocketRoute

    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:  # older FastAPI flattens included routers
        _IncludedRouter = ()  # type: ignore[assignment,misc]

    def walk(routes: list[Any], prefix: str) -> Iterator[tuple[str, str]]:
        for route in routes:
            if _IncludedRouter and isinstance(route, _IncludedRouter):
                yield from walk(
                    route.original_router.routes,
                    prefix + (route.include_context.prefix or ""),
                )
                continue
            path = prefix + getattr(route, "path", "")
            if isinstance(route, (APIWebSocketRoute, WebSocketRoute)):
                yield "WS", path
            elif isinstance(route, Mount):
                yield "MOUNT", path
            elif isinstance(route, (APIRoute, Route)):
                for method in sorted(route.methods or ()):
                    if method != "HEAD":
                        yield method, path
            else:
                yield "UNKNOWN", path

    yield from walk(app.router.routes, "")


_ROLES = ("viewer", "steward", "admin")
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Write-method routes that are gated below steward on purpose (Lead ruling on
# #263, 2026-10-09: a write route under the steward floor must be listed here
# with its reason). (method, path) -> reason.
VIEWER_WRITES: dict[tuple[str, str], str] = {
    ("POST", "/api/engine/just-works"): "plans with no model call; apply=true is checked at admin in the handler",
    ("POST", "/api/engine/coordination-patterns/recommend"): "local pattern recommendation, no store write",
    ("POST", "/api/memory/query"): "memory store query only",
    ("POST", "/api/memory/assemble"): "store query plus text joining",
    ("POST", "/api/context/assemble"): "local token budgeting, no write",
    ("POST", "/api/context/compact"): "truncates the posted text, no model, no write",
    ("POST", "/api/discovery/tools/search"): "search over an in-memory catalog",
    ("POST", "/api/discovery/tools/inject"): "mutates a per-request catalog object only",
    ("POST", "/api/discovery/find-mcp"): "evolve is hard-coded off",
    ("POST", "/api/discovery/find-subagents"): "evolve is hard-coded off",
    ("POST", "/api/engine/osr"): "classify-only; local hash embedding",
    ("POST", "/dms/analyse-entry"): "parses and validates the posted text",
    ("POST", "/dms/secure"): "scam, injection and PII checks with no I/O",
    ("POST", "/dms/classify"): "local intent classifier",
    ("POST", "/dms/audit/verify"): "reads the ledger chain",
    ("POST", "/dms/sidecar/query-objects"): "agent-visible columns under a bound session grant",
    ("POST", "/dms/lakehouse/query"): "validated SQL on a read-only DuckDB connection",
    ("POST", "/cortex/constructor/fetch"): "read-only preview, at most 100 rows (column scope: see open item)",
    ("POST", "/cortex/constructor/ghost"): "compiles a graph, no write",
    ("POST", "/cortex/constructor/recommend"): "local pattern catalog",
    ("POST", "/cortex/constructor/generate"): "deterministic template match, no model, no write",
    ("POST", "/a2a/messages"): "in-process delivery to canned echo and seller handlers",
}

# Write-method routes already on the viewer floor before #263 that spend a model
# credential or write a store. Not accepted: each is an open role decision,
# routed from the #263 PR. Counted here so none is silently passed, and a new
# one still fails the walker.
VIEWER_WRITE_DEBT: dict[tuple[str, str], str] = {
    ("POST", "/dms/query"): "spends Cortex's credential via L2 at viewer; kept at viewer because DMS calls it with the demo viewer key (#265 item 3)",
    ("POST", "/dms/brain/chart"): "model call on Cortex's credential at viewer",
    ("POST", "/dms/brain/analyze"): "model call on Cortex's credential at viewer",
    ("POST", "/dms/brain/auto-analysis"): "model call on Cortex's credential at viewer",
    ("POST", "/dms/brain/report"): "model call on Cortex's credential at viewer",
    ("POST", "/dms/brain/suggest"): "model call when use_llm=true, at viewer",
    ("POST", "/dms/brain/ponytail"): "ledger append per uncached request, body-chosen user_id, at viewer",
    ("POST", "/dms/sidecar/call-action"): "ledger append on every denial, at viewer",
    ("POST", "/dms/sidecar/insights"): "generate=true spends (caller ov_ key or loopback), at viewer",
    ("POST", "/mcp/call"): "answer_engine.answer spends; find_skills evolve writes skill files",
    ("POST", "/api/discovery/find"): "evolve=true writes skill files, at viewer",
    ("POST", "/api/discovery/find-skills"): "evolve=true writes skill files, at viewer",
    ("POST", "/api/engine/bakeoff"): "outbound HTTP probes; write_report writes bench files, at viewer",
}


def _closure_floors(fn: Any, depth: int = 0) -> list[str]:
    """Role strings an auth dependency closes over, following nested closures."""
    if depth > 4 or fn is None:
        return []
    out: list[str] = []
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            val = cell.cell_contents
        except ValueError:
            continue
        if isinstance(val, str) and val in _ROLES:
            out.append(val)
        elif callable(val):
            out.extend(_closure_floors(val, depth + 1))
    return out


def _route_floor(route: Any) -> str | None:
    """Effective floor: every dependency must pass, so the strictest role wins."""
    floors: list[str] = []

    def visit(dependant: Any) -> None:
        for sub in dependant.dependencies:
            name = getattr(sub.call, "__name__", "")
            if name == "require_constructor_viewer":
                floors.append("viewer")
            floors.extend(_closure_floors(sub.call))
            visit(sub)

    visit(route.dependant)
    if not floors:
        return None
    return max(floors, key=_ROLES.index)


def _iter_api_routes(app: Any) -> Iterator[tuple[str, str, Any]]:
    from fastapi.routing import APIRoute

    try:
        from fastapi.routing import _IncludedRouter
    except ImportError:
        _IncludedRouter = ()  # type: ignore[assignment,misc]

    def walk(routes: list[Any], prefix: str) -> Iterator[tuple[str, str, Any]]:
        for route in routes:
            if _IncludedRouter and isinstance(route, _IncludedRouter):
                yield from walk(
                    route.original_router.routes,
                    prefix + (route.include_context.prefix or ""),
                )
            elif isinstance(route, APIRoute):
                for method in sorted(route.methods or ()):
                    if method != "HEAD":
                        yield method, prefix + route.path, route

    yield from walk(app.router.routes, "")


def _write_floor_problems(app: Any) -> list[str]:
    """Every gated write route is steward or higher unless ``VIEWER_WRITES`` lists it."""
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for method, path, route in _iter_api_routes(app):
        key = (method, path)
        seen.add(key)
        if method not in _WRITE_METHODS:
            continue
        if key in PUBLIC or key in OUT_OF_SCOPE or key in REDIRECTS_TO_LOGIN:
            continue
        floor = _route_floor(route)
        if floor is None:
            problems.append(f"unlisted write route with no auth floor: {method} {path}")
        elif floor == "viewer" and key not in VIEWER_WRITES and key not in VIEWER_WRITE_DEBT:
            problems.append(f"write route on viewer floor: {method} {path}")
    for table in (VIEWER_WRITES, VIEWER_WRITE_DEBT):
        problems += [f"stale viewer-write entry {k}" for k in table if k not in seen]
        problems += [f"no reason for {k}" for k, why in table.items() if not str(why).strip()]
    for method, path, route in _iter_api_routes(app):
        key = (method, path)
        if (key in VIEWER_WRITES or key in VIEWER_WRITE_DEBT) and _route_floor(route) != "viewer":
            problems.append(f"viewer-write entry no longer on viewer floor: {method} {path}")
    return problems


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x263", path)


@pytest.fixture
def app(monkeypatch, tmp_path):
    from CortexOS.execution import app_store

    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    from CortexOS.api.app import create_app
    from packs.dms.security.rate_limit import reset_limiter

    # The sweep sends hundreds of requests; a 429 must not stand in for a 401.
    reset_limiter(per_minute=1_000_000)
    yield create_app()
    reset_limiter()


@pytest.fixture
def spies(monkeypatch):
    """Record every route handler call and every listed downstream call."""
    import importlib

    import fastapi.routing as fr

    calls: list[str] = []
    real_run = fr.run_endpoint_function

    async def spy_run(*, dependant, values, is_coroutine, **kw):
        calls.append(f"endpoint:{getattr(dependant.call, '__name__', '?')}")
        return await real_run(dependant=dependant, values=values, is_coroutine=is_coroutine, **kw)

    monkeypatch.setattr(fr, "run_endpoint_function", spy_run)
    for mod_name, attr in _DOWNSTREAM:
        mod = importlib.import_module(mod_name)

        def _rec(*_a, _n=f"{mod_name}.{attr}", **_k):
            calls.append(_n)
            raise AssertionError(f"{_n} reached")

        monkeypatch.setattr(mod, attr, _rec)
    return calls


def _gated(app) -> list[tuple[str, str]]:
    out = []
    for method, path in _iter_routes(app):
        key = (method, path)
        if key in PUBLIC or key in OUT_OF_SCOPE or key in REDIRECTS_TO_LOGIN:
            continue
        out.append(key)
    return out


def test_every_route_is_in_one_bucket_and_the_tables_are_not_stale(app):
    seen: dict[tuple[str, str], int] = {}
    for key in _iter_routes(app):
        seen[key] = seen.get(key, 0) + 1
    problems = [f"{m} {p} registered {n} times" for (m, p), n in seen.items() if n > 1]
    problems += [f"{m} {p}" for (m, p) in seen if m in ("MOUNT", "WS", "UNKNOWN")]
    for table in (PUBLIC, OUT_OF_SCOPE):
        problems += [f"stale entry {k}" for k in table if k not in seen]
        problems += [f"no reason for {k}" for k, why in table.items() if not str(why).strip()]
    problems += [f"stale redirect entry {k}" for k in REDIRECTS_TO_LOGIN if k not in seen]
    assert not (set(PUBLIC) & set(OUT_OF_SCOPE))
    assert not problems, "\n".join(problems)
    # The walker found the real app, not a stub profile.
    assert len(seen) >= 200, len(seen)


def test_anonymous_caller_is_refused_on_every_gated_route_and_nothing_runs(app, spies):
    gated = _gated(app)
    assert len(gated) >= 150, len(gated)
    bad: list[str] = []
    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path in gated:
            res = client.request(method, _concrete(path), json={}, follow_redirects=False)
            if res.status_code not in (401, 403):
                bad.append(f"{method} {path} -> {res.status_code} {res.text[:120]}")
    assert not bad, "\n".join(bad)
    assert spies == [], spies


@pytest.mark.parametrize("variant", ["trailing_slash", "upper_case", "double_slash"])
def test_path_variants_of_gated_routes_never_serve_anonymously(app, spies, variant):
    bad: list[str] = []
    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path in _gated(app):
            concrete = _concrete(path)
            if variant == "trailing_slash":
                probe = concrete.rstrip("/") + "/"
            elif variant == "upper_case":
                probe = concrete.upper()
            else:
                probe = "/" + concrete
            res = client.request(method, probe, json={}, follow_redirects=True)
            if 200 <= res.status_code < 300:
                bad.append(f"{method} {probe} -> {res.status_code}")
    assert not bad, "\n".join(bad)
    assert spies == [], spies


def test_constructor_pages_send_an_anonymous_caller_to_login(app, spies):
    with TestClient(app, raise_server_exceptions=False) as client:
        for method, path in sorted(REDIRECTS_TO_LOGIN):
            res = client.request(method, _concrete(path), follow_redirects=False)
            assert res.status_code == 303, (path, res.status_code)
            assert res.headers["location"] == "/cortex/login"
    # The gate is inside these handlers, so they run; nothing downstream does.
    handlers = {"endpoint:cortex_root", "endpoint:constructor_index", "endpoint:constructor_asset"}
    assert spies and set(spies) <= handlers, spies


def test_spy_can_fire(app, spies):
    """The spy is a real gate: an authenticated call does reach the handler."""
    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.get("/dms/table-preview?table=inventory", headers={"X-API-Key": TEST_VIEWER_KEY})
    assert res.status_code == 500  # the preview_table spy raised inside the handler
    assert "CortexOS.dms.warehouse_db.preview_table" in spies


def test_table_preview_without_a_key_returns_no_rows(app):
    with TestClient(app) as client:
        res = client.get("/dms/table-preview?table=inventory")
    assert res.status_code == 401
    assert "rows" not in res.json()
    assert res.json() == {"detail": "Valid API key required (X-API-Key or Bearer): missing_key"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/dms/add-entry", {"proposed": {"sku": "SKU-T263"}, "approved_by": "ceo"}),
        ("/dms/propose-edit", {"changes": [], "approved_by": "ceo"}),
        ("/dms/threads", {"actor": "ceo"}),
        ("/dms/tasks/gate/acknowledge", {"event_id": "e1", "actor": "ceo"}),
        ("/dms/items/intake", {"sku": "S", "label": "L", "location_code": "A", "photo": "", "actor": "ceo"}),
    ],
)
def test_viewer_cannot_write(app, spies, path, body):
    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.post(path, json=body, headers={"X-API-Key": TEST_VIEWER_KEY})
    assert res.status_code == 403, res.text
    assert spies == []


def _capture(monkeypatch, module: str, attr: str, result: Any) -> list[dict[str, Any]]:
    import importlib

    seen: list[dict[str, Any]] = []

    def fake(*args, **kwargs):
        seen.append({"args": args, **kwargs})
        return result

    monkeypatch.setattr(importlib.import_module(module), attr, fake)
    return seen


@pytest.mark.parametrize(
    ("path", "body", "module", "attr", "actor_kw"),
    [
        (
            "/dms/add-entry",
            {"proposed": {"sku": "SKU-T263"}, "approved_by": "ceo"},
            "CortexOS.dms.entry_analyser",
            "add_inventory_entry",
            "approved_by",
        ),
        (
            "/dms/propose-edit",
            {"changes": [{"row": 1}], "approved_by": "ceo"},
            "CortexOS.dms.query_service",
            "propose_edits",
            "approved_by",
        ),
        ("/dms/threads", {"actor": "ceo"}, "packs.dms.chat.threads", "create_thread", "actor"),
        (
            "/dms/threads/t1/messages",
            {"sender": "s", "body": "b", "actor": "ceo"},
            "packs.dms.chat.threads",
            "append_message",
            "actor",
        ),
        (
            "/dms/tasks/gate/acknowledge",
            {"event_id": "e1", "actor": "ceo"},
            "packs.dms.tasks.gate",
            "acknowledge_event",
            "actor",
        ),
        (
            "/dms/items/intake",
            {"sku": "S", "label": "L", "location_code": "A", "photo": "", "actor": "ceo"},
            "packs.dms.vision.intake",
            "intake_item",
            "actor",
        ),
        (
            "/dms/movements/scan",
            {"item_qr_or_id": "i", "to_location_qr": "q", "actor": "ceo"},
            "packs.dms.vision.movement",
            "scan_move",
            "actor",
        ),
        (
            "/dms/items/i1/confirm-dims",
            {"l": 1, "w": 1, "h": 1, "actor": "ceo"},
            "packs.dms.vision.intake",
            "confirm_item_dims",
            "actor",
        ),
    ],
)
def test_actor_is_the_authenticated_caller_never_the_body(
    app, monkeypatch, path, body, module, attr, actor_kw
):
    from packs.dms.tasks import gate

    verdict = type("V", (), {"status": "ok", "violations": [], "executable": True, "event_id": "e1"})()
    result: Any = verdict if module == "packs.dms.tasks.gate" else {"ok": True, "echo": "served"}
    seen = _capture(monkeypatch, module, attr, result)
    _ = gate
    with TestClient(app) as client:
        res = client.post(path, json=body, headers={"X-API-Key": TEST_STEWARD_KEY})
    assert res.status_code == 200, res.text
    assert len(seen) == 1
    assert seen[0][actor_kw] == "api_steward"
    assert "ceo" not in res.text


def test_every_write_route_is_steward_or_listed(app):
    problems = _write_floor_problems(app)
    assert not problems, "\n".join(problems)


def test_write_floor_walker_fails_on_an_unlisted_open_write_route(app):
    """Planted must-fail: a new POST with no gate is reported."""

    @app.post("/planted/t263/open")
    async def planted_open() -> dict[str, Any]:
        return {}

    problems = _write_floor_problems(app)
    assert "unlisted write route with no auth floor: POST /planted/t263/open" in problems


def test_write_floor_walker_fails_on_a_viewer_floor_write_route(app):
    """Planted must-fail: a new POST on the viewer floor is reported."""
    from fastapi import Depends

    from CortexOS.security.auth_port import require_spend_auth

    @app.post("/planted/t263/viewer", dependencies=[Depends(require_spend_auth("viewer"))])
    async def planted_viewer() -> dict[str, Any]:
        return {}

    problems = _write_floor_problems(app)
    assert "write route on viewer floor: POST /planted/t263/viewer" in problems


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/routines/pause-all"),
        ("POST", "/api/routines/resume-all"),
        ("POST", "/api/routines/tick"),
        ("POST", "/api/engine/telemetry/compact"),
    ],
)
def test_high_impact_routes_refuse_a_steward(app, spies, method, path):
    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.request(method, path, json={}, headers={"X-API-Key": TEST_STEWARD_KEY})
    assert res.status_code == 403, res.text
    assert spies == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/engine/auto"),
        ("POST", "/api/routines/x263/run"),
        ("POST", "/api/routines/x263/fire"),
        ("GET", "/api/engine/activity"),
    ],
)
def test_spend_and_activity_routes_refuse_a_viewer(app, spies, method, path):
    with TestClient(app, raise_server_exceptions=False) as client:
        res = client.request(method, path, json={}, headers={"X-API-Key": TEST_VIEWER_KEY})
    assert res.status_code == 403, res.text
    assert spies == []


def test_demo_viewer_key_reaches_dms_query_and_nothing_else_under_dms(monkeypatch, app, spies):
    """DMS's default credential still reaches /dms/query; the engine port refuses it elsewhere."""
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    demo = {"X-API-Key": "dms-demo-viewer-key", "Authorization": "Bearer dms-demo-viewer-key"}
    with TestClient(app, raise_server_exceptions=False) as client:
        query = client.post("/dms/query", json={"question": "total stock"}, headers=demo)
        preview = client.get("/dms/table-preview?table=inventory", headers=demo)
        audit = client.get("/dms/audit", headers=demo)
    # Past the gate: the answer_question spy raised inside the handler.
    assert query.status_code == 500
    assert "CortexOS.dms.query_service.answer_question" in spies
    assert preview.status_code == 401 and audit.status_code == 401
    assert "CortexOS.dms.warehouse_db.preview_table" not in spies
