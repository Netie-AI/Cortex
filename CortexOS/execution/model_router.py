from dataclasses import dataclass, field
from typing import Any

from netie.routing.adapters import LLMAdapter, default_adapter_registry
from netie.routing.adapters.base import AdapterRequest, AdapterResponse
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest
from netie.routing.tiers import TIER_ORDER, Tier, bounded_tier

BIG_API_PLACEHOLDER = "BIG_API_PLACEHOLDER"


class TierFloorAboveCap(RuntimeError):
    """A deterministic safety floor judged a tier above the node's ``max_tier``.

    H2-LEGAL-CLAMP (#251). The call is refused rather than served below the
    floor. Names the floor, the judged tier and the cap so the ledger error row
    and the run failure say exactly which policy refused the call.
    """

    def __init__(self, *, request_type: str, floor: str, judged_tier: Tier, max_tier: Tier) -> None:
        self.request_type = request_type
        self.floor = floor
        self.judged_tier = judged_tier
        self.max_tier = max_tier
        super().__init__(
            f"{floor} requires {judged_tier.value} for request {request_type!r} but the node caps "
            f"max_tier at {max_tier.value}; refusing to serve below the floor"
        )


class FloorRefusalAdapter(LLMAdapter):
    """Adapter handed out in place of the real one when a floor exceeds the cap.

    ``complete`` raises the typed refusal, so the existing executor error path
    writes the ledger error row and re-raises. ``cost_myr`` is 0 so the cost
    gate cannot turn the refusal into a different error first. The real
    adapter is never reached.
    """

    def __init__(self, refusal: TierFloorAboveCap) -> None:
        self.refusal = refusal

    async def complete(self, req: AdapterRequest) -> AdapterResponse:
        del req
        raise self.refusal

    def cost_myr(self, prompt_tokens: int, completion_tokens: int) -> float:
        del prompt_tokens, completion_tokens
        return 0.0


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
        self.judgment_model = judgment_model or JudgmentModel.from_env()
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
        # H2-LEGAL-CLAMP (#251): a safety floor above the DSL cap fails closed.
        # The judged tier is reported and the adapter refuses; nothing is served
        # at the cap. Heuristic tiers (no floor) keep the clamp below. getattr so
        # a duck-typed judgment model without the field still routes as before.
        floor = getattr(decision, "floor", None)
        # The floor requires ``floor_tier``; the judged tier may sit above it
        # (a backend chose higher). Refuse when the *required* tier is above the
        # cap; when the cap satisfies the floor, the clamp below still lands at
        # or above it, so nothing is served under the floor either way.
        required = getattr(decision, "floor_tier", None) or decision.tier
        if floor is not None and TIER_ORDER[required] > TIER_ORDER[req.max_tier]:
            refusal = TierFloorAboveCap(
                request_type=req.request_type,
                floor=floor,
                judged_tier=required,
                max_tier=req.max_tier,
            )
            return RoutedModelCall(
                tier=required,
                provider=self._resolve_provider(req.provider, required),
                model=self.tier_models[required],
                reason=f"refused: {refusal} ({decision.reason})",
                adapter=FloorRefusalAdapter(refusal),
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
