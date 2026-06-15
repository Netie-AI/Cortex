from .protocol import A2AMessage


class InProcessA2ATransport:
    """v1 intra-system transport placeholder for WebSocket path."""

    def __init__(self) -> None:
        self._handlers: dict[str, callable] = {}

    def register(self, did: str, handler) -> None:
        self._handlers[did] = handler

    def send(self, message: A2AMessage) -> A2AMessage | None:
        handler = self._handlers.get(message.to)
        if not handler:
            return None
        return handler(message)
