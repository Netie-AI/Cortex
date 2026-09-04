"""Constructor graph compile + key-gated /cortex mount."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from CortexOS.constructor_graph import (
    ConstructorGraphError,
    compile_constructor_graph,
    describe_constructor_node,
    generate_constructor_graph,
)
from packs.dms.security.rate_limit import reset_limiter

SAMPLE = {
    "nodes": [
        {"id": "n1", "kind": "ingest", "x": 48, "y": 72, "note": "Read operations into the graph."},
        {"id": "n2", "kind": "hypothesize", "x": 280, "y": 72, "note": "Surface a testable claim."},
        {"id": "n3", "kind": "improve", "x": 512, "y": 72, "note": "Change a product from the claim."},
        {"id": "n4", "kind": "audit", "x": 280, "y": 220, "note": "Show why this node exists."},
    ],
    "edges": [
        {"from": "n1", "to": "n2"},
        {"from": "n2", "to": "n3"},
        {"from": "n3", "to": "n4"},
    ],
}


@pytest.fixture
def api_keys_env(monkeypatch):
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.delenv("CONSTRUCTOR_SKIN_DIR", raising=False)
    return {"viewer": "sk-viewer-test"}


@pytest.fixture
def dms_client(api_keys_env, tmp_path, monkeypatch):
    reset_limiter(per_minute=120)
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_compile_sample_has_exactly_one_emit():
    program = compile_constructor_graph(SAMPLE)
    assert [n.id for n in program.nodes] == ["n1", "n2", "n3", "n4"]
    assert program.nodes[1].inputs == ["n1"]
    assert program.entry_node_id == "n1"
    assert program.output_node_id == "n4"
    from netie.fabrication.dsl_parser import NodeType

    emits = [n for n in program.nodes if n.type == NodeType.EMIT]
    assert len(emits) == 1


def test_generate_inventory_pptx_is_foundry_path():
    graph = generate_constructor_graph("export inventory and suppliers as pptx")
    assert graph["ok"] is True
    assert graph["pattern"] == "orchestrator_subagent"
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds[:5] == ["connector", "ontology", "insight", "foundry", "app"]
    assert kinds[-1] == "tool_call"
    assert graph["nodes"][0]["object_type"] == "inventory"
    assert graph["nodes"][1]["object_type"] == "suppliers"
    assert graph["edges"][0] == {"from": "g1", "to": "g2"}
    compile_constructor_graph(graph)


def test_generate_venue_crm_is_place_venue_contact_lead():
    """Skin Place-Venue-Contact-Lead must compile on the engine, not only locally."""
    graph = generate_constructor_graph("nearby clubs and customer contacts")
    assert graph["ok"] is True
    assert graph["pattern"] == "orchestrator_subagent"
    assert graph["objects"][:4] == ["places", "venues", "contacts", "leads"]
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds[0] == "ingest"
    assert "ontology" in kinds
    assert "insight" in kinds
    assert kinds[-1] == "tool_call"
    fetches = {n.get("fetch_from") for n in graph["nodes"] if n.get("fetch_from")}
    assert "maps.places" in fetches
    assert "maps.venues" in fetches or "crm.contacts" in fetches
    ontology = next(n for n in graph["nodes"] if n["kind"] == "ontology")
    assert "venue_at_place" in ontology["note"]
    insight = next(n for n in graph["nodes"] if n["kind"] == "insight")
    assert "leads from contacts" in insight["note"]
    compile_constructor_graph(graph)


def test_generate_define_data_uses_ontology_not_palantir_product():
    graph = generate_constructor_graph("define data for inventory")
    assert graph["ok"] is True
    kinds = [n["kind"] for n in graph["nodes"]]
    assert "ontology" in kinds
    assert "insight" in kinds
    compile_constructor_graph(graph)


def test_generate_govern_agents_is_checked_not_palantir_product():
    """Govern-agents is Cortex agent.checked. P1 parked. Ontology stays Cortex."""
    graph = generate_constructor_graph("govern agents on inventory")
    assert graph["ok"] is True
    assert graph["action"] == "agent.checked"
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds == ["ingest", "connector", "ontology", "agent", "audit"]
    assert "foundry" not in kinds
    compile_constructor_graph(graph)


def test_generate_business_insights_stays_foundry_path():
    graph = generate_constructor_graph("business insights on inventory")
    assert graph["ok"] is True
    kinds = [n["kind"] for n in graph["nodes"]]
    assert "ontology" in kinds
    assert "insight" in kinds
    assert "foundry" in kinds
    compile_constructor_graph(graph)


def test_generate_understand_company_is_client_intake_not_palantir_product():
    graph = generate_constructor_graph("understand this company")
    assert graph["ok"] is True
    kinds = [n["kind"] for n in graph["nodes"]]
    assert kinds == [
        "ingest",
        "connector",
        "ontology",
        "insight",
        "foundry",
        "app",
        "tool_call",
    ]
    assert graph["action"] == "export_pptx"
    ontology = next(n for n in graph["nodes"] if n["kind"] == "ontology")
    assert "semantic_layer.yaml" in ontology["note"]
    assert "P1 parked" in ontology["note"]
    compile_constructor_graph(graph)


def test_generate_refuses_stalk_prompt():
    with pytest.raises(ConstructorGraphError, match="Refused"):
        generate_constructor_graph("stalk this person and scrape the internet")


def test_generate_verify_is_generator_verifier():
    graph = generate_constructor_graph("verify supplier risk")
    assert graph["pattern"] == "generator_verifier"
    assert [n["kind"] for n in graph["nodes"]] == ["connector", "hypothesize", "audit"]
    assert graph["nodes"][0]["object_type"] == "suppliers"


def test_describe_decision_layer_marks_emit():
    graph = generate_constructor_graph("create a foundry app from inventory")
    layer = describe_constructor_node(graph, "g5")
    assert layer["is_emit"] is True
    assert layer["cortex_kind"] == "EMIT"
    assert layer["constructor_kind"] == "app"


def test_compile_rejects_unknown_kind():
    with pytest.raises(ConstructorGraphError):
        compile_constructor_graph({"nodes": [{"id": "x", "kind": "n8n"}], "edges": []})


def test_compile_enhance_kind_is_document_ref():
    from netie.fabrication.dsl_parser import NodeType

    program = compile_constructor_graph(
        {
            "nodes": [
                {
                    "id": "e1",
                    "kind": "enhance",
                    "object_type": "images",
                    "action_type": "image.enhance",
                    "fetch_from": "local.model",
                },
                {"id": "a1", "kind": "app"},
            ],
            "edges": [{"from": "e1", "to": "a1"}],
        }
    )
    by_id = {n.id: n for n in program.nodes}
    assert by_id["e1"].type == NodeType.DOCUMENT_REF
    assert by_id["e1"].annotations["constructor_kind"] == "enhance"
    assert by_id["a1"].type == NodeType.EMIT


def test_compile_ontology_fields_land_on_the_dag():
    program = compile_constructor_graph(
        {
            "nodes": [
                {
                    "id": "c1",
                    "kind": "connector",
                    "object_type": "inventory",
                    "data_point": "sku",
                    "data_type": "string",
                    "fetch_from": "warehouse.inventory",
                    "tier": "T1",
                    "stream": False,
                },
                {
                    "id": "t1",
                    "kind": "tool_call",
                    "action_type": "export_pptx",
                    "object_type": "inventory",
                    "tier": "T0",
                },
                {"id": "a1", "kind": "app"},
            ],
            "edges": [{"from": "c1", "to": "t1"}, {"from": "t1", "to": "a1"}],
        }
    )
    by_id = {n.id: n for n in program.nodes}
    assert by_id["c1"].annotations["object_type"] == "inventory"
    assert by_id["c1"].annotations["data_point"] == "sku"
    assert by_id["c1"].annotations["fetch_from"] == "warehouse.inventory"
    assert by_id["c1"].context_key == "c1"
    assert by_id["t1"].tool_name == "export_pptx"
    assert by_id["t1"].default_tier is not None
    assert program.output_node_id == "a1"


def test_event_action_is_not_an_f8_tool():
    from netie.fabrication.dsl_parser import NodeType

    program = compile_constructor_graph(
        {
            "nodes": [
                {"id": "t1", "kind": "tool_call", "action_type": "item.intake"},
                {"id": "a1", "kind": "app"},
            ],
            "edges": [{"from": "t1", "to": "a1"}],
        }
    )
    by_id = {n.id: n for n in program.nodes}
    assert by_id["t1"].type == NodeType.DOCUMENT_REF
    assert by_id["t1"].tool_name is None
    assert by_id["t1"].annotations["action_type"] == "item.intake"


def test_login_open_constructor_redirects_without_key(dms_client):
    login = dms_client.get("/cortex/login")
    assert login.status_code == 200
    assert "OpenVault" in login.text
    assert "DMS_API_KEYS" in login.text
    assert "Generate OpenVault key" in login.text
    assert "restart the openmw console" in login.text
    assert "second listener" in login.text
    bare = dms_client.get("/cortex/constructor/", follow_redirects=False)
    assert bare.status_code == 303
    assert "/cortex/login" in bare.headers["location"]


def test_vendored_skin_is_served_without_laptop_path(dms_client, api_keys_env):
    from CortexOS.paths import constructor_skin_dir

    skin = constructor_skin_dir()
    assert (skin / "index.html").is_file()
    assert (skin / "engine.js").is_file()
    res = dms_client.get(
        "/cortex/constructor/engine.js",
        headers={"X-API-Key": api_keys_env["viewer"]},
        follow_redirects=False,
    )
    assert res.status_code == 200
    assert b"cortexOrigin" in res.content
    denied = dms_client.get("/cortex/constructor/", follow_redirects=False)
    assert denied.status_code == 303
    ok = dms_client.get(
        "/cortex/constructor/",
        headers={"X-API-Key": api_keys_env["viewer"]},
        follow_redirects=False,
    )
    assert ok.status_code == 200
    assert b"id=\"stage\"" in ok.content


def test_run_401_without_key(dms_client):
    res = dms_client.post("/cortex/constructor/run", json=SAMPLE)
    assert res.status_code == 401


def test_ghost_compiles_with_viewer_key(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/ghost",
        json=SAMPLE,
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["ghost"] is True
    assert body["output_node_id"] == "n4"
    assert len(body["nodes"]) == 4


def test_ghost_401_without_key(dms_client):
    res = dms_client.post("/cortex/constructor/ghost", json=SAMPLE)
    assert res.status_code == 401


def test_ghost_does_not_call_run_dag_or_fetch(dms_client, api_keys_env, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("ghost dry-run must not fetch or run_dag")

    monkeypatch.setattr("packs.dms.constructor_fetch.fetch_slice", boom)
    monkeypatch.setattr("netie.execution.dag_runner.run_dag", boom)
    res = dms_client.post(
        "/cortex/constructor/ghost",
        json=SAMPLE,
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ghost"] is True
    assert "fetches" not in body
    assert "run_id" not in body


def test_skin_ghost_honesty_does_not_claim_live_on_404():
    from CortexOS.paths import constructor_skin_dir

    skin = constructor_skin_dir()
    engine = (skin / "engine.js").read_text(encoding="utf-8")
    readme = (skin / "README.md").read_text(encoding="utf-8")
    assert "if (!res.ok)" in engine
    assert "status: res.status" in engine
    assert "ok: false" in engine
    assert "remote && remote.ok" in engine
    assert "Cortex ghost compile ok" in engine
    assert "Cortex ghost blocked" in engine
    assert "No internal fallback" in engine
    assert "cortex-shaped compiler" in engine.lower()
    assert "governed cortex app" in engine.lower()
    assert "app.netie.ai/cortex" in readme
    assert "404" in readme
    assert "do not claim live" in readme.lower()
    assert "do not clone n8n" in readme.lower()


def test_recommend_returns_three_live_patterns(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/recommend",
        json=SAMPLE,
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    ids = {row["id"] for row in body["approaches"]}
    assert ids == {"single_agent", "generator_verifier", "orchestrator_subagent"}
    assert body["recommendation"]["pattern"] == "generator_verifier"


FOUNDRY = {
    "nodes": [
        {"id": "c1", "kind": "connector"},
        {"id": "o1", "kind": "ontology"},
        {"id": "i1", "kind": "insight"},
        {"id": "f1", "kind": "foundry"},
        {"id": "a1", "kind": "app"},
        {"id": "g1", "kind": "audit"},
    ],
    "edges": [
        {"from": "c1", "to": "o1"},
        {"from": "o1", "to": "i1"},
        {"from": "i1", "to": "f1"},
        {"from": "f1", "to": "a1"},
        {"from": "f1", "to": "g1"},
    ],
}


def test_compile_foundry_path_emits_the_app():
    program = compile_constructor_graph(FOUNDRY)
    assert program.output_node_id == "a1"
    from netie.fabrication.dsl_parser import NodeType

    emits = [n for n in program.nodes if n.type == NodeType.EMIT]
    assert [n.id for n in emits] == ["a1"]


def test_generate_route_compiles_chat(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/generate",
        json={"prompt": "export inventory as pptx"},
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert len(body["nodes"]) >= 5
    assert body["edges"]


def test_catalog_route_returns_semantic_layer(dms_client, api_keys_env):
    denied = dms_client.get("/cortex/constructor/catalog")
    assert denied.status_code == 401
    res = dms_client.get(
        "/cortex/constructor/catalog",
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body.get("badge") == "catalog"
    assert "Certified questions" in (body.get("answer") or "")


def test_decision_route_exposes_cortex_layer(dms_client, api_keys_env):
    graph = generate_constructor_graph("export inventory as pptx")
    res = dms_client.post(
        "/cortex/constructor/decision",
        json={"node_id": "g5", "nodes": graph["nodes"], "edges": graph["edges"]},
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["is_emit"] is True
    assert body["recommendation"]["pattern"]


def test_recommend_foundry_picks_orchestrator(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/recommend",
        json=FOUNDRY,
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["recommendation"]["pattern"] == "orchestrator_subagent"


def test_run_compiles_with_viewer_key(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/run",
        json=SAMPLE,
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body["nodes"]) == {"n1", "n2", "n3", "n4"}
    assert body["actor"] == "api_viewer"


def test_ontology_catalog_is_live_yaml(dms_client, api_keys_env):
    res = dms_client.get(
        "/cortex/constructor/ontology",
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert "sku" in body["objects"]["inventory"]["points"]
    assert "export_pptx" in body["actions"]
    assert "warehouse.inventory" in body["fetch_places"]
    assert "maps.venues" in body["fetch_places"]
    assert "crm.contacts" in body["fetch_places"]


def test_fetch_reads_warehouse_inventory(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/fetch",
        json={
            "nodes": [
                {
                    "id": "c1",
                    "kind": "connector",
                    "object_type": "inventory",
                    "data_point": "sku",
                    "fetch_from": "warehouse.inventory",
                }
            ],
            "edges": [],
        },
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    slice_ = body["slice"]
    assert slice_["table"] == "inventory"
    assert slice_["data_point"] == "sku"
    assert slice_["error"] is None
    assert isinstance(slice_["rows"], list)
    assert slice_["row_count"] >= 1


def test_fetch_ghost_venue_is_named_not_invented(dms_client, api_keys_env):
    """Place/Venue compile places have no DuckDB table. Absence is named."""
    res = dms_client.post(
        "/cortex/constructor/fetch",
        json={
            "nodes": [
                {
                    "id": "c1",
                    "kind": "connector",
                    "object_type": "venues",
                    "data_point": "venue_id",
                    "fetch_from": "maps.venues",
                }
            ],
            "edges": [],
        },
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    slice_ = res.json()["slice"]
    assert slice_["table"] is None
    assert slice_["rows"] == []
    assert "ghost place" in (slice_["error"] or "")
    assert "maps.venues" in (slice_["error"] or "")
    assert slice_.get("row_count", 0) == 0


def test_run_seeds_fetch_onto_document_ref(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/constructor/run",
        json={
            "nodes": [
                {
                    "id": "c1",
                    "kind": "connector",
                    "object_type": "inventory",
                    "data_point": "sku",
                    "fetch_from": "warehouse.inventory",
                },
                {"id": "a1", "kind": "app"},
            ],
            "edges": [{"from": "c1", "to": "a1"}],
        },
        headers={"X-API-Key": api_keys_env["viewer"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["ok"] is True
    assert body["fetches"]["c1"]["table"] == "inventory"
    assert body["fetches"]["c1"]["row_count"] >= 1
    out = body["nodes"]["c1"]["output"]
    assert out["table"] == "inventory"
    assert isinstance(out["rows"], list)
    assert out["rows"]


def test_session_rejects_broken_json_with_400(dms_client):
    res = dms_client.post(
        "/cortex/session",
        content="{key:no-quotes}",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400


def test_session_sets_cookie_for_viewer_key(dms_client, api_keys_env):
    res = dms_client.post(
        "/cortex/session",
        json={"key": api_keys_env["viewer"]},
        follow_redirects=False,
    )
    assert res.status_code == 303
    assert "cortex_api_key" in res.cookies


def test_issue_key_uses_openvault_token(dms_client, monkeypatch):
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json",
        lambda *a, **k: {
            "ok": True,
            "token": "ov_test_once",
            "key": {"key_id": "abc", "label": "constructor cortex viewer", "tier": "free"},
        },
    )
    res = dms_client.post("/cortex/constructor/issue-key", json={})
    assert res.status_code == 200
    assert res.json()["token"].startswith("ov_")


def test_issue_key_503_tells_hyperlift_to_set_keys(dms_client, monkeypatch):
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.ping",
        lambda **k: False,
    )
    res = dms_client.post("/cortex/constructor/issue-key", json={})
    assert res.status_code == 503
    assert "DMS_API_KEYS" in res.text
    assert "Restart the openmw console" not in res.text


def test_issue_key_503_when_openvault_up_says_restart(dms_client, monkeypatch):
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.ping",
        lambda **k: True,
    )
    res = dms_client.post("/cortex/constructor/issue-key", json={})
    assert res.status_code == 503
    assert "Restart the openmw console" in res.text
    assert "second listener" in res.text
    assert "DMS_API_KEYS" in res.text


def test_demo_keys_rejected_when_refused(monkeypatch):
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    from packs.dms.security.api_auth import parse_api_keys, resolve_caller

    assert parse_api_keys() == {}
    assert resolve_caller("dms-demo-viewer-key") is None


def test_ov_token_resolves_via_openvault(monkeypatch):
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json",
        lambda *a, **k: {"ok": True, "key": {"key_id": "k1", "tier": "free"}},
    )
    from packs.dms.security.api_auth import resolve_caller

    caller = resolve_caller("ov_live_secret_value")
    assert caller is not None
    assert caller.role == "viewer"
    assert caller.actor == "ov_k1"


def test_ov_token_rejects_valid_false(monkeypatch):
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json",
        lambda *a, **k: {"ok": True, "valid": False},
    )
    from packs.dms.security.api_auth import resolve_caller

    assert resolve_caller("ov_dead") is None


def test_ov_token_accepts_flat_valid_true(monkeypatch):
    monkeypatch.setenv("DMS_REFUSE_DEMO_KEYS", "1")
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.setattr(
        "CortexOS.integrations.openvault_client.post_json",
        lambda *a, **k: {"ok": True, "valid": True, "key_id": "flat1", "tier": "free"},
    )
    from packs.dms.security.api_auth import resolve_caller

    caller = resolve_caller("ov_flat")
    assert caller is not None
    assert caller.actor == "ov_flat1"
