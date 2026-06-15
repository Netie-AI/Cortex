from datetime import datetime

from netie.a2a.protocol import A2AMessage


class BuyerAgent:
    did_prefix = "did:netie:agent:buyer/"

    def ask_listing(self, did: str, listing_id: str, question: str) -> A2AMessage:
        return A2AMessage(
            id=f"msg_{listing_id}",
            from_did=f"{self.did_prefix}local",
            to=did,
            created_at=datetime.utcnow(),
            type="https://netie.com/a2a/v1/ask_about_listing",
            body={"listing_id": listing_id, "question": question},
            expects_reply=True,
        )
