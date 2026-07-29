from netie.routing.adapters.anthropic import AnthropicAdapter
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter
from netie.routing.adapters.openai import OpenAIAdapter
from netie.routing.adapters.vllm import SelfHostedAdapter

__all__ = [
    "AdapterRequest",
    "AdapterResponse",
    "LLMAdapter",
    "AnthropicAdapter",
    "OpenAIAdapter",
    "SelfHostedAdapter",
]


def default_adapter_registry(vllm_base_url: str | None = None) -> dict[str, LLMAdapter]:
    """
    Canonical registry keys match resolved provider strings from ModelRouter.
    BIG_API aliases map into one of these (e.g. anthropic vs openai).
    """
    vllm_base = vllm_base_url or "http://127.0.0.1:8000/v1"
    return {
        "anthropic": AnthropicAdapter(),
        "openai": OpenAIAdapter(),
        "self_hosted": SelfHostedAdapter(api_base=vllm_base),
    }
