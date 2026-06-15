BIG_API_PLACEHOLDER = "BIG_API_PLACEHOLDER"


def judge_response(prompt: str, response: str, provider: str = BIG_API_PLACEHOLDER) -> dict:
    return {
        "provider": provider,
        "score": 4.0,
        "reason": "placeholder llm judge; wire through model_router in integration",
    }
