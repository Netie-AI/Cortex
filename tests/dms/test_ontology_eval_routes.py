"""Read APIs for the ontology registry and the benchmark artifacts.

Both route modules are mounted with the pack's viewer gate injected from
``CortexOS.api.app``; the anonymous checks below are what proves the injection
actually happened rather than the routes being quietly public.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from packs.dms.semantic.loader import load_all


@pytest.fixture
def api_keys_env(monkeypatch):
    monkeypatch.setenv("DMS_API_KEYS", "viewer:sk-viewer-test;admin:sk-admin-test")
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    return {"viewer": "sk-viewer-test", "admin": "sk-admin-test"}


@pytest.fixture
def client(api_keys_env):
    from packs.dms.security.rate_limit import reset_limiter

    reset_limiter(per_minute=600)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


@pytest.fixture
def viewer(api_keys_env):
    return {"X-API-Key": api_keys_env["viewer"]}


# ---------------------------------------------------------------- ontology


def test_ontology_requires_a_key(client):
    assert client.get("/dms/ontology").status_code in (401, 403)


def test_summary_counts_match_the_registry(client, viewer):
    body = client.get("/dms/ontology", headers=viewer).json()
    from CortexOS.ontology.registry import load_link_types, load_object_types

    assert body["pack"] == "dms"
    assert body["counts"]["object_types"] == len(load_object_types())
    assert body["counts"]["link_types"] == len(load_link_types())
    assert body["counts"]["sensitive_properties"] >= 1  # supplier PII is flagged


def test_objects_carry_properties_and_the_metrics_that_read_them(client, viewer):
    objects = client.get("/dms/ontology/objects", headers=viewer).json()["object_types"]
    inventory = next(o for o in objects if o["id"] == "inventory")
    assert inventory["primary_key"] == "sku"
    assert any(p["name"] == "quantity_kg" for p in inventory["properties"])
    assert "sku_count" in inventory["metric_ids"]


def test_sensitive_properties_are_marked_not_dropped(client, viewer):
    objects = client.get("/dms/ontology/objects", headers=viewer).json()["object_types"]
    suppliers = next(o for o in objects if o["id"] == "suppliers")
    email = next(p for p in suppliers["properties"] if p["name"] == "email")
    assert email["agent_visible"] is False
    assert suppliers["sensitive_count"] >= 3


def test_metrics_view_covers_the_semantic_layer(client, viewer):
    metrics = client.get("/dms/ontology/metrics", headers=viewer).json()["metrics"]
    assert len(metrics) == len(load_all().metrics)
    assert {m["id"] for m in metrics} >= {"sku_count", "delayed_count"}


def test_actions_split_tools_from_events(client, viewer):
    actions = client.get("/dms/ontology/actions", headers=viewer).json()["action_types"]
    tools = [a for a in actions if a["kind"] == "tool"]
    assert any(a["id"] == "export_pptx" and a["requires_confirm"] for a in tools)
    assert any(a["kind"] == "event" and a["id"] == "item.intake" for a in actions)


def test_graph_edges_reference_declared_nodes(client, viewer):
    graph = client.get("/dms/ontology/graph", headers=viewer).json()
    node_ids = {n["id"] for n in graph["nodes"]}
    assert node_ids
    for edge in graph["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids
    assert sum(n["degree"] for n in graph["nodes"]) == 2 * len(graph["edges"])


def test_unknown_pack_is_a_404_not_a_traceback(client, viewer):
    res = client.get("/dms/ontology", params={"pack": "no-such-pack"}, headers=viewer)
    assert res.status_code == 404


# -------------------------------------------------------------------- eval


def test_eval_requires_a_key(client):
    assert client.get("/dms/eval/summary").status_code in (401, 403)


def test_eval_summary_reports_thresholds_and_runs(client, viewer):
    body = client.get("/dms/eval/summary", headers=viewer).json()
    assert body["thresholds"]["confidently_wrong"] == 0
    assert set(body["runs"]) >= {"corpus", "live_probe", "paraphrase", "adversarial"}
    for run in body["runs"].values():
        assert {"id", "label", "proves", "present"} <= set(run)


def test_claim_is_refused_until_the_corpus_reaches_target(client, viewer):
    claim = client.get("/dms/eval/summary", headers=viewer).json()["claim"]
    if claim["corpus_target"] and claim["corpus_n"] < claim["corpus_target"]:
        assert claim["supported"] is False
        assert any("corpus is N=" in b for b in claim["blockers"])


def test_any_confidently_wrong_answer_blocks_the_claim(client, viewer):
    claim = client.get("/dms/eval/summary", headers=viewer).json()["claim"]
    if claim["confidently_wrong"]:
        assert claim["supported"] is False


def test_run_detail_filters_by_outcome(client, viewer):
    res = client.get("/dms/eval/runs/corpus", headers=viewer)
    if res.status_code == 404:
        pytest.skip("no corpus artifact on disk")
    assert res.json()["items"]
    wrong = client.get(
        "/dms/eval/runs/corpus", params={"outcome": "wrong"}, headers=viewer
    ).json()
    assert all(item["outcome"] == "wrong" for item in wrong["items"])


def test_unknown_run_is_a_404(client, viewer):
    assert client.get("/dms/eval/runs/nope", headers=viewer).status_code == 404
