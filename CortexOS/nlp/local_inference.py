"""Optional local GPU/CPU inference — falls back if model unavailable."""

from __future__ import annotations

import os
from functools import lru_cache

from packs.dms.classify.intent import ClassifyResult, classify_heuristic

DEFAULT_MODEL = os.environ.get("CORTEX_LOCAL_MODEL", "Qwen/Qwen2.5-1.5B-Instruct")


@lru_cache(maxsize=1)
def _model_ready() -> bool:
    if os.environ.get("CORTEX_LOCAL_INFERENCE", "").lower() not in ("1", "true", "yes"):
        return False
    try:
        import torch

        return torch.cuda.is_available() or os.environ.get("CORTEX_LOCAL_CPU", "") == "1"
    except ImportError:
        return False


def classify_with_model(text: str) -> ClassifyResult:
    """GPU path when CORTEX_LOCAL_INFERENCE=1 and transformers installed."""
    if not _model_ready():
        raise RuntimeError("local inference disabled")

    from transformers import AutoModelForCausalLM, AutoTokenizer
    import torch

    model_id = DEFAULT_MODEL
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto" if device == "cuda" else None,
        trust_remote_code=True,
    )
    if device == "cpu":
        model = model.to(device)

    prompt = (
        "Classify warehouse message. Reply JSON only: "
        '{"intent":"check_stock|order_status|other","sentiment":-1 to 1}\n'
        f"Message: {text[:500]}"
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=64, do_sample=False)
    raw = tokenizer.decode(out[0], skip_special_tokens=True)
    # Fallback parse — if model output unusable, heuristic
    if "check_stock" in raw or "order_status" in raw:
        base = classify_heuristic(text)
        return ClassifyResult(
            intent=base.intent,
            sentiment=base.sentiment,
            confidence=min(0.95, base.confidence + 0.1),
            blocked=base.blocked,
            block_reason=base.block_reason,
            language_mix=base.language_mix,
            psychological_state=base.psychological_state,
        )
    return classify_heuristic(text)
