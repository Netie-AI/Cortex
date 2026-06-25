"""Token count estimates for projected cost before an LLM call."""


def estimate_prompt_tokens(text: str, family: str | None = None) -> int:
    """
    For OpenAI-family models, prefer tiktoken when installed.
    For vLLM / other endpoints, heuristic len(text)//4 is acceptable until a server-side estimator exists.
    """
    if not text:
        return 0
    normalized = family or ""
    if normalized in ("openai", "openai_compat"):
        try:
            import tiktoken  # type: ignore import-not-found

            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    # Anthropic counts differ; heuristic is intentionally conservative-lite for projection.
    return max(1, len(text) // 4)
