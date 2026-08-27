"""Crew model routing: Gemini 3.5 when a key is present, else local."""

from CortexOS.crew import llm


def test_resolve_defaults_local(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("CREW_LLM_BASE", raising=False)
    monkeypatch.delenv("CREW_LLM_MODEL", raising=False)
    monkeypatch.delenv("CREW_LLM_API_KEY", raising=False)
    assert llm.resolve_base() == llm.OLLAMA_BASE
    assert llm.resolve_model() == "qwen3:4b"
    assert llm.resolve_api_key() == ""


def test_resolve_gemini_when_key_set(monkeypatch):
    monkeypatch.delenv("CREW_LLM_BASE", raising=False)
    monkeypatch.delenv("CREW_LLM_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    assert llm.resolve_base() == llm.GEMINI_OPENAI_BASE
    assert llm.resolve_model() == "gemini-3.5-flash"
    assert llm.resolve_api_key() == "test-key"
