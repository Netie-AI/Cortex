"""Contract tests for the Netie Clicks opaque telemetry intake."""



from __future__ import annotations



import base64

import json



from fastapi import FastAPI

from fastapi.testclient import TestClient



from CortexOS.api.telemetry_routes import register_telemetry_routes





def _client(monkeypatch, tmp_path) -> tuple[TestClient, object]:

    telemetry_dir = tmp_path / "telemetry"

    monkeypatch.setenv("CORTEX_TELEMETRY_DIR", str(telemetry_dir))

    monkeypatch.setenv("DMS_MASTER_KEY", base64.b64encode(b"M" * 32).decode("ascii"))

    app = FastAPI()

    register_telemetry_routes(app)

    return TestClient(app), telemetry_dir





def _envelope() -> dict:

    return {

        "v": 1,

        "id": "record-1",

        "type": "telemetry",

        "lineage_id": "lineage-1",

        "wrap_user": {"iv": "user-iv", "tag": "user-tag", "ct": "user-ct"},

        "wrap_netie": {"iv": "netie-iv", "tag": "netie-tag", "ct": "netie-ct"},

        "body": {"iv": "body-iv", "tag": "body-tag", "ct": "opaque-ciphertext"},

        "hash": "integrity-hash",

    }





def test_register_returns_stable_encrypted_at_rest_kek(monkeypatch, tmp_path):

    client, telemetry_dir = _client(monkeypatch, tmp_path)

    body = {"device_id": "clicks-device-1", "product": "netie-clicks"}



    first = client.post("/v1/telemetry/register", json=body)

    second = client.post("/v1/telemetry/register", json=body)



    assert first.status_code == 200

    assert second.status_code == 200

    assert first.json() == second.json()

    assert len(base64.b64decode(first.json()["netie_kek_b64"])) == 32

    key_file = next((telemetry_dir / "keys").glob("*.json"))

    persisted = key_file.read_text(encoding="utf-8")

    assert first.json()["netie_kek_b64"] not in persisted

    assert json.loads(persisted)["key_storage"] == "dms-aes256-gcm-v1"





def test_ingest_stores_opaque_envelope_unchanged(monkeypatch, tmp_path):

    client, telemetry_dir = _client(monkeypatch, tmp_path)

    envelope = _envelope()



    response = client.post(

        "/v1/telemetry",

        json={

            "device_id": "clicks-device-1",

            "lineage_id": "lineage-1",

            "kind": "outcome",

            "envelope": envelope,

            "user_verified": True,

            "flush_reason": "manual",

        },

    )



    assert response.status_code == 200

    assert response.json()["stored"] is True

    stored_file = next((telemetry_dir / "envelopes").glob("*.json"))

    assert json.loads(stored_file.read_text(encoding="utf-8"))["envelope"] == envelope

    audit = (telemetry_dir / "audit.jsonl").read_text(encoding="utf-8")

    assert "opaque-ciphertext" not in audit





def test_ingest_rejects_unverified_or_netie_unwrapped(monkeypatch, tmp_path):

    client, _ = _client(monkeypatch, tmp_path)

    payload = {

        "device_id": "clicks-device-1",

        "lineage_id": "lineage-1",

        "kind": "outcome",

        "envelope": _envelope(),

        "user_verified": False,

        "flush_reason": "manual",

    }

    assert client.post("/v1/telemetry", json=payload).status_code == 403



    payload["user_verified"] = True

    payload["envelope"]["wrap_netie"] = None

    assert client.post("/v1/telemetry", json=payload).status_code == 400





def test_create_app_registers_telemetry_routes(monkeypatch, tmp_path):

    monkeypatch.setenv("CORTEX_TELEMETRY_DIR", str(tmp_path / "telemetry"))

    from CortexOS.api.app import create_app



    client = TestClient(create_app())

    assert client.get("/v1/telemetry/health").status_code == 200

    assert client.post(

        "/v1/telemetry/register",

        json={"device_id": "app-smoke-device", "product": "netie-clicks"},

    ).status_code == 200

