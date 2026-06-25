from dataclasses import dataclass, field
from typing import Any

from netie.routing.adapters import LLMAdapter, default_adapter_registry
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest
from netie.routing.tiers import Tier, bounded_tier

BIG_API_PLACEHOLDER = "BIG_API_PLACEHOLDER"


@dataclass(slots=True)
class ModelRequest:
    request_type: str
    prompt: str
    default_tier: Tier
    max_tier: Tier
    cost_ceiling_myr: float = 0.5
    provider: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoutedModelCall:
    """Output of routing: tier + resolved provider/model + callable adapter."""

    tier: Tier
    provider: str
    model: str
    reason: str
    adapter: LLMAdapter


# Alias for legacy naming
ModelResponse = RoutedModelCall


class ModelRouter:
    def __init__(
        self,
        provider_aliases: dict[str, str] | None = None,
        tier_models: dict[Tier, str] | None = None,
        judgment_model: JudgmentModel | None = None,
        adapter_registry: dict[str, LLMAdapter] | None = None,
        vllm_base_url: str | None = None,
    ) -> None:
        self.provider_aliases = provider_aliases or {BIG_API_PLACEHOLDER: "anthropic"}
        self.tier_models = tier_models or {
            Tier.T0: "fasttext+bge-m3",
            Tier.T1: "qwen2.5-7b-instruct",
            Tier.T2: "qwen2.5-32b-instruct",
            Tier.T3: "claude-sonnet",
        }
        self.judgment_model = judgment_model or JudgmentModel()
        self.adapter_registry = adapter_registry or default_adapter_registry(vllm_base_url=vllm_base_url)

    def route(self, req: ModelRequest) -> RoutedModelCall:
        decision = self.judgment_model.decide(
            JudgmentRequest(
                request_type=req.request_type,
                content=req.prompt,
                context_size=len(req.prompt),
                prior_tier_failures=int(req.metadata.get("prior_tier_failures", 0)),
                user_tier_budget=Tier(req.metadata.get("user_tier_budget", req.max_tier.value)),
                is_vip=bool(req.metadata.get("is_vip", False)),
            )
        )
        chosen_tier = bounded_tier(decision.tier, req.default_tier, req.max_tier)
        provider = self._resolve_provider(req.provider, chosen_tier)
        model = self.tier_models[chosen_tier]
        adapter = self._adapter_for(provider)
        return RoutedModelCall(
            tier=chosen_tier,
            provider=provider,
            model=model,
            reason=decision.reason,
            adapter=adapter,
        )

    def _resolve_provider(self, provider: str | None, tier: Tier) -> str:
        if provider is None:
            return (
                "self_hosted"
                if tier in {Tier.T0, Tier.T1, Tier.T2}
                else self.provider_aliases[BIG_API_PLACEHOLDER]
            )
        if provider == BIG_API_PLACEHOLDER:
            return self.provider_aliases.get(provider, "anthropic")
        return provider

    def _adapter_for(self, provider: str) -> LLMAdapter:
        adapter = self.adapter_registry.get(provider)
        if adapter is None:
            adapter = self.adapter_registry.get("self_hosted")
        if adapter is None:
            raise KeyError(f"No LLM adapter registered for provider {provider!r}")
        return adapter
