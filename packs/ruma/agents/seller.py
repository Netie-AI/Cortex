from datetime import datetime

from netie.a2a.protocol import A2AMessage


class SellerAgent:
    def __init__(self, did: str) -> None:
        self.did = did

    def handle(self, message: A2AMessage) -> A2AMessage:
        return A2AMessage(
            id=f"reply_{message.id}",
            thread_id=message.id,
            from_did=self.did,
            to=message.from_did,
            created_at=datetime.utcnow(),
            type="https://netie.com/a2a/v1/listing_answer",
            body={"answer": "Listing details available.", "confidence": 0.9, "evidence": []},
        )
