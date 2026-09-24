"""T2-DMS (#263): the DMS query, chat, warehouse and task routes are role-gated.

Gated through the engine auth port (``CortexOS.security.auth_port``), the same
way TRUST-01 gates ``/api/apps``. Reads need viewer; anything that writes a
store or the ledger needs steward; overriding the capacity gate on
confirm-dims is an approval and needs admin. The actor and approver recorded
are the authenticated caller, never a name taken from the request body.

Assertions are on what the HTTP caller gets back (status and body) and on the
stored state (the ops ledger, the changelog file, the task-event table), with
a spy on every backend call to prove a refused request never reached it.
"""

from __future__ import annotations

import base64
import importlib
import io
import json
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from CortexOS.security import auth_port

KEYS = {"viewer": "t2dms-viewer-key", "steward": "t2dms-steward-key", "admin": "t2dms-admin-key"}
_KEY_ENV = ";".join(f"{role}:{key}" for role, key in KEYS.items())
_ROLE_ORDER = ("viewer", "steward", "admin")
_FORGED = "mallory"


def _hdr(role: str | None) -> dict[str, str]:
    return {"X-API-Key": KEYS[role]} if role else {}


def _below(min_role: str) -> list[str]:
    return list(_ROLE_ORDER[: _ROLE_ORDER.index(min_role)])


def _at_or_above(min_role: str) -> list[str]:
    return list(_ROLE_ORDER[_ROLE_ORDER.index(min_role) :])


def _jpeg_b64() -> str:
    img = Image.new("RGB", (32, 32), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_VERDICT = types.SimpleNamespace(status="pass", violations=[], executable=True, event_id="ev-spy")


def _spied(name: str, calls: list[tuple[str, tuple, dict]], result: Any):
    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append((name, args, kwargs))
        return result(*args, **kwargs) if callable(result) else result

    return _spy


def _base_env(monkeypatch, tmp_path) -> dict[str, Any]:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    monkeypatch.setenv("DMS_API_KEYS", _KEY_ENV)
    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    # These tests send a few hundred /dms requests; the per-IP bucket is not under test.
    from packs.dms.security import rate_limit

    rate_limit.reset_limiter(1_000_000)

    import CortexOS.config as cortex_config
    from CortexOS.dms import entry_analyser, query_service

    monkeypatch.setattr(cortex_config, "_cached_config", None)
    monkeypatch.setattr(query_service, "CHANGELOG_PATH", tmp_path / "propose_changelog.jsonl")
    monkeypatch.setattr(entry_analyser, "CLEAN_CSV", tmp_path / "inventory_clean.csv")
    monkeypatch.setattr(entry_analyser, "CHANGELOG_PATH", tmp_path / "entry_changelog.jsonl")

    # Seed real rows so path parameters resolve: two bins and one item.
    from packs.dms.vision import intake, locations

    bin_a = locations.build_location(kind="bin", code="BIN-A", capacity_volume=1.0, db_path=db)
    bin_b = locations.build_location(kind="bin", code="BIN-B", db_path=db)
    item = intake.intake_item(
        sku="SKU-T2", label="seed", location_code="BIN-A", photo_b64=_jpeg_b64(),
        actor="seed", db_path=db,
    )["item"]
    return {"db": db, "tmp": tmp_path, "bin_a": bin_a, "bin_b": bin_b, "item": item}


def _reset_rate_limit() -> None:
    from packs.dms.security import rate_limit

    rate_limit.reset_limiter()


def _ledger(db: Path) -> list[tuple[str, str]]:
    from packs.dms.audit.ledger import list_entries

    return [(e.actor, e.event_type) for e in list_entries(db_path=db, limit=1000)]


@pytest.fixture
def env(monkeypatch, tmp_path):
    """App with every backend the routes call replaced by a recording spy."""
    state = _base_env(monkeypatch, tmp_path)
    calls: list[tuple[str, tuple, dict]] = []

    from CortexOS.dms import entry_analyser, query_service, warehouse_db
    from packs.dms.chat import threads
    from packs.dms.tasks import gate

    # ``packs.dms.tasks`` re-exports a ``suggest`` function that shadows the module.
    suggest = importlib.import_module("packs.dms.tasks.suggest")
    from packs.dms.vision import dimension, intake, locations, movement, space, warehouse_store

    spec: list[tuple[Any, str, Any]] = [
        (query_service, "answer_question", {
            "answer": "42 units on hand", "audit_id": "aud-spy", "row_count": 1,
            "rows": [{"sku": "SKU-T2", "qty": 42}], "query_source": "spy",
        }),
        (query_service, "list_audit_entries", query_service.list_audit_entries),
        (query_service, "propose_edits", {"proposed_id": "p-spy", "count": 1}),
        (warehouse_db, "table_row_counts", {"inventory": 3}),
        (warehouse_db, "preview_table", ([{"sku": "SKU-T2"}], 1, ["sku"])),
        (entry_analyser, "analyse_raw_entry", entry_analyser.analyse_raw_entry),
        (entry_analyser, "add_inventory_entry", {"ok": True, "row_id": 7}),
        (threads, "create_thread", {"thread": {"id": "th-spy"}}),
        (threads, "append_message", {"message": {"id": "m-spy"}}),
        (threads, "list_messages", [{"id": "m-spy"}]),
        (locations, "build_location", {"id": "loc-spy", "code": "NEW"}),
        (locations, "location_tree_with_items", [{"code": "BIN-A"}]),
        (locations, "render_qr_png", locations.render_qr_png),
        (intake, "intake_item", {"item": {"id": "it-spy"}}),
        (intake, "confirm_item_dims", {"ok": True}),
        (movement, "scan_move", {"ok": True}),
        (dimension, "estimate_dims", types.SimpleNamespace(to_dict=lambda: {"l": 1.0})),
        (space, "location_space", {"free_volume": 1.0}),
        (warehouse_store, "get_location_by_code", warehouse_store.get_location_by_code),
        (gate, "check_task", _VERDICT),
        (gate, "create_task_event", "ev-spy"),
        (gate, "acknowledge_event", _VERDICT),
        (suggest, "record_choice", None),
    ]
    for module, name, result in spec:
        monkeypatch.setattr(module, name, _spied(name, calls, result))

    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        yield {**state, "client": client, "calls": calls}
    _reset_rate_limit()


@pytest.fixture
def real(monkeypatch, tmp_path):
    """App with the real backends, for stored-state assertions."""
    state = _base_env(monkeypatch, tmp_path)
    from CortexOS.api.app import create_app

    with TestClient(create_app()) as client:
        yield {**state, "client": client}
    _reset_rate_limit()


# (method, path template, json body or None, minimum role, backend the handler calls)
def _routes() -> list[tuple[str, str, Any, str, str | None]]:
    return [
        ("POST", "/dms/query", {"question": "how many units of SKU-T2"}, "viewer", "answer_question"),
        ("GET", "/dms/audit", None, "viewer", "list_audit_entries"),
        ("GET", "/dms/tables", None, "viewer", "table_row_counts"),
        ("GET", "/dms/table-preview?table=inventory", None, "viewer", "preview_table"),
        ("POST", "/dms/propose-edit",
         {"changes": [{"field": "qty", "old_val": 1, "new_val": 2}], "approved_by": _FORGED},
         "steward", "propose_edits"),
        ("GET", "/dms/data/clean", None, "viewer", None),
        ("GET", "/dms/changelog", None, "viewer", None),
        ("POST", "/dms/analyse-entry", {"raw_text": "SKU-9 10kg bin A"}, "viewer", "analyse_raw_entry"),
        ("POST", "/dms/add-entry",
         {"proposed": {"sku": "SKU-9", "quantity_kg": 1, "location": "A", "supplier_name": "S"},
          "approved_by": _FORGED},
         "steward", "add_inventory_entry"),
        ("POST", "/dms/threads", {"customer_label": "Co", "actor": _FORGED}, "steward", "create_thread"),
        ("POST", "/dms/threads/th-1/messages",
         {"sender": "customer", "body": "hello", "actor": _FORGED}, "steward", "append_message"),
        ("GET", "/dms/threads/th-1/messages", None, "viewer", "list_messages"),
        ("POST", "/dms/warehouse/locations", {"kind": "bin", "code": "NEW"}, "steward", "build_location"),
        ("GET", "/dms/warehouse/locations/tree", None, "viewer", "location_tree_with_items"),
        ("GET", "/dms/warehouse/locations/{bin_a}/qr-label", None, "viewer", "render_qr_png"),
        ("POST", "/dms/items/intake",
         {"sku": "SKU-X", "label": "x", "location_code": "BIN-A", "photo": _jpeg_b64(), "actor": _FORGED},
         "steward", "intake_item"),
        ("POST", "/dms/movements/scan",
         {"item_qr_or_id": "SKU-T2", "to_location_qr": "q", "actor": _FORGED}, "steward", "scan_move"),
        ("POST", "/dms/items/estimate-dims", {"photo": _jpeg_b64()}, "steward", "estimate_dims"),
        ("POST", "/dms/items/{item}/confirm-dims",
         {"l": 0.1, "w": 0.1, "h": 0.1, "actor": _FORGED}, "steward", "confirm_item_dims"),
        ("POST", "/dms/items/{item}/confirm-dims",
         {"l": 0.1, "w": 0.1, "h": 0.1, "actor": _FORGED, "gate_approved": True},
         "admin", "confirm_item_dims"),
        ("GET", "/dms/locations/{bin_a}/space", None, "viewer", "location_space"),
        ("GET", "/dms/warehouse/locations/by-code/BIN-A", None, "viewer", "get_location_by_code"),
        ("POST", "/dms/tasks/gate/check",
         {"event_id": "e1", "task_id": "t1", "actor": _FORGED}, "steward", "check_task"),
        ("POST", "/dms/tasks/choose", {"task_id": "t1", "actor": _FORGED}, "steward", "create_task_event"),
        ("POST", "/dms/tasks/gate/acknowledge",
         {"event_id": "e1", "actor": _FORGED}, "steward", "acknowledge_event"),
    ]


def _path(env: dict[str, Any], template: str) -> str:
    return template.format(bin_a=env["bin_a"]["id"], item=env["item"]["id"])


def _call(client: TestClient, method: str, path: str, body: Any, role: str | None):
    kwargs: dict[str, Any] = {"headers": _hdr(role)}
    if body is not None:
        kwargs["json"] = body
    return client.request(method, path, **kwargs)


def _assert_no_path(text: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in text
    assert "/home/" not in text and "/tmp/" not in text and ":\\\\" not in text


def _stored(env: dict[str, Any]) -> tuple[Any, ...]:
    tmp = env["tmp"]
    files = tuple(
        (p.name, p.read_bytes() if p.is_file() else None)
        for p in (
            tmp / "propose_changelog.jsonl",
            tmp / "entry_changelog.jsonl",
            tmp / "inventory_clean.csv",
        )
    )
    return (tuple(_ledger(env["db"])), files)


# --- refusals: 401 without a key, 403 below the role, no side effect ----------


def test_every_route_refuses_without_key_or_with_low_role_and_changes_nothing(env):
    client, tmp = env["client"], env["tmp"]
    before = _stored(env)
    for method, template, body, min_role, _spy in _routes():
        path = _path(env, template)
        res = _call(client, method, path, body, None)
        assert res.status_code == 401, (method, path, res.status_code, res.text)
        _assert_no_path(res.text, tmp)
        for role in _below(min_role):
            res = _call(client, method, path, body, role)
            assert res.status_code == 403, (method, path, role, res.status_code, res.text)
            _assert_no_path(res.text, tmp)
    assert env["calls"] == []  # no backend was reached
    assert _stored(env) == before  # ledger, changelog and inventory untouched


def test_every_route_in_these_modules_is_gated():
    """Walk the routes the four modules register; a route added later without the gate fails here."""
    from CortexOS.api.dms_query import register_dms_routes
    from CortexOS.api.task_routes import register_task_routes

    app = FastAPI()
    app.state.pack = types.SimpleNamespace(name="dms")
    register_dms_routes(app)  # also registers the chat and warehouse routes
    register_task_routes(app)
    client = TestClient(app)
    seen: set[tuple[str, str]] = set()
    for path, ops in app.openapi()["paths"].items():
        concrete = (
            path.replace("{thread_id}", "t").replace("{location_id}", "l")
            .replace("{item_id}", "i").replace("{code}", "c").replace("{variant}", "clean")
        )
        for method in sorted(m.upper() for m in ops):
            seen.add((method, path))
            res = client.request(method, concrete, json={})
            assert res.status_code == 401, (method, path, res.status_code)
    # The table above must cover exactly the registered routes, in OpenAPI spelling.
    to_openapi = {
        "{bin_a}": "{location_id}", "{item}": "{item_id}", "/th-1/": "/{thread_id}/",
        "/clean": "/{variant}", "/BIN-A": "/{code}",
    }
    listed = set()
    for method, template, *_ in _routes():
        path = template.split("?")[0]
        for concrete, param in to_openapi.items():
            path = path.replace(concrete, param)
        listed.add((method, path))
    assert seen == listed, seen ^ listed


def test_gate_override_on_confirm_dims_needs_admin_and_writes_nothing_for_steward(real):
    client, db = real["client"], real["db"]
    item_id = real["item"]["id"]
    before = _ledger(db)
    oversize = {"l": 2.0, "w": 2.0, "h": 2.0, "gate_approved": True, "actor": _FORGED}
    res = client.post(f"/dms/items/{item_id}/confirm-dims", json=oversize, headers=_hdr("steward"))
    assert res.status_code == 403, res.text
    assert _ledger(db) == before
    from packs.dms.vision.warehouse_store import get_item_by_id_or_qr

    assert get_item_by_id_or_qr(item_id, db_path=db).dims is None

    res = client.post(f"/dms/items/{item_id}/confirm-dims", json=oversize, headers=_hdr("admin"))
    assert res.status_code == 200, res.text
    assert get_item_by_id_or_qr(item_id, db_path=db).dims is not None
    assert _ledger(db)[-1] == ("api_admin", "item.dimensioned")


# --- no false positive: a sufficient role sees today's behaviour --------------


def _baseline(monkeypatch, fn):
    """Run ``fn`` with auth disabled: the response the route gave before the gate."""
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")
    try:
        return fn()
    finally:
        monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)


def _body(res: Any) -> Any:
    if "application/json" in res.headers.get("content-type", ""):
        return res.json()
    return res.content


def test_every_route_answers_a_sufficient_role_exactly_as_before(env, monkeypatch):
    client = env["client"]
    for method, template, body, min_role, spy in _routes():
        path = _path(env, template)
        want = _baseline(
            monkeypatch, lambda m=method, p=path, b=body: _call(client, m, p, b, None)
        )
        assert want.status_code == 200, (method, path, want.status_code, want.text)
        for role in _at_or_above(min_role):
            env["calls"].clear()
            got = _call(client, method, path, body, role)
            assert got.status_code == want.status_code, (method, path, role, got.text)
            assert _body(got) == _body(want), (method, path, role)
            if spy is not None:
                assert spy in [c[0] for c in env["calls"]], (method, path, role)


def test_query_answer_and_rows_reach_a_viewer(env):
    res = env["client"].post(
        "/dms/query", json={"question": "how many units of SKU-T2"}, headers=_hdr("viewer")
    )
    assert res.status_code == 200
    out = res.json()
    assert out["answer"] == "42 units on hand"
    assert out["rows"] == [{"sku": "SKU-T2", "qty": 42}]
    assert out["audit_id"] == "aud-spy"


def test_bearer_header_is_accepted(env):
    res = env["client"].get(
        "/dms/warehouse/locations/tree", headers={"Authorization": f"Bearer {KEYS['viewer']}"}
    )
    assert res.status_code == 200 and res.json() == {"tree": [{"code": "BIN-A"}]}


# --- actor and approver come from the caller, not the body --------------------


def _actor_of(call: tuple[str, tuple, dict]) -> Any:
    name, args, kwargs = call
    if name in ("propose_edits", "add_inventory_entry"):
        return kwargs["approved_by"]
    if name == "record_choice":
        return args[2]
    return kwargs["actor"]


def test_backends_receive_the_caller_not_the_forged_body_name(env):
    client = env["client"]
    actor_routes = [r for r in _routes() if r[0] == "POST" and isinstance(r[2], dict)
                    and (r[2].get("actor") == _FORGED or r[2].get("approved_by") == _FORGED)]
    assert len(actor_routes) >= 10
    for method, template, body, min_role, _spy in actor_routes:
        for role in _at_or_above(min_role):
            env["calls"].clear()
            res = _call(client, method, _path(env, template), body, role)
            assert res.status_code == 200, (template, role, res.text)
            actors = {_actor_of(c) for c in env["calls"]
                      if c[0] not in ("get_location_by_code", "list_audit_entries")}
            assert actors == {f"api_{role}"}, (template, role, env["calls"])


def test_stored_chat_warehouse_and_task_actors_are_the_caller(real):
    client, db = real["client"], real["db"]
    steward = _hdr("steward")
    start = len(_ledger(db))

    thread = client.post("/dms/threads", json={"customer_label": "Co", "actor": _FORGED}, headers=steward)
    assert thread.status_code == 200
    thread_id = thread.json()["thread"]["id"]
    msg = client.post(
        f"/dms/threads/{thread_id}/messages",
        json={"sender": "customer", "body": "stock for SKU-T2?", "actor": _FORGED},
        headers=steward,
    )
    assert msg.status_code == 200
    listed = client.get(f"/dms/threads/{thread_id}/messages", headers=_hdr("viewer"))
    assert [m["body"] for m in listed.json()["messages"]] == ["stock for SKU-T2?"]

    moved = client.post(
        "/dms/movements/scan",
        json={"item_qr_or_id": "SKU-T2", "to_location_qr": real["bin_b"]["qr_token"], "actor": _FORGED},
        headers=steward,
    )
    assert moved.status_code == 200 and moved.json()["to_location_code"] == "BIN-B"

    chosen = client.post(
        "/dms/tasks/choose",
        json={"task_id": "reorder", "filled_template": {}, "actor": _FORGED},
        headers=steward,
    )
    assert chosen.status_code == 200
    acked = client.post(
        "/dms/tasks/gate/acknowledge",
        json={"event_id": chosen.json()["event_id"], "actor": _FORGED},
        headers=_hdr("admin"),
    )
    assert acked.status_code == 200

    written = _ledger(db)[start:]
    assert len(written) >= 6
    assert _FORGED not in {actor for actor, _ in written}
    by_event = {event: actor for actor, event in written}
    assert by_event["thread.created"] == "api_steward"
    assert by_event["item.moved"] == "api_steward"
    assert by_event["task.event_created"] == "api_steward"
    assert written[-1][0] == "api_admin"  # the acknowledgement is the admin's


class _NoWarehouseWrite:
    """Stands in for the DuckDB warehouse so add-entry never touches the shared file."""

    def __init__(self) -> None:
        self.inserts: list[list[Any]] = []

    def execute(self, _sql: str, params: list[Any]) -> None:
        self.inserts.append(params)

    def close(self) -> None:
        return None


def test_stored_changelog_approver_is_the_caller(real, monkeypatch):
    client, tmp = real["client"], real["tmp"]
    from CortexOS.dms import entry_analyser

    warehouse = _NoWarehouseWrite()
    monkeypatch.setattr(entry_analyser, "get_connection", lambda _path: warehouse)
    res = client.post(
        "/dms/propose-edit",
        json={"changes": [{"field": "qty", "old_val": 1, "new_val": 2}], "approved_by": _FORGED},
        headers=_hdr("steward"),
    )
    assert res.status_code == 200 and res.json()["count"] == 1
    stored = [json.loads(line) for line in (tmp / "propose_changelog.jsonl").read_text().splitlines()]
    assert [e["approved_by"] for e in stored] == ["api_steward"]

    res = client.post(
        "/dms/add-entry",
        json={"proposed": {"sku": "SKU-9", "quantity_kg": 1, "location": "A", "supplier_name": "S"},
              "approved_by": _FORGED},
        headers=_hdr("admin"),
    )
    assert res.status_code == 200, res.text
    entries = [json.loads(line) for line in (tmp / "entry_changelog.jsonl").read_text().splitlines()]
    assert entries and {e["approved_by"] for e in entries} == {"api_admin"}
    assert "SKU-9" in (tmp / "inventory_clean.csv").read_text()
    assert [row[0] for row in warehouse.inserts] == ["SKU-9"]


# --- the port fails closed on these routes too ---------------------------------


@pytest.fixture
def swap_authorizer():
    saved = auth_port.registered_authorizer()
    yield
    if saved is not None:
        auth_port.register_authorizer(saved)
    else:
        auth_port.clear_authorizer()


def test_no_registered_authorizer_refuses_even_an_admin(env, monkeypatch, swap_authorizer):
    monkeypatch.setattr(auth_port, "_load_active_pack", lambda: None)
    auth_port.clear_authorizer()
    before = _stored(env)
    for method, template, body, _min, _spy in _routes():
        res = _call(env["client"], method, _path(env, template), body, "admin")
        assert res.status_code == 503, (template, res.status_code)
        assert res.json() == {"detail": auth_port.NO_AUTHORIZER_DETAIL}
    assert env["calls"] == []
    assert _stored(env) == before
