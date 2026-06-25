def decayed_formality(initial: float, exchange_count: int, floor: float = 0.1, decay: float = 0.1) -> float:
    steps = exchange_count // 5
    return max(floor, round(initial - (steps * decay), 3))
