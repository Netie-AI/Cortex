from enum import Enum


class Tier(str, Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


TIER_ORDER: dict[Tier, int] = {
    Tier.T0: 0,
    Tier.T1: 1,
    Tier.T2: 2,
    Tier.T3: 3,
}


def bounded_tier(candidate: Tier, default_tier: Tier, max_tier: Tier) -> Tier:
    """Clamp candidate tier between default and max."""
    floor = TIER_ORDER[default_tier]
    ceiling = TIER_ORDER[max_tier]
    value = TIER_ORDER[candidate]
    if value < floor:
        return default_tier
    if value > ceiling:
        return max_tier
    return candidate
