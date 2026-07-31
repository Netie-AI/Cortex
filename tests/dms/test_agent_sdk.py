"""O4 Agent SDK — visibility-scoped reads, action-gated writes, pack-agnostic engine.

Proves the four properties that make this "the SDK":
  1. reads never expose agent-invisible (PII) properties — schema or data or filters;
  2. writes only happen through registered tool action types, role- and confirm-gated,
     with denials AND executions ledgered (F1);
  3. api_auth.Caller duck-types as the actor (one identity shape end-to-end);
  4. the engine surface is pack-agnostic — a synthetic pack dir works without DMS.
"""

from __future__ import annotations

import pytest

from CortexOS.agent_sdk import AgentActor, SdkDenied, call_action, list_object_types, query_objects
from packs.dms.agents import sdk as dms_sdk

VIEWER = AgentActor(actor="agent_viewer", role="viewer")
STEWARD = AgentActor(actor="agent_steward", role="steward")


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    """Tmp DuckDB warehouse built from the tracked sample CSVs (CI-safe)."""
    from CortexOS.dms.warehouse_db import load_inventory_csv

    db = tmp_path_factory.mktemp("wh") / "wh.duckdb"
    load_inventory_csv(db_path=db)
    return db


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    db = tmp_path / "ops.db"
    monkeypatch.delenv("DMS_LEDGER_DSN", raising=False)
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    monkeypatch.setenv("PACK", "dms")
    return db


# -- 1. visibility ----------------------------------------------------------

def test_schema_listing_strips_hidden_properties():
    suppliers = {o.id: o for o in dms_sdk.list_object_types()}["suppliers"]
    names = {p.name for p in suppliers.properties}
    assert {"email", "phone", "contact_person"}.isdisjoint(names)
    assert "supplier_name" in names


def test_query_returns_only_visible_columns(warehouse):
    rows = dms_sdk.query_objects("suppliers", actor=VIEWER, limit=5, db_path=warehouse)
    assert rows, "sample warehouse should have suppliers"
    for row in rows:
        assert {"email", "phone", "contact_person"}.isdisjoint(row)
        assert "supplier_name" in row


def test_filters_work_and_hidden_filter_is_refused(warehouse):
    all_rows = dms_sdk.query_objects("inventory", actor=VIEWER, limit=500, db_path=warehouse)
    category = all_rows[0]["category"]
    filtered = dms_sdk.query_objects(
        "inventory", {"category": category}, actor=VIEWER, limit=500, db_path=warehouse
    )
    assert filtered and all(r["category"] == category for r in filtered)
    assert len(filtered) <= len(all_rows)

    with pytest.raises(SdkDenied) as exc:  # membership probe on hidden PII
        dms_sdk.query_objects("suppliers", {"email": "x@y.com"}, actor=VIEWER, db_path=warehouse)
    assert exc.value.verdict == "filter_hidden"


def test_unknown_object_or_property(warehouse):
    with pytest.raises(SdkDenied) as e1:
        dms_sdk.query_objects("nope", actor=VIEWER, db_path=warehouse)
    assert e1.value.verdict == "not_found"
    with pytest.raises(SdkDenied) as e2:
        dms_sdk.query_objects("inventory", {"nope": 1}, actor=VIEWER, db_path=warehouse)
    assert e2.value.verdict == "not_found"


# -- 2. governed writes -----------------------------------------------------

def _denials(db):
    from packs.dms.audit.ledger import list_entries

    return list_entries(db_path=db, event_type="action.tool_call_denied")


def test_viewer_call_action_rbac_denied_and_ledgered(ledger_db):
    with pytest.raises(SdkDenied) as exc:
        dms_sdk.call_action("export_pptx", {"title": "T"}, actor=VIEWER, db_path=ledger_db)
    assert exc.value.verdict == "rbac"
    events = _denials(ledger_db)
    assert events and events[-1].payload["verdict"] == "rbac"
    assert events[-1].actor == "agent_viewer"


def test_steward_needs_confirm_then_agent_apply_denied(ledger_db, tmp_path, monkeypatch):
    from netie.execution.tool_runner import ToolCallError

    monkeypatch.setattr("netie.execution.tool_runner.OUTPUTS", tmp_path / "outputs")
    with pytest.raises(SdkDenied) as exc:  # export_pptx is requires_confirm in the registry
        dms_sdk.call_action("export_pptx", {"title": "T"}, actor=STEWARD, db_path=ledger_db)
    assert exc.value.verdict == "confirm_required"

    with pytest.raises(ToolCallError) as exc2:  # C5: agents may not invoke apply-class tools
        dms_sdk.call_action(
            "export_pptx", {"title": "Q3 Stock"}, actor=STEWARD, confirmed=True,
            run_id="sdkrun1", db_path=ledger_db,
        )
    assert exc2.value.verdict == "agent_apply_denied"


def test_event_kind_not_invocable_and_unregistered(ledger_db):
    with pytest.raises(SdkDenied) as e1:  # item.intake is a registered EVENT, not a tool
        dms_sdk.call_action("item.intake", {"sku": "X"}, actor=STEWARD, db_path=ledger_db)
    assert e1.value.verdict == "not_invocable"
    with pytest.raises(SdkDenied) as e2:
        dms_sdk.call_action("rm_rf", {}, actor=STEWARD, db_path=ledger_db)
    assert e2.value.verdict == "unregistered"
    verdicts = [e.payload["verdict"] for e in _denials(ledger_db)]
    assert verdicts == ["not_invocable", "unregistered"]


# -- 3. identity ------------------------------------------------------------

def test_api_auth_caller_duck_types(warehouse):
    from packs.dms.security.api_auth import Caller

    rows = dms_sdk.query_objects(
        "locations", actor=Caller(role="viewer", actor="api_viewer"), limit=3, db_path=warehouse
    )
    assert rows and "location_code" in rows[0]


def test_bad_actor_rejected(warehouse):
    with pytest.raises(SdkDenied) as exc:
        dms_sdk.query_objects("inventory", actor="just-a-string", db_path=warehouse)
    assert exc.value.verdict == "bad_actor"
    with pytest.raises(SdkDenied):
        dms_sdk.query_objects("inventory", actor=AgentActor("x", role="root"), db_path=warehouse)


# -- 4. pack-agnostic engine ------------------------------------------------

FAKE_OBJECTS = """
object_types:
  - id: account
    description: "A CRM account."
    primary_key: account_id
    properties:
      - {name: account_id, type: string, agent_visible: true}
      - {name: account_name, type: string, agent_visible: true}
      - {name: owner_email, type: string, agent_visible: false}
"""
FAKE_ACTIONS = """
action_types:
  - id: crm.export
    kind: tool
    tool_class: apply
    description: "Export accounts."
    required_role: admin
    ledger_event_type: action.tool_call
    requires_confirm: false
    params: [title]
"""


@pytest.fixture
def fake_pack(tmp_path):
    onto = tmp_path / "crm" / "ontology"
    onto.mkdir(parents=True)
    (onto / "object_types.yaml").write_text(FAKE_OBJECTS, encoding="utf-8")
    (onto / "action_types.yaml").write_text(FAKE_ACTIONS, encoding="utf-8")
    (onto / "link_types.yaml").write_text("link_types: []\n", encoding="utf-8")
    (onto / "functions.yaml").write_text("functions: []\n", encoding="utf-8")
    return tmp_path / "crm"


def test_engine_serves_a_non_dms_pack(fake_pack):
    objs = list_object_types(pack_dir=fake_pack)
    assert [o.id for o in objs] == ["account"]
    assert {p.name for p in objs[0].properties} == {"account_id", "account_name"}  # hidden stripped

    from CortexOS.ontology.registry import compile_to_sqlite

    counts = compile_to_sqlite(fake_pack, fake_pack / "ops.db")
    assert counts == {
        "object_types": 1, "properties": 3, "link_types": 0, "action_types": 1, "functions": 0,
    }


def test_non_dms_pack_rbac_from_its_own_registry(fake_pack, ledger_db):
    # crm.export requires admin per the fake pack's registry — steward is refused.
    with pytest.raises(SdkDenied) as exc:
        call_action("crm.export", {"title": "x"}, actor=STEWARD, pack_dir=fake_pack, db_path=ledger_db)
    assert exc.value.verdict == "rbac"


def test_unregistered_backend_pack_fails_closed(fake_pack):
    with pytest.raises(LookupError):
        query_objects("account", actor=VIEWER, pack_dir=fake_pack)
