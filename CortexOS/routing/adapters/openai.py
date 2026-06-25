import time

import litellm

from netie.routing.adapters._litellm_util import litellm_completion_to_adapter_response
from netie.routing.adapters.base import AdapterRequest, AdapterResponse, LLMAdapter


class OpenAIAdapter(LLMAdapter):
    """OpenAI via LiteLLM."""

    def __init__(
        self,
        *,
        usd_per_million_prompt: float = 5.0,
        usd_per_million_completion: float = 15.0,
        myr_per_usd: float = 4.7,
        model_prefix: str = "openai/",
    ) -> None:
        self._usd_in = usd_per_million_prompt / 1_000_000
        self._usd_out = usd_per_million_completion / 1_000_000
        self._myr_usd = myr_per_usd
        self._model_prefix = model_prefix

    def _litellm_model(self, model: str) -> str:
        if "/" in model:
            return model
        return f"{self._model_prefix}{model}"

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        t0 = time.perf_counter()
        messages = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.prompt})

        resp = await litellm.acompletion(
            model=self._litellm_model(req.model),
            messages=messages,
            max_tokens=req.max_tokens,
            stream=req.stream,
        )
        return litellm_completion_to_adapter_response(resp, t0)

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        usd = (prompt_tokens * self._usd_in) + (completion_tokens * self._usd_out)
        return round(usd * self._myr_usd, 6)
