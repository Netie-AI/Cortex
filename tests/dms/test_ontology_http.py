"""GET /dms/ontology -- DMS Ontology page reads this; no invented rows."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from packs.dms.ontology.http import ontology_bundle
from packs.dms.ontology.registry import PACK_DIR, load_object_types
from packs.dms.security.rate_limit import reset_limiter


@pytest.fixture
def api_keys_env(monkeypatch):
    monkeypatch.setenv(
        "DMS_API_KEYS",
        "viewer:sk-viewer-test;steward:sk-steward-test;admin:sk-admin-test",
    )
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("PACK", "dms")
    return {"viewer": "sk-viewer-test"}


@pytest.fixture
def dms_client(api_keys_env):
    reset_limiter(per_minute=120)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def test_ontology_requires_api_key(dms_client):
    assert dms_client.get("/dms/ontology").status_code == 401


def test_slim_constructor_app_serves_ontology(api_keys_env):
    reset_limiter(per_minute=120)
    from packs.dms.constructor_app import create_constructor_app
    from fastapi.testclient import TestClient

    client = TestClient(create_constructor_app())
    res = client.get("/dms/ontology", headers={"X-API-Key": api_keys_env["viewer"]})
    assert res.status_code == 200, res.text
    assert res.json()["pack"] == "dms"
    assert res.json()["counts"]["object_types"] >= 1


def test_ontology_summary_matches_registry(dms_client, api_keys_env):
    res = dms_client.get("/dms/ontology", headers={"X-API-Key": api_keys_env["viewer"]})
    assert res.status_code == 200, res.text
    body = res.json()
    want = ontology_bundle("dms")
    assert body["pack"] == "dms"
    assert body["counts"]["object_types"] == len(load_object_types(PACK_DIR))
    assert body["counts"]["metrics"] == want["counts"]["metrics"]
    assert body["counts"]["object_types"] == want["counts"]["object_types"]
    assert isinstance(body["objects_without_metrics"], list)


def test_ontology_sections_match_dms_allowlist(dms_client, api_keys_env):
    headers = {"X-API-Key": api_keys_env["viewer"]}
    objects = dms_client.get("/dms/ontology/objects", headers=headers)
    assert objects.status_code == 200, objects.text
    ids = {row["id"] for row in objects.json()["object_types"]}
    assert ids == {o.id for o in load_object_types(PACK_DIR)}
    first = objects.json()["object_types"][0]
    assert "property_count" in first
    assert "metric_ids" in first
    links = dms_client.get("/dms/ontology/links", headers=headers)
    assert links.status_code == 200
    assert "link_types" in links.json()
    graph = dms_client.get("/dms/ontology/graph", headers=headers)
    assert graph.status_code == 200
    assert {n["id"] for n in graph.json()["nodes"]} == ids
    metrics = dms_client.get("/dms/ontology/metrics", headers=headers)
    assert metrics.status_code == 200
    assert metrics.json()["metrics"]
    assert dms_client.get("/dms/ontology/secrets", headers=headers).status_code == 404
