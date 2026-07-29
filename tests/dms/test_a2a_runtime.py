from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from CortexOS.a2a.protocol import A2AMessage
from CortexOS.a2a.runtime import send_message
from CortexOS.a2a.transport_ws import InProcessA2ATransport
from CortexOS.execution.dag_runner import ExecutionContext, execute_a2a_call_node
from CortexOS.fabrication.dsl_parser import DSLNode


def test_send_message_round_trips_to_echo_agent():
    reply = send_message(
        to="did:netie:echo",
        body={"message": "hello"},
        from_did="did:netie:buyer",
    )

    assert reply is not None
    assert reply.to == "did:netie:buyer"
    assert reply.body == {"message": "hello"}


def test_send_message_denies_unknown_agent():
    assert send_message("did:netie:unknown", {"message": "hello"}) is None


def test_a2a_message_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("PACK", "dms")
    monkeypatch.setenv("DMS_AUTH_DISABLED", "1")

    from CortexOS.api.app import create_app

    client = TestClient(create_app())
    response = client.post(
        "/a2a/messages",
        json={
            "to": "did:netie:seller",
            "body": {"listing_id": "listing-1", "question": "Is it available?"},
            "from_did": "did:netie:buyer",
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["reply"]["body"]["answer"] == "Listing details available."

    unknown = client.post("/a2a/messages", json={"to": "did:netie:unknown", "body": {}})
    assert unknown.status_code == 404
    assert unknown.json() == {"ok": False, "error": "unknown_agent"}


@pytest.mark.asyncio
async def test_a2a_call_node_uses_injected_transport():
    transport = InProcessA2ATransport()

    def handle(message: A2AMessage) -> A2AMessage:
        return A2AMessage(
            id="reply-1",
            thread_id=message.id,
            from_did=message.to,
            to=message.from_did,
            created_at=datetime.now(timezone.utc),
            type="test/reply",
            body={"received": message.body},
        )

    transport.register("did:netie:test", handle)
    context = ExecutionContext(
        "a2a-run",
        seed={"_a2a_transport": transport, "payload": {"listing_id": "listing-1"}},
    )
    node = DSLNode(
        id="contact-seller",
        kind="a2a_call",
        target_did="did:netie:test",
        inputs=["payload"],
    )

    result = await execute_a2a_call_node(node, context)

    assert result.output["ok"] is True
    assert result.output["reply"]["body"] == {"received": {"listing_id": "listing-1"}}
