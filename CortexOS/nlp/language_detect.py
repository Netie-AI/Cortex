def detect_language_mix(text: str) -> dict[str, float]:
    lowered = text.lower()
    scores = {"en": 0.0, "ms": 0.0, "zh": 0.0}
    if any(token in lowered for token in ("the", "is", "and", "with")):
        scores["en"] += 0.6
    if any(token in lowered for token in ("untuk", "dan", "dengan", "bilik")):
        scores["ms"] += 0.6
    if any(ch >= "\u4e00" and ch <= "\u9fff" for ch in text):
        scores["zh"] += 0.8
    total = sum(scores.values()) or 1.0
    return {k: round(v / total, 3) for k, v in scores.items()}
