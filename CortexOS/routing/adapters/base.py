from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AdapterRequest:
    model: str
    system: str
    prompt: str
    max_tokens: int = 1000
    stream: bool = False
    tools: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class AdapterResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    raw: dict  # full provider response, for debugging


class LLMAdapter(ABC):
    """Provider-specific completion + MYR costing (implementations tune rates)."""

    @abstractmethod
    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        raise NotImplementedError

    @abstractmethod
    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        raise NotImplementedError
