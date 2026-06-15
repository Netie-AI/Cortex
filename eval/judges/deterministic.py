def language_fidelity(user_mix: dict[str, float], model_mix: dict[str, float]) -> float:
    keys = set(user_mix) | set(model_mix)
    delta = sum(abs(user_mix.get(k, 0.0) - model_mix.get(k, 0.0)) for k in keys)
    return max(0.0, 1.0 - (delta / 2.0))
