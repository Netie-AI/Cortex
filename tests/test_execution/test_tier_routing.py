from netie.execution.model_router import BIG_API_PLACEHOLDER, ModelRequest, ModelRouter
from netie.routing.tiers import Tier


def test_router_respects_max_tier():
    router = ModelRouter()
    req = ModelRequest(
        request_type="birthday_rapport",
        prompt="Send a birthday message",
        default_tier=Tier.T1,
        max_tier=Tier.T2,
        provider=BIG_API_PLACEHOLDER,
    )
    res = router.route(req)
    assert res.tier == Tier.T2
    assert res.adapter is not None


def test_router_routes_intent_classify_to_t0():
    router = ModelRouter()
    req = ModelRequest(
        request_type="intent_classify",
        prompt="how many items",
        default_tier=Tier.T0,
        max_tier=Tier.T2,
        provider=BIG_API_PLACEHOLDER,
    )
    res = router.route(req)
    assert res.tier == Tier.T0


def test_router_uses_big_api_placeholder_for_t3():
    router = ModelRouter(provider_aliases={BIG_API_PLACEHOLDER: "openai"})
    req = ModelRequest(
        request_type="birthday_rapport",
        prompt="Birthday for VIP user",
        default_tier=Tier.T1,
        max_tier=Tier.T3,
        provider=BIG_API_PLACEHOLDER,
    )
    res = router.route(req)
    assert res.tier == Tier.T3
    assert res.provider == "openai"
    assert res.adapter is not None
