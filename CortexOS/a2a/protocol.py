from datetime import datetime

from pydantic import BaseModel


class A2AMessage(BaseModel):
    id: str
    from_did: str
    to: str
    created_at: datetime
    type: str
    body: dict
    expects_reply: bool = False
    reply_timeout_ms: int = 5000
    thread_id: str | None = None
