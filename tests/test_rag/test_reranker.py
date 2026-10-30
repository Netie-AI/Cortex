import asyncio
import time

import pytest

from netie.rag.fuser_rrf import FusedHit
from netie.rag.reranker import BGEReranker, doc_text_from_fused


def test_doc_text_concatenates_payload_fields():
    h = FusedHit(
        listing_id="x",
        fused_score=0.5,
        payload={"title": "A", "description": "B", "postcode": "50000"},
    )
    txt = doc_text_from_fused(h)
    assert "A" in txt and "B" in txt and "50000" in txt


@pytest.mark.asyncio
async def test_fallback_zero_scores_preserve_rrf_order(monkeypatch):
    def no_cross(self):  # noqa: ARG001
        return None

    monkeypatch.setattr(BGEReranker, "_get_cross_encoder", no_cross)

    r = BGEReranker()
    fused = [
        FusedHit("a", 2.0, {"title": "one"}),
        FusedHit("b", 1.5, {"title": "two"}),
    ]

    ranked = await r.rerank("q", fused, top_n=10)

    assert [x.hit.listing_id for x in ranked] == ["a", "b"]
    assert all(x.rerank_score == 0.0 for x in ranked)


@pytest.mark.asyncio
async def test_cross_encoder_scores_sort_desc(monkeypatch):
    r = BGEReranker()

    def fake_scores(pairs):
        return [float(i) for i in range(len(pairs))]

    monkeypatch.setattr(r, "_score_pairs", fake_scores)

    fused = [
        FusedHit("a", 1.0, {"title": "x"}),
        FusedHit("b", 1.0, {"title": "y"}),
    ]
    ranked = await r.rerank("qq", fused, top_n=10)

    ids = [x.hit.listing_id for x in ranked]
    assert ids[1] == "a"
    assert ids[0] == "b"


@pytest.mark.asyncio
async def test_semaphore_serializes_concurrent_slow_reranks(monkeypatch):
    r = BGEReranker(max_concurrent=1)

    def slow_score(pairs):
        time.sleep(0.08)
        return [float(i) * 0.01 for i in range(len(pairs))]

    monkeypatch.setattr(r, "_score_pairs", slow_score)

    fused = [
        FusedHit("only", 1.0, {"title": "t"}),
    ]

    async def run_batch():
        return await r.rerank("slow", fused, top_n=5)

    t0 = time.perf_counter()
    await asyncio.wait_for(asyncio.gather(run_batch(), run_batch()), timeout=2.0)
    elapsed = time.perf_counter() - t0

    assert elapsed >= 0.12


# -- RAG-RANK-01: sync score() and constructor kwargs ----------------------------


class _Predicts:
    def __init__(self, out):
        self.out = out

    def predict(self, pairs, **kwargs):  # noqa: ARG002
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def test_score_is_none_when_the_model_is_missing(monkeypatch):
    monkeypatch.setattr(BGEReranker, "_get_cross_encoder", lambda self: None)
    assert BGEReranker().score("q", ["a", "b"]) is None


@pytest.mark.parametrize(
    "out",
    [RuntimeError("predict failed"), [0.5, float("nan")], [0.5], [[0.1, 0.2, 0.3]]],
    ids=["raises", "non-finite", "too-few", "too-many"],
)
def test_score_is_none_on_unusable_output(monkeypatch, out):
    model = _Predicts(out)
    monkeypatch.setattr(BGEReranker, "_get_cross_encoder", lambda self: model)
    assert BGEReranker().score("q", ["a", "b"]) is None


def test_score_returns_floats(monkeypatch):
    import numpy as np

    model = _Predicts(np.array([0.25, -1.5]))
    monkeypatch.setattr(BGEReranker, "_get_cross_encoder", lambda self: model)
    scores = BGEReranker().score("q", ["a", "b"])
    assert scores == [0.25, -1.5]
    assert all(type(s) is float for s in scores)


def _recording_cross_encoder(monkeypatch):
    import sys
    import types

    calls = []

    class CrossEncoder:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    fake = types.ModuleType("sentence_transformers")
    fake.CrossEncoder = CrossEncoder
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return calls


def test_no_model_kwargs_constructs_exactly_as_before(monkeypatch):
    """/search builds BGEReranker() with no kwargs; its CrossEncoder call is unchanged."""
    calls = _recording_cross_encoder(monkeypatch)
    assert BGEReranker().ensure_loaded() is True
    assert calls == [(("BAAI/bge-reranker-v2-m3",), {"device": None})]


def test_model_kwargs_reach_the_constructor(monkeypatch):
    calls = _recording_cross_encoder(monkeypatch)
    r = BGEReranker("/abs/dir", model_kwargs={"local_files_only": True, "max_length": 512})
    assert r.ensure_loaded() is True
    assert calls == [(("/abs/dir",), {"device": None, "local_files_only": True, "max_length": 512})]
