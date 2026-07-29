"""In-process A2A runtime used by local callers and the HTTP API."""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from CortexOS.a2a.protocol import A2AMessage
from CortexOS.a2a.transport_ws import InProcessA2ATransport

DEFAULT_FROM_DID = "did:netie:cortex"
DEFAULT_MESSAGE_TYPE = "https://netie.com/a2a/v1/message"

transport = InProcessA2ATransport()
_bootstrapped = False


def _reply(message: A2AMessage, body: dict[str, Any], message_type: str) -> A2AMessage:
    return A2AMessage(
        id=str(uuid4()),
        thread_id=message.id,
        from_did=message.to,
        to=message.from_did,
        created_at=datetime.now(timezone.utc),
        type=message_type,
        body=body,
    )


def _handle_echo(message: A2AMessage) -> A2AMessage:
    return _reply(message, message.body, message.type)


def _handle_seller(message: A2AMessage) -> A2AMessage:
    return _reply(
        message,
        {
            "answer": "Listing details available.",
            "confidence": 0.9,
            "evidence": [],
        },
        "https://netie.com/a2a/v1/listing_answer",
    )


def bootstrap_default_agents() -> InProcessA2ATransport:
    """Register the local demo A2A handlers once and return their transport."""
    global _bootstrapped
    if not _bootstrapped:
        transport.register("did:netie:echo", _handle_echo)
        transport.register("did:netie:seller", _handle_seller)
        _bootstrapped = True
    return transport


def send_message(
    to: str,
    body: dict[str, Any],
    from_did: str = DEFAULT_FROM_DID,
    type: str = DEFAULT_MESSAGE_TYPE,
) -> A2AMessage | None:
    """Deliver a message to a default local A2A agent."""
    active_transport = bootstrap_default_agents()
    return active_transport.send(
        A2AMessage(
            id=str(uuid4()),
            from_did=from_did,
            to=to,
            created_at=datetime.now(timezone.utc),
            type=type,
            body=body,
            expects_reply=True,
        )
    )
