"""RAG-RANK-01 unit tests: ranking only reorders passages that pass the RAG-01 gate.

Models are fakes injected through the ``TextEmbedder`` / ``PairScorer``
protocols (CI has no weights). The served-envelope assertions for the same
behaviour are in ``tests/dms/test_rag_rank_envelope.py``.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any

import numpy as np
import pytest

from CortexOS.dms.query_service import CONTRACTS_DIR
from CortexOS.rag import doc_rank, documents
from CortexOS.rag.documents import Passage

BOTH = frozenset({"dense", "rerank"})
Q = "what is the late delivery penalty"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch, tmp_path: Any):
    """Each test starts with no loaded model and no weights on disk."""
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-empty"))
    doc_rank.reset_for_tests()
    yield
    doc_rank.reset_for_tests()


# -- fakes ------------------------------------------------------------------


class FakeEmbedder:
    """Vectors from ``vec(text)``; records every batch it is asked to encode."""

    def __init__(self, vec: Any) -> None:
        self.vec = vec
        self.calls: list[list[str]] = []

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [list(self.vec(t)) for t in texts]


class FakeScorer:
    def __init__(self, fn: Any) -> None:
        self.fn = fn
        self.calls: list[list[str]] = []

    def score(self, query: str, texts: list[str]) -> list[float] | None:
        self.calls.append(list(texts))
        return [float(self.fn(t)) for t in texts]


class LoaderSpy:
    def __init__(self, result: tuple[Any, str] = (None, "weights_missing")) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> tuple[Any, str]:
        self.calls += 1
        return self.result


def _spy_loaders(monkeypatch: pytest.MonkeyPatch) -> tuple[LoaderSpy, LoaderSpy]:
    emb, sc = LoaderSpy(), LoaderSpy()
    monkeypatch.setattr(doc_rank, "embedder", emb)
    monkeypatch.setattr(doc_rank, "scorer", sc)
    return emb, sc


def _unit(cos: float) -> list[float]:
    """A 2-d unit vector at cosine ``cos`` to the query vector [1, 0]."""
    return [cos, math.sqrt(max(0.0, 1.0 - cos * cos))]


def _label(p: Passage) -> str:
    return f"{p.source}\n{p.text}"


# -- corpora ------------------------------------------------------------------


def _sample() -> list[Passage]:
    return documents.load_passages(CONTRACTS_DIR.glob("*.txt"))


def _three_files() -> list[Passage]:
    return [
        Passage("a.txt", "Late delivery penalty clauses are reviewed by the late delivery penalty board."),
        Passage("a.txt", "Acme overview. Acme ships fasteners."),
        Passage("b.txt", "Late delivery penalty: 2% of the order value per week."),
        Passage("b.txt", "A late delivery notice is due within two days; penalty waivers need consent."),
        Passage("c.txt", "Delivery is late after the agreed window; the penalty table is in annex B."),
        Passage("c.txt", "Penalty interest on late payment is 1% per month."),
        Passage("c.txt", "Late delivery of samples carries no penalty."),
        Passage("c.txt", "Warehouse opening hours are 8am to 6pm."),
    ]


def _ranked_six() -> tuple[list[Passage], list[Passage]]:
    """Six gated passages, listed in the reverse of their BM25 order.

    Returns (passages as listed, the same passages in BM25 order). Listing
    them reversed means a tie broken by passage index lands differently from
    a tie broken by BM25 rank.
    """
    base = [
        Passage("r.txt", "late delivery penalty " * 4),
        Passage("r.txt", "late delivery penalty " * 3 + "of the order"),
        Passage("r.txt", "late delivery penalty " * 2 + "for every week of the order value"),
        Passage("r.txt", "late delivery penalty applies per week to the order value in full"),
        Passage("r.txt", "a late delivery is charged per week against the order value as agreed"),
        Passage("r.txt", "the penalty for a delivery is charged per week against the order value as agreed here"),
    ]
    by_bm25 = [base[i] for i in documents.gated_ranking(Q, base)]
    assert len(by_bm25) == 6
    listed = list(reversed(by_bm25))
    assert [listed[i] for i in documents.gated_ranking(Q, listed)] == by_bm25
    return listed, by_bm25


_RARE = [f"zq{chr(97 + i)}rare" for i in range(25)]
_DISTRACTOR_Q = "late delivery penalty " + " ".join(_RARE)


def _distractor_corpus() -> tuple[list[Passage], list[Passage]]:
    """25 passages with one rare question term each (high BM25, fail the gate)
    before 6 gated passages that BM25 ranks below every one of them."""
    distractors = [Passage(f"d{i:02d}.txt", f"{w} {w} {w}") for i, w in enumerate(_RARE)]
    gated = [
        Passage(f"g{i}.txt", "the late delivery penalty is set out in schedule " + "x " * (10 * i + 30))
        for i in range(6)
    ]
    passages = distractors + gated
    raw = documents.bm25(_DISTRACTOR_Q, passages)
    assert min(raw[:25]) > max(raw[25:]) > 0, "the distractors must outrank the gated passages"
    assert not any(documents.is_relevant(_DISTRACTOR_Q, p.text) for p in distractors)
    assert all(documents.is_relevant(_DISTRACTOR_Q, p.text) for p in gated)
    return passages, gated


# -- flag ---------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "0", "off", " OFF ", "  "])
def test_flag_off_values(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(doc_rank.RANK_ENV, raising=False)
    else:
        monkeypatch.setenv(doc_rank.RANK_ENV, value)
    assert doc_rank.stages_from_env() == frozenset()


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("dense", {"dense"}),
        ("rerank", {"rerank"}),
        ("dense,rerank", {"dense", "rerank"}),
        (" Rerank , DENSE ", {"dense", "rerank"}),
        ("dense,,", {"dense"}),
    ],
)
def test_flag_valid_sets(monkeypatch: pytest.MonkeyPatch, value: str, want: set[str]) -> None:
    monkeypatch.setenv(doc_rank.RANK_ENV, value)
    assert doc_rank.stages_from_env() == frozenset(want)


@pytest.mark.parametrize("value", ["1", "true", "dense,colbert", "on", ","])
def test_flag_unknown_token_is_off_with_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, value: str
) -> None:
    monkeypatch.setenv(doc_rank.RANK_ENV, value)
    with caplog.at_level(logging.WARNING, logger="CortexOS.rag.doc_rank"):
        assert doc_rank.stages_from_env() == frozenset()
    assert any(r.levelno == logging.WARNING and doc_rank.RANK_ENV in r.getMessage() for r in caplog.records)


# -- models absent: today's order ----------------------------------------------


@pytest.mark.parametrize("corpus", ["sample", "three_files", "distractors"])
def test_models_absent_select_equals_retrieve(corpus: str) -> None:
    if corpus == "sample":
        passages, q = _sample(), "according to the Acme Corp agreement, what are the payment terms?"
        assert passages, CONTRACTS_DIR
    elif corpus == "three_files":
        passages, q = _three_files(), Q
    else:
        passages, q = _distractor_corpus()[0], _DISTRACTOR_Q
    for stages in (frozenset({"dense"}), frozenset({"rerank"}), BOTH):
        sel = doc_rank.select(q, passages, stages)
        assert sel.top == documents.retrieve(q, passages)
        assert sel.top


def test_models_absent_by_loader_keep_bm25_order_and_say_so() -> None:
    """No injection: the real loaders find no weights under the empty HF_HOME."""
    passages = _three_files()

    sel = doc_rank.select(Q, passages, BOTH)

    assert sel.top == documents.retrieve(Q, passages)
    assert sel.trace.skipped == {"dense": "weights_missing", "rerank": "weights_missing"}
    assert sel.note == (
        "Ranking: keyword match only. The local embedding model did not run (weights not installed)."
        " The local reranker did not run (weights not installed)."
    )


# -- the gate runs before any depth cut ----------------------------------------


def test_gate_before_depth() -> None:
    passages, gated = _distractor_corpus()
    distractor_texts = {_label(p) for p in passages[:25]}

    absent = doc_rank.select(_DISTRACTOR_Q, passages, BOTH, embedder=None, scorer=None)
    assert absent.trace.pool == 6
    assert absent.top == documents.retrieve(_DISTRACTOR_Q, passages) == gated[:4]

    loud = FakeScorer(lambda t: 99.0 if t in distractor_texts else float(len(t) % 7))
    ranked = doc_rank.select(_DISTRACTOR_Q, passages, frozenset({"rerank"}), scorer=loud)

    assert ranked.trace.ran == ["rerank"]
    assert ranked.trace.pool == 6
    assert len(ranked.top) == 4
    assert set(ranked.top) <= set(gated), [p.source for p in ranked.top]
    assert all(t not in distractor_texts for call in loud.calls for t in call)


def test_strict_property_over_random_scores() -> None:
    """Whatever the models say, every shown passage cleared the RAG-01 gate."""
    corpora = [(_distractor_corpus()[0], _DISTRACTOR_Q), (_three_files(), Q), (_ranked_six()[0], Q)]
    rng = random.Random(20261030)
    for _ in range(200):
        passages, q = corpora[rng.randrange(len(corpora))]
        vec = {(_label(p)): [rng.uniform(-1, 1) for _ in range(4)] for p in passages}
        qvec = [rng.uniform(-1, 1) for _ in range(4)]
        emb = FakeEmbedder(lambda t, vec=vec, qvec=qvec: vec.get(t, qvec))
        scores = {_label(p): rng.uniform(-50, 50) for p in passages}
        sc = FakeScorer(lambda t, scores=scores: scores[t])

        sel = doc_rank.select(q, passages, BOTH, embedder=emb, scorer=sc)

        raw = dict(zip(passages, documents.bm25(q, passages), strict=True))
        for p in sel.top:
            assert raw[p] > 0 and documents.is_relevant(q, p.text), p


# -- fusion and ties ------------------------------------------------------------


def _dense_by_rank(by_bm25: list[Passage], cos_by_rank: list[float]) -> FakeEmbedder:
    vec = {_label(p): _unit(c) for p, c in zip(by_bm25, cos_by_rank, strict=True)}
    return FakeEmbedder(lambda t: vec.get(t, [1.0, 0.0]))


def test_rrf_places_x_before_y() -> None:
    """X: BM25 rank 3, dense rank 1 -> 1/63 + 1/61 = 0.032266.
    Y: BM25 rank 2, dense rank 5 -> 1/62 + 1/65 = 0.031514."""
    listed, by_bm25 = _ranked_six()
    # Dense order by BM25 rank (0-based): [2, 0, 3, 4, 1, 5].
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])
    x, y = by_bm25[2], by_bm25[1]

    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)

    assert sel.trace.ran == ["dense"]
    assert math.isclose(1 / 63 + 1 / 61, 0.032266, abs_tol=1e-6)
    assert math.isclose(1 / 62 + 1 / 65, 0.031514, abs_tol=1e-6)
    assert sel.top == [by_bm25[0], x, y, by_bm25[3]]
    assert sel.top.index(x) < sel.top.index(y)


def test_dense_tie_breaks_by_bm25_rank() -> None:
    listed, by_bm25 = _ranked_six()
    # BM25 ranks 1 and 2 tie on dense. By BM25 rank the dense order is
    # [1, 2, 0, 3, 4, 5] and the fused order starts 1 (1/62+1/61) then 0.
    # Broken by passage index (rank 2 is listed before rank 1) the dense
    # order is [2, 1, ...] and rank 2 (1/63+1/61) beats rank 1 (2/62).
    emb = _dense_by_rank(by_bm25, [0.5, 0.9, 0.9, 0.4, 0.3, 0.1])

    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)

    assert sel.top[:3] == [by_bm25[1], by_bm25[0], by_bm25[2]], [by_bm25.index(p) for p in sel.top]


def test_fused_tie_keeps_bm25_rank() -> None:
    listed, by_bm25 = _ranked_six()
    # Dense swaps BM25 ranks 0 and 1: both fuse to 1/61 + 1/62 exactly.
    emb = _dense_by_rank(by_bm25, [0.8, 0.9, 0.5, 0.4, 0.3, 0.2])

    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)

    assert sel.top[:2] == [by_bm25[0], by_bm25[1]]


def test_rerank_tie_breaks_by_fused_rank() -> None:
    listed, by_bm25 = _ranked_six()
    score = {_label(p): s for p, s in zip(by_bm25, [1.0, 5.0, 5.0, 0.0, 0.5, 0.2], strict=True)}

    sel = doc_rank.select(Q, listed, frozenset({"rerank"}), scorer=FakeScorer(lambda t: score[t]))

    assert sel.trace.ran == ["rerank"]
    assert sel.top == [by_bm25[1], by_bm25[2], by_bm25[0], by_bm25[4]]


def test_rerank_scores_the_fused_order() -> None:
    listed, by_bm25 = _ranked_six()
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])
    sc = FakeScorer(lambda t: 1.0 if t == _label(by_bm25[5]) else 0.0)

    sel = doc_rank.select(Q, listed, BOTH, embedder=emb, scorer=sc)

    [texts] = sc.calls
    assert texts[:3] == [_label(by_bm25[0]), _label(by_bm25[2]), _label(by_bm25[1])]
    assert sel.top[0] == by_bm25[5]
    assert sel.trace.ran == ["dense", "rerank"] and sel.note == ""


# -- degenerate and error output --------------------------------------------------


def test_constant_vectors_are_degenerate() -> None:
    passages = _three_files()
    emb = FakeEmbedder(lambda t: [0.1] * 384)

    sel = doc_rank.select(Q, passages, frozenset({"dense"}), embedder=emb)

    assert sel.trace.skipped == {"dense": "degenerate"}
    assert sel.top == documents.retrieve(Q, passages)


def test_conftest_mock_transformer_is_degenerate() -> None:
    """The suite's MockTransformer returns one constant vector for every text."""
    from CortexOS.nlp.embedder_bge import BGEM3Embedder

    passages = _three_files()
    sel = doc_rank.select(Q, passages, frozenset({"dense"}), embedder=BGEM3Embedder("mock"))

    assert sel.trace.skipped == {"dense": "degenerate"}
    assert sel.top == documents.retrieve(Q, passages)


def test_non_finite_vectors_are_degenerate() -> None:
    passages = _three_files()
    emb = FakeEmbedder(lambda t: [float("nan"), 1.0])

    sel = doc_rank.select(Q, passages, frozenset({"dense"}), embedder=emb)

    assert sel.trace.skipped == {"dense": "degenerate"}
    assert sel.top == documents.retrieve(Q, passages)


def test_encode_raise_is_encode_error() -> None:
    class Boom:
        def encode_batch(self, texts: list[str]) -> Any:
            raise RuntimeError("cuda gone")

    passages = _three_files()
    sel = doc_rank.select(Q, passages, frozenset({"dense"}), embedder=Boom())

    assert sel.trace.skipped == {"dense": "encode_error"}
    assert sel.top == documents.retrieve(Q, passages)


@pytest.mark.parametrize(
    "result",
    [None, "raise", [1.0, 2.0], [float("inf")] * 6],
    ids=["none", "raise", "wrong-length", "non-finite"],
)
def test_scorer_failure_is_predict_error(result: Any) -> None:
    class Bad:
        def score(self, query: str, texts: list[str]) -> Any:
            if result == "raise":
                raise RuntimeError("predict failed")
            return result

    passages = _three_files()
    sel = doc_rank.select(Q, passages, frozenset({"rerank"}), scorer=Bad())

    assert sel.trace.skipped == {"rerank": "predict_error"}
    assert sel.top == documents.retrieve(Q, passages)


def test_constant_rerank_scores_are_degenerate() -> None:
    passages = _three_files()
    sel = doc_rank.select(Q, passages, frozenset({"rerank"}), scorer=FakeScorer(lambda t: 0.5))

    assert sel.trace.skipped == {"rerank": "degenerate"}
    assert sel.top == documents.retrieve(Q, passages)


# -- small pool -------------------------------------------------------------------


def test_small_pool_touches_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    emb, sc = _spy_loaders(monkeypatch)
    passages = _sample()
    q = "according to the Acme Corp agreement, what are the payment terms?"

    sel = doc_rank.select(q, passages, BOTH)

    assert emb.calls == 0 and sc.calls == 0
    assert sel.trace.skipped == {"dense": "small_pool", "rerank": "small_pool"}
    assert sel.note == ""
    assert sel.top == documents.retrieve(q, passages)


def test_pool_of_four_touches_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    emb, sc = _spy_loaders(monkeypatch)
    passages = _ranked_six()[1][:4]

    sel = doc_rank.select(Q, passages, BOTH)

    assert sel.trace.pool == 4
    assert emb.calls == 0 and sc.calls == 0 and sel.note == ""


def test_pool_of_five_calls_the_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    emb, sc = _spy_loaders(monkeypatch)
    passages = _ranked_six()[1][:5]

    sel = doc_rank.select(Q, passages, BOTH)

    assert emb.calls == 1 and sc.calls == 1
    assert sel.top == documents.retrieve(Q, passages)
    assert sel.note.startswith("Ranking: keyword match only.")


# -- vector cache -----------------------------------------------------------------


def test_second_call_encodes_only_the_query() -> None:
    listed, by_bm25 = _ranked_six()
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])

    first = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)
    second = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)

    assert emb.calls[0] and len(emb.calls[0]) == 6
    assert emb.calls[2:] == [[Q]]
    assert first.trace.cache == {"hits": 0, "misses": 6, "evictions": 0}
    assert second.trace.cache == {"hits": 6, "misses": 0, "evictions": 0}
    assert first.top == second.top


def test_new_embedder_instance_gets_a_fresh_cache() -> None:
    listed, by_bm25 = _ranked_six()
    a = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])
    b = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])

    doc_rank.select(Q, listed, frozenset({"dense"}), embedder=a)
    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=b)

    assert len(b.calls[0]) == 6
    assert sel.trace.cache["misses"] == 6


def test_cache_cap_counts_evictions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_rank, "_CACHE_MAX", 3)
    listed, by_bm25 = _ranked_six()
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])

    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)

    assert sel.trace.cache["evictions"] == 3
    assert sel.trace.ran == ["dense"]


# -- the note --------------------------------------------------------------------


def test_note_both_missing() -> None:
    sel = doc_rank.select(Q, _three_files(), BOTH, embedder=None, scorer=None)
    assert sel.note == (
        "Ranking: keyword match only. The local embedding model did not run (weights not installed)."
        " The local reranker did not run (weights not installed)."
    )


def test_note_dense_ran_rerank_missing() -> None:
    listed, by_bm25 = _ranked_six()
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])
    sel = doc_rank.select(Q, listed, BOTH, embedder=emb, scorer=None)
    assert sel.note == (
        "Ranking: keyword match + local embeddings. The local reranker did not run (weights not installed)."
    )


def test_note_rerank_ran_dense_missing() -> None:
    listed, by_bm25 = _ranked_six()
    score = {_label(p): float(i) for i, p in enumerate(by_bm25)}
    sel = doc_rank.select(Q, listed, BOTH, embedder=None, scorer=FakeScorer(lambda t: score[t]))
    assert sel.note == (
        "Ranking: keyword match + local reranker. The local embedding model did not run (weights not installed)."
    )


def test_note_degenerate_and_model_error() -> None:
    class NoScore:
        def score(self, query: str, texts: list[str]) -> None:
            return None

    sel = doc_rank.select(Q, _three_files(), BOTH, embedder=FakeEmbedder(lambda t: [1.0, 1.0]), scorer=NoScore())
    assert sel.note == (
        "Ranking: keyword match only. The local embedding model did not run (model output unusable)."
        " The local reranker did not run (model error)."
    )


@pytest.mark.parametrize(
    ("code", "why"),
    [("extra_missing", "not installed on this host"), ("load_error", "could not load"), ("encode_error", "model error")],
)
def test_note_reason_text(code: str, why: str) -> None:
    trace = doc_rank.Trace(requested=["dense"], skipped={"dense": code})
    assert doc_rank.note_for(trace) == f"Ranking: keyword match only. The local embedding model did not run ({why})."


def test_no_note_when_every_requested_stage_ran() -> None:
    listed, by_bm25 = _ranked_six()
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])
    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)
    assert sel.note == "" and sel.trace.skipped == {}


# -- trace and warnings -----------------------------------------------------------------


def test_one_trace_record_per_call_and_rate_limited_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = [1000.0]
    monkeypatch.setattr(doc_rank, "_clock", lambda: now[0])
    passages = _three_files()

    with caplog.at_level(logging.INFO, logger="CortexOS.rag.doc_rank"):
        for _ in range(3):
            doc_rank.select(Q, passages, frozenset({"dense"}), embedder=None)
        now[0] += doc_rank._WARN_EVERY_S + 1
        doc_rank.select(Q, passages, frozenset({"dense"}), embedder=None)

    traces = [r for r in caplog.records if r.levelno == logging.INFO and "doc_rank_trace" in r.getMessage()]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(traces) == 4
    assert '"weights_missing"' in traces[0].getMessage()
    assert len(warnings) == 2, [w.getMessage() for w in warnings]
    assert all("dense" in w.getMessage() and "weights_missing" in w.getMessage() for w in warnings)


def test_small_pool_logs_a_trace_and_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="CortexOS.rag.doc_rank"):
        doc_rank.select("payment terms acme", _sample(), BOTH)
    assert [r.levelno for r in caplog.records] == [logging.INFO]
    assert "doc_rank_trace" in caplog.records[0].getMessage()


def test_trace_fields() -> None:
    listed, by_bm25 = _ranked_six()
    emb = _dense_by_rank(by_bm25, [0.8, 0.4, 0.9, 0.7, 0.6, 0.1])

    sel = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=emb)

    t = sel.trace
    assert len(t.q_sha16) == 16 and t.requested == ["dense"] and t.pool == 6
    assert len(t.ranked_sha12) == 6 and all(len(h) == 12 for h in t.ranked_sha12)
    assert [s for s, _ in t.shown] == [p.source for p in sel.top]
    assert {"bm25", "embed_passages", "embed_query", "total"} <= set(t.ms)


# -- every golden question on the sample corpus -------------------------------------------


def test_every_golden_question_selects_what_retrieve_selects(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.dms.test_ask_flag_off_golden_340 import _questions

    emb, sc = _spy_loaders(monkeypatch)
    passages = _sample()
    questions = _questions()
    assert len(questions) > 150

    for _qid, q in questions:
        assert doc_rank.select(q, passages, BOTH).top == documents.retrieve(q, passages), q

    assert emb.calls == 0 and sc.calls == 0


def test_select_returns_passages_not_copies() -> None:
    passages = _three_files()
    sel = doc_rank.select(Q, passages, BOTH, embedder=None, scorer=None)
    assert all(any(p is q for q in passages) for p in sel.top)
    assert np.isfinite(sel.trace.ms["total"])


def test_unusable_vectors_are_not_cached() -> None:
    listed, by_bm25 = _ranked_six()
    bad = FakeEmbedder(lambda t: [float("nan"), 1.0])
    assert doc_rank.select(Q, listed, frozenset({"dense"}), embedder=bad).trace.skipped == {"dense": "degenerate"}

    second = doc_rank.select(Q, listed, frozenset({"dense"}), embedder=bad)

    assert second.trace.cache["hits"] == 0
    assert len(bad.calls) == 2 and all(len(c) == 6 for c in bad.calls)
