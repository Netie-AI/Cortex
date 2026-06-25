"""Phase 3 scaffolding: sentiment stub, tone YAML, memory window, scheduler hook."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_sentiment_intent_stub_shape():
    from netie.nlp import INTENT_CLASSES, SentimentIntentModel

    m = SentimentIntentModel()
    r = m.predict("any text")
    assert r.confidence == pytest.approx(0.5)
    assert r.sentiment == pytest.approx(0.0)
    assert r.intent == "off_topic"
    assert r.intent in INTENT_CLASSES


def test_tone_profile_yaml_and_compose():
    from netie.personality import compose_system_prompt, load_tone_agent_yaml

    path = ROOT / "CortexOS" / "personality" / "profiles" / "ai_buyer_v1.yaml"
    loaded = load_tone_agent_yaml(path)
    assert loaded.agent_id == "ai_buyer_v1"
    system = compose_system_prompt(loaded.agent_id, loaded.tone)
    assert "ai_buyer_v1" in system
    assert "Malaysia" in system


def test_timing_urgent_always_sendable():
    from netie.personality.timing import is_sendable_now

    assert is_sendable_now("Asia/Kuala_Lumpur", "muslim", "urgent") is True


@pytest.mark.asyncio
async def test_build_context_window_working_only():
    from netie.personality.memory import InMemoryWorkingStore, build_context_window

    w = InMemoryWorkingStore()
    await w.append_turn("sess-1", "Buyer asked about parking.")
    blob = await build_context_window("sess-1", "user-9", working=w, semantic_engine=None)
    assert "parking" in blob


def test_weekly_summarizer_registers_on_scheduler():
    pytest.importorskip("apscheduler")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from netie.personality.weekly_summarizer import register_weekly_summarizer

    sch = AsyncIOScheduler()
    register_weekly_summarizer(sch)
    assert len(sch.get_jobs()) >= 1
    sch.shutdown(wait=False)
