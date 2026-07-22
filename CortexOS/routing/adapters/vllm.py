import time

import litellm

from netie.routing.adapters._litellm_util import litellm_completion_to_adapter_response
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter


class SelfHostedAdapter(LLMAdapter):
    """
    vLLM / OpenAI-compatible server. Uses LiteLLM with custom api_base + openai routing.
    """

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str | None = None,
        usd_per_million_prompt: float = 0.2,
        usd_per_million_completion: float = 0.2,
        myr_per_usd: float = 4.7,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key or "EMPTY"
        self._usd_in = usd_per_million_prompt / 1_000_000
        self._usd_out = usd_per_million_completion / 1_000_000
        self._myr_usd = myr_per_usd

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        t0 = time.perf_counter()
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})

        model_for_litellm = req.model if "/" in req.model else f"openai/{req.model}"
        kwargs: dict = {
            "model": model_for_litellm,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "stream": req.stream,
            "api_base": self.api_base,
            "api_key": self.api_key,
        }

        resp = await litellm.acompletion(**kwargs)
        return litellm_completion_to_adapter_response(resp, t0)

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        usd = (prompt_tokens * self._usd_in) + (completion_tokens * self._usd_out)
        return round(usd * self._myr_usd, 6)
