"""STUDIO-FIX-B: GET /dms/ontology lists pack YAML. It is not Insights or Constructor.

Counts must match the lists. A missing key is a typed refusal and must not
carry metric SQL. The published demo viewer key, which DMS still sends, is
accepted on the same gate as the other /dms reads.
"""

from __future__ import annotations

import yaml
from fastapi.testclient import TestClient

from CortexOS.ontology.registry import (
    ObjectType,
    load_action_types,
    load_functions,
    load_link_types,
    load_object_types,
    pack_dir_for,
)
from packs.dms.security.rate_limit import reset_limiter
from tests.api_key_isolation import TEST_VIEWER_KEY

DEMO_KEY = "dms-demo-viewer-key"
PATHS = (
    "/dms/ontology",
    "/dms/ontology/objects",
    "/dms/ontology/links",
    "/dms/ontology/actions",
    "/dms/ontology/functions",
    "/dms/ontology/metrics",
    "/dms/ontology/graph",
)
_SQL_MARKER = "COUNT(DISTINCT sku)"


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.delenv("DMS_AUTH_DISABLED", raising=False)
    import netie.config

    netie.config._cached_config = None
    reset_limiter(per_minute=600)
    from CortexOS.api.app import create_app

    return TestClient(create_app())


def _headers() -> dict[str, str]:
    return {"X-API-Key": TEST_VIEWER_KEY}


def test_seven_paths_match_yaml_and_counts(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _headers()
    bodies = {}
    for path in PATHS:
        res = client.get(path, headers=headers)
        assert res.status_code == 200, (path, res.status_code, res.text[:300])
        body = res.json()
        assert body["ok"] is True
        assert body["pack"] == "dms"
        assert "phase" not in body
        bodies[path] = body

    summary = bodies["/dms/ontology"]
    objects = bodies["/dms/ontology/objects"]["object_types"]
    links = bodies["/dms/ontology/links"]["link_types"]
    actions = bodies["/dms/ontology/actions"]["action_types"]
    functions = bodies["/dms/ontology/functions"]["functions"]
    metrics = bodies["/dms/ontology/metrics"]["metrics"]
    graph = bodies["/dms/ontology/graph"]
    counts = summary["counts"]

    assert set(counts) == {
        "object_types",
        "properties",
        "sensitive_properties",
        "link_types",
        "action_types",
        "action_tools",
        "action_events",
        "functions",
        "metrics",
    }
    assert counts["object_types"] == len(objects)
    assert counts["properties"] == sum(row["property_count"] for row in objects)
    assert counts["sensitive_properties"] == sum(row["sensitive_count"] for row in objects)
    assert counts["link_types"] == len(links)
    assert counts["action_types"] == len(actions)
    assert counts["action_tools"] == sum(1 for row in actions if row["kind"] == "tool")
    assert counts["action_events"] == sum(1 for row in actions if row["kind"] == "event")
    assert counts["functions"] == len(functions)
    assert counts["metrics"] == len(metrics)
    assert summary["objects_without_metrics"] == [
        row["id"] for row in objects if not row["metric_ids"]
    ]

    pack = pack_dir_for("dms")
    assert [row["id"] for row in objects] == [row.id for row in load_object_types(pack)]
    assert [row["id"] for row in links] == [row.id for row in load_link_types(pack)]
    assert [row["id"] for row in actions] == [row.id for row in load_action_types(pack)]
    assert [row["id"] for row in functions] == [row.id for row in load_functions(pack)]

    for row in objects:
        assert row["property_count"] == len(row["properties"])
        assert row["sensitive_count"] == sum(1 for prop in row["properties"] if not prop["agent_visible"])
        for prop in row["properties"]:
            assert set(prop) == {"name", "type", "agent_visible"}
    suppliers = next(row for row in objects if row["id"] == "suppliers")
    email = next(prop for prop in suppliers["properties"] if prop["name"] == "email")
    assert email["agent_visible"] is False

    for row in links:
        assert set(row) >= {
            "id",
            "from_object",
            "from_property",
            "to_object",
            "to_property",
            "cardinality",
        }
    for row in actions:
        assert set(row) >= {
            "id",
            "kind",
            "description",
            "ledger_event_type",
            "object_type",
            "required_role",
            "requires_confirm",
            "params",
        }
    export = next(row for row in actions if row["id"] == "export_pptx")
    assert export["kind"] == "tool"
    assert export["requires_confirm"] is True

    for row in functions:
        assert set(row) >= {"id", "description", "module", "callable"}

    raw_metrics = yaml.safe_load(
        (pack / "semantic" / "metrics.yaml").read_text(encoding="utf-8")
    )["metrics"]
    sku = next(row for row in metrics if row["id"] == "sku_count")
    raw_sku = next(row for row in raw_metrics if row["id"] == "sku_count")
    assert sku["sql"] == raw_sku["sql"].strip()
    assert "inventory" in sku["object_types"]
    assert set(sku) >= {
        "id",
        "kind",
        "synonyms",
        "result_columns",
        "params",
        "object_types",
        "sql",
    }
    inventory = next(row for row in objects if row["id"] == "inventory")
    assert "sku_count" in inventory["metric_ids"]

    assert [node["id"] for node in graph["nodes"]] == [row["id"] for row in objects]
    assert [edge["id"] for edge in graph["edges"]] == [row["id"] for row in links]
    for node in graph["nodes"]:
        assert set(node) >= {
            "id",
            "label",
            "description",
            "primary_key",
            "property_count",
            "sensitive_count",
            "metric_count",
            "degree",
        }
        ends = sum(
            (edge["source"] == node["id"]) + (edge["target"] == node["id"])
            for edge in graph["edges"]
        )
        assert node["degree"] == ends
        match = next(row for row in objects if row["id"] == node["id"])
        assert node["metric_count"] == len(match["metric_ids"])
    for edge in graph["edges"]:
        assert set(edge) >= {
            "id",
            "source",
            "target",
            "source_property",
            "target_property",
            "cardinality",
        }


def test_objects_come_from_the_registry_loader(monkeypatch, tmp_path) -> None:
    """A hardcoded object list would ignore a loader the request actually calls."""
    client = _client(monkeypatch, tmp_path)
    from CortexOS.ontology import page as page_mod

    real = page_mod.load_object_types

    def _with_sentinel(pack_dir=None):
        rows = list(real(pack_dir))
        rows.append(
            ObjectType(
                id="sentinel_object",
                description="sentinel from the loader",
                primary_key="id",
                properties=(),
            )
        )
        return rows

    monkeypatch.setattr(page_mod, "load_object_types", _with_sentinel)
    res = client.get("/dms/ontology/objects", headers=_headers())
    assert res.status_code == 200, res.text
    ids = [row["id"] for row in res.json()["object_types"]]
    assert ids[-1] == "sentinel_object"
    summary = client.get("/dms/ontology", headers=_headers())
    assert summary.json()["counts"]["object_types"] == len(ids)
    assert "sentinel_object" in summary.json()["objects_without_metrics"]


def test_openapi_lists_the_seven_paths(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    spec = client.app.openapi()["paths"]
    for path in PATHS:
        assert path in spec, path
        assert "get" in spec[path]
    assert "/dms/ontology" in spec
    ranked = spec["/v1/insights/ontology"]["get"]
    assert any(param.get("name") == "q" for param in ranked.get("parameters") or [])


def test_missing_key_is_a_typed_refusal_without_sql(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    for path in PATHS:
        res = client.get(path)
        assert res.status_code == 401, (path, res.status_code, res.text[:200])
        assert res.json()["detail"].endswith("missing_key")
        assert _SQL_MARKER not in res.text
        assert "object_types.yaml" not in res.text
        assert "sku_count" not in res.text


def test_demo_viewer_key_is_served(monkeypatch, tmp_path) -> None:
    """DMS still sends the published demo viewer key. The engine auth port refuses it."""
    monkeypatch.delenv("DMS_API_KEYS", raising=False)
    monkeypatch.delenv("DMS_REFUSE_DEMO_KEYS", raising=False)
    client = _client(monkeypatch, tmp_path)
    res = client.get(
        "/dms/ontology",
        headers={"X-API-Key": DEMO_KEY, "Authorization": f"Bearer {DEMO_KEY}"},
    )
    assert res.status_code == 200, res.text[:300]
    body = res.json()
    assert body["ok"] is True
    assert body["pack"] == "dms"
    assert "counts" in body


def test_unknown_pack_does_not_leak_a_path(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    res = client.get("/dms/ontology", params={"pack": "../secrets"}, headers=_headers())
    assert res.status_code == 404
    assert res.json()["detail"] == "unknown_pack"
    assert "secrets" not in res.text
    assert "object_types.yaml" not in res.text


def test_pack_query_reads_that_pack(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    res = client.get("/dms/ontology/objects", params={"pack": "crm"}, headers=_headers())
    assert res.status_code == 200, res.text[:300]
    body = res.json()
    assert body["pack"] == "crm"
    assert any(row["id"] == "account" for row in body["object_types"])
    metrics = client.get("/dms/ontology/metrics", params={"pack": "crm"}, headers=_headers())
    assert metrics.status_code == 200
    assert metrics.json()["metrics"] == []
    summary = client.get("/dms/ontology", params={"pack": "crm"}, headers=_headers()).json()
    assert summary["counts"]["metrics"] == 0
    assert summary["counts"]["object_types"] == len(body["object_types"])


def test_existing_ontology_routes_stay_ranking_and_catalog(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    headers = _headers()

    missing = client.get("/v1/insights/ontology", headers=headers)
    assert missing.status_code == 400
    assert missing.json()["detail"] == "q is required"
    ranked = client.get("/v1/insights/ontology", params={"q": "how many skus"}, headers=headers)
    assert ranked.status_code == 200, ranked.text[:300]
    ranking = ranked.json()
    assert ranking["phase"] == "ontology"
    assert "counts" not in ranking
    assert "object_types" not in ranking
    assert "objects_without_metrics" not in ranking

    side_missing = client.get("/dms/sidecar/insights/ontology", headers=headers)
    assert side_missing.status_code == 400
    assert side_missing.json()["detail"] == "q is required"
    side = client.get(
        "/dms/sidecar/insights/ontology",
        params={"q": "how many skus"},
        headers=headers,
    )
    assert side.status_code == 200, side.text[:300]
    assert side.json()["phase"] == "ontology"
    assert "counts" not in side.json()

    catalog = client.get("/cortex/constructor/ontology", headers=headers)
    assert catalog.status_code == 200, catalog.text[:300]
    body = catalog.json()
    assert body["ok"] is True
    assert isinstance(body["objects"], dict)
    assert "sku" in body["objects"]["inventory"]["points"]
    assert "export_pptx" in body["actions"]
    assert "counts" not in body
    assert "object_types" not in body
    assert "nodes" not in body
