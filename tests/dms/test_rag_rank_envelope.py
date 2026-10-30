"""RAG-RANK-01 on the served envelopes (CLAUDE.md section 8).

With ``CORTEX_DOC_RANK`` set, local models reorder the passages that clear the
RAG-01 gate before the reader model sees them. Every test asks both
``POST /v1/contract/ask`` and ``POST /dms/query`` and asserts what the customer
receives: badge, layer, rendered answer text, rows, source cards, plus the
passages the reader model was shown.

CI has no weights. Models are fakes injected by replacing the loaders, or the
suite's ``MockTransformer`` loaded from a placeholder snapshot tree, or absent
(an empty ``HF_HOME``).

A document miss (ruling 1, CLAUDE.md section 9) is not pinned to a badge: the
lane router may send it down the SQL ladder. Those tests assert only that no
document answer is served.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from CortexOS.dms import query_service
from CortexOS.dms.query_service import CONTRACTS_DIR
from CortexOS.rag import doc_rank, documents
from tests.dms.test_ask_flag_off_golden_340 import VOLATILE_KEYS, _questions
from tests.dms.test_rag03_answer_routing import (  # noqa: F401
    DOC_HIT_Q,
    SESSION,
    SOP_MISS_Q,
    _ask_dms,
    _badge,
    _sources,
    dms_http,
)

SPACE = "alpha"
RANK_Q = "what is the delivery penalty per week under the agreement"
PENALTY = "Late delivery penalty: 2% of the order value per week."
REPLY = "The late delivery penalty is 2% of the order value per week [1]."
SAMPLE_REPLY = "Invoices are payable Net 30 from the invoice date [1]."
NOTE_MISSING = (
    "Ranking: keyword match only. The local embedding model did not run (weights not installed)."
    " The local reranker did not run (weights not installed)."
)
CORPUS_FILES = ("a.txt", "b.txt", "c.txt")
# Each line is padded past half the chunk cap, so every line is its own passage.
_PAD = " Clause reference SA-2026 records this paragraph for the file." * 5
_CORPUS = {
    "a.txt": [
        "Delivery penalty overview: the delivery penalty schedule and the penalty per week are set by the agreement.",
        "Acme overview: Acme ships steel fasteners and hydraulic components.",
    ],
    "b.txt": [
        PENALTY,
        "Delivery windows: a delivery is on time within the agreed window; penalty notices go out weekly.",
    ],
    "c.txt": [
        "Penalty interest on late payment is 1% per month; delivery terms are FOB.",
        "Agreement term: this agreement renews each year; delivery addresses are in annex A.",
        "Delivery of samples carries no penalty under this agreement.",
    ],
}
ADVERSARIAL_QIDS = (
    "golden:fallback_best_price",
    "adversarial:best_price_no_fallback",
    "adversarial:esg_weather",
    "paraphrase:fallback_turnover#0",
    "paraphrase:fallback_turnover#1",
)


@pytest.fixture(autouse=True)
def _rank_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("CORTEX_PROFILE", raising=False)
    monkeypatch.delenv(doc_rank.RANK_ENV, raising=False)
    empty = tmp_path / "hf-empty"
    empty.mkdir()
    monkeypatch.setenv("HF_HOME", str(empty))
    doc_rank.reset_for_tests()
    yield
    doc_rank.reset_for_tests()


@pytest.fixture
def ranked_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[documents.Passage]:
    folder = tmp_path / "contracts"
    folder.mkdir()
    for name, lines in _CORPUS.items():
        (folder / name).write_text("\n".join(line + _PAD for line in lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(query_service, "CONTRACTS_DIR", folder)
    passages = documents.load_passages(folder.glob("*.txt"))
    assert len(passages) == 7 and all(len(p.text.splitlines()) == 1 for p in passages)
    gated = documents.gated_ranking(RANK_Q, passages)
    assert len(gated) == 6, "the pool must exceed four so ranking runs"
    assert passages[gated[0]].source == "a.txt", "BM25 must rank an a.txt passage first"
    assert not passages[gated[0]].text.startswith(PENALTY)
    return passages


class _Loader:
    def __init__(self, result: tuple[Any, str]) -> None:
        self.result = result
        self.calls = 0

    def __call__(self) -> tuple[Any, str]:
        self.calls += 1
        return self.result


def _loaders(
    monkeypatch: pytest.MonkeyPatch,
    emb: tuple[Any, str] = (None, "weights_missing"),
    sc: tuple[Any, str] = (None, "weights_missing"),
) -> tuple[_Loader, _Loader]:
    e, s = _Loader(emb), _Loader(sc)
    monkeypatch.setattr(doc_rank, "embedder", e)
    monkeypatch.setattr(doc_rank, "scorer", s)
    return e, s


class _PenaltyFirst:
    """Scores only the b.txt penalty passage; the rest tie, so they keep the fused order."""

    def score(self, query: str, texts: list[str]) -> list[float]:
        return [10.0 if PENALTY in t else 0.0 for t in texts]


def _contract(client: Any, question: str) -> dict[str, Any]:
    client.bind_session(SESSION, {"transactions": "TRUE", "inventory": "TRUE"}, space_id=SPACE)
    resp = client.post("/v1/contract/ask", json={"question": question, "session_id": SESSION, "space_id": SPACE})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def _dms(client: Any, question: str) -> dict[str, Any]:
    client.bind_session(SESSION, {"transactions": "TRUE"}, space_id=SPACE)
    return _ask_dms(client, question, SESSION, SPACE)


def _doc_prompts(fake: Any) -> list[list[dict[str, str]]]:
    """Chat calls that are doc-answer prompts (the ``documents.messages`` shape)."""
    out = []
    for call in fake.chat_calls:
        msgs = call["body"].get("messages") or []
        if any(m.get("role") == "user" and str(m.get("content", "")).startswith("Passages:\n") for m in msgs):
            out.append(msgs)
    return out


def _ask_both(client: Any, fake: Any, question: str, reply: str | None) -> tuple[dict, dict, list, list]:
    """Ask both endpoints; return each body and the doc-answer prompts each sent."""
    before = len(_doc_prompts(fake))
    if reply is not None:
        fake.reply(reply)
    contract = _contract(client, question)
    mid = len(_doc_prompts(fake))
    if reply is not None:
        fake.reply(reply)
    dms = _dms(client, question)
    prompts = _doc_prompts(fake)
    return contract, dms, prompts[before:mid], prompts[mid:]


def _cards(body: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(c["ref_id"], c["kind"], c["container"]) for c in body.get("contributing_sources") or []]


def _source_line(top: list[documents.Passage]) -> str:
    return "Sources: " + "; ".join(f"[{i}] {p.source}" for i, p in enumerate(top, 1))


def _bm25_top(passages: list[documents.Passage], question: str = RANK_Q) -> list[documents.Passage]:
    return documents.retrieve(question, passages)


def _assert_document(contract: dict[str, Any], dms: dict[str, Any], answer: str, sources: list[str]) -> None:
    assert contract["provenance"]["badge"] == "document", contract
    assert contract["provenance"]["layer"] == "rag", contract
    assert contract["answer"] == answer, contract["answer"]
    assert _cards(contract) == [(s, "document", "documents") for s in sources]
    assert contract.get("rows") in ([], None) and contract.get("sql_used") is None
    assert contract.get("drillthrough_token") is None
    assert contract.get("audit_id")

    assert _badge(dms) == "document", dms
    assert dms.get("layer") == "rag" and dms.get("route") == "rag", dms
    assert dms.get("answer") == answer, dms.get("answer")
    assert _sources(dms) == sources
    assert dms.get("rows") == []


def _assert_no_document(contract: dict[str, Any], dms: dict[str, Any], files: tuple[str, ...]) -> None:
    """Ruling 1: a document miss is not a document answer. Badge not pinned."""
    assert contract["provenance"]["badge"] != "document", contract
    assert not [c for c in contract.get("contributing_sources") or [] if c.get("kind") == "document"]
    assert contract.get("drillthrough_token") is None
    assert contract.get("audit_id")
    for body in (contract, dms):
        text = str(body.get("answer") or "")
        assert "Sources:" not in text, text
        assert not any(f in text for f in files), text
        assert not any(f in _sources(body) for f in files), body
        assert "Ranking:" not in text, text
    assert _badge(dms) != "document", dms


# -- (a) the reranked passage is served -------------------------------------------


def test_reranked_passage_is_served(
    armed_openvault, dms_http, ranked_corpus, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(doc_rank.RANK_ENV, "rerank")
    emb, sc = _loaders(monkeypatch, sc=(_PenaltyFirst(), ""))
    bm25 = [ranked_corpus[i] for i in documents.gated_ranking(RANK_Q, ranked_corpus)]
    b_penalty = next(p for p in bm25 if p.text.startswith(PENALTY))
    top = [b_penalty] + [p for p in bm25 if p is not b_penalty][:3]
    assert top != _bm25_top(ranked_corpus)

    contract, dms, c_prompts, d_prompts = _ask_both(dms_http, armed_openvault, RANK_Q, REPLY)

    answer = f"{REPLY}\n{_source_line(top)}"
    assert answer == f"{REPLY}\nSources: [1] b.txt; [2] a.txt; [3] c.txt; [4] c.txt"
    _assert_document(contract, dms, answer, ["b.txt", "a.txt", "c.txt"])
    assert "Ranking:" not in contract["answer"]
    assert len(c_prompts) == 1 and len(d_prompts) == 1
    assert len(armed_openvault.chat_calls) == 2, "one model call per ask"
    assert c_prompts[0][1]["content"].startswith(f"Passages:\n[1] (b.txt)\n{PENALTY}")
    assert c_prompts[0] == d_prompts[0] == documents.messages(RANK_Q, top)
    assert emb.calls == 0 and sc.calls == 2


# -- (b) missing weights keep the BM25 order and say so ------------------------------


def test_missing_weights_keep_bm25_order_and_say_so(
    armed_openvault, dms_http, ranked_corpus, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    armed_openvault.reply(REPLY)
    off = _contract(dms_http, RANK_Q)
    [off_prompt] = _doc_prompts(armed_openvault)
    top = _bm25_top(ranked_corpus)
    assert off["answer"] == f"{REPLY}\n{_source_line(top)}"

    monkeypatch.setenv(doc_rank.RANK_ENV, "dense,rerank")
    with caplog.at_level(logging.WARNING, logger="CortexOS.rag.doc_rank"):
        contract, dms, c_prompts, d_prompts = _ask_both(dms_http, armed_openvault, RANK_Q, REPLY)

    assert c_prompts == d_prompts == [off_prompt]
    answer = f"{REPLY}\n{_source_line(top)}\n{NOTE_MISSING}"
    assert contract["answer"].endswith(f"\n{NOTE_MISSING}")
    _assert_document(contract, dms, answer, list(dict.fromkeys(p.source for p in top)))
    assert _cards(contract) == _cards(off)
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("weights_missing" in m for m in warned), warned


# -- (c) flag on, nothing injected, under the suite's MockTransformer --------------------


def _snapshot_tree(hf_home: Path) -> None:
    for model, pin, files in (
        (doc_rank.EMBED_MODEL, doc_rank.EMBED_REVISION, doc_rank._EMBED_FILES),
        (doc_rank.RERANK_MODEL, doc_rank.RERANK_REVISION, doc_rank._RERANK_FILES),
    ):
        snap = hf_home / "hub" / ("models--" + model.replace("/", "--")) / "snapshots" / pin
        for name in files:
            (snap / name).parent.mkdir(parents=True, exist_ok=True)
            (snap / name).write_text("{}", encoding="utf-8")


def test_mock_transformer_is_unusable_and_says_so(
    armed_openvault, dms_http, ranked_corpus, monkeypatch: pytest.MonkeyPatch, tmp_path: Path  # noqa: F811
) -> None:
    armed_openvault.reply(REPLY)
    _contract(dms_http, RANK_Q)
    [off_prompt] = _doc_prompts(armed_openvault)

    hf_home = tmp_path / "hf-tree"
    _snapshot_tree(hf_home)
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setenv(doc_rank.RANK_ENV, "dense,rerank")
    contract, dms, c_prompts, d_prompts = _ask_both(dms_http, armed_openvault, RANK_Q, REPLY)

    assert c_prompts == d_prompts == [off_prompt]
    note = (
        "Ranking: keyword match only. The local embedding model did not run (model output unusable)."
        " The local reranker did not run (model error)."
    )
    top = _bm25_top(ranked_corpus)
    _assert_document(
        contract, dms, f"{REPLY}\n{_source_line(top)}\n{note}", list(dict.fromkeys(p.source for p in top))
    )


# -- (d) NOT_IN_DOCUMENTS with ranking on ------------------------------------------------


def test_not_in_documents_with_rank_on_serves_no_document(
    armed_openvault, dms_http, ranked_corpus, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(doc_rank.RANK_ENV, "rerank")
    _loaders(monkeypatch, sc=(_PenaltyFirst(), ""))

    contract, dms, c_prompts, d_prompts = _ask_both(
        dms_http, armed_openvault, RANK_Q, documents.NOT_IN_DOCUMENTS
    )

    assert len(c_prompts) == 1 and len(d_prompts) == 1
    _assert_no_document(contract, dms, CORPUS_FILES)
    assert documents.NOT_IN_DOCUMENTS not in contract["answer"]


# -- (e) adversarial questions: ranking sends no prompt the gate did not send ----------------


@pytest.mark.parametrize("qid", [*ADVERSARIAL_QIDS, "sop_miss"])
def test_adversarials_get_no_document_and_no_extra_prompt(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch, qid: str  # noqa: F811
) -> None:
    """The five questions the stub once served from the first file, plus SOP-DOC-ZZ9.

    Four of the five, and the SOP question, clear no passage, so no doc-answer
    prompt is sent. ``paraphrase:fallback_turnover#0`` ("stock turnover per
    SKU last quarter") shares "stock" and "SKU" with the agreement, clears the
    RAG-01 gate on main and is shown to the reader model, which says the
    passages do not answer it. Ranking must change none of this: the same
    prompts as flag off, no model loaded, no document served.
    """
    assert CONTRACTS_DIR.is_dir() and any(CONTRACTS_DIR.glob("*.txt")), CONTRACTS_DIR
    question = SOP_MISS_Q if qid == "sop_miss" else dict(_questions())[qid]
    gated = documents.retrieve(question, documents.load_passages(CONTRACTS_DIR.glob("*.txt")))
    assert bool(gated) == (qid == "paraphrase:fallback_turnover#0"), qid
    reply = documents.NOT_IN_DOCUMENTS if gated else None

    off_c, off_d, off_cp, off_dp = _ask_both(dms_http, armed_openvault, question, reply)
    monkeypatch.setenv(doc_rank.RANK_ENV, "dense,rerank")
    emb, sc = _loaders(monkeypatch)
    contract, dms, c_prompts, d_prompts = _ask_both(dms_http, armed_openvault, question, reply)

    assert c_prompts == off_cp and d_prompts == off_dp
    assert len(c_prompts) == len(d_prompts) == (1 if gated else 0)
    assert emb.calls == 0 and sc.calls == 0
    _assert_no_document(contract, dms, ("acme_agreement.txt",))
    assert "acme_agreement.txt" not in {c.get("ref_id") for c in contract.get("contributing_sources") or []}
    assert _stable(contract) == _stable(off_c) and _stable(dms) == _stable(off_d)


# -- (f) flag on equals flag off on the sample corpus ----------------------------------------


def _stable(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in VOLATILE_KEYS}


def test_flag_on_equals_flag_off_on_the_sample_corpus(
    armed_openvault, dms_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    assert CONTRACTS_DIR.is_dir() and any(CONTRACTS_DIR.glob("*.txt")), CONTRACTS_DIR
    off_c, off_d, off_cp, off_dp = _ask_both(dms_http, armed_openvault, DOC_HIT_Q, SAMPLE_REPLY)
    monkeypatch.setenv(doc_rank.RANK_ENV, "dense,rerank")
    emb, sc = _loaders(monkeypatch)
    on_c, on_d, on_cp, on_dp = _ask_both(dms_http, armed_openvault, DOC_HIT_Q, SAMPLE_REPLY)

    assert _stable(on_c) == _stable(off_c)
    assert _stable(on_d) == _stable(off_d)
    assert on_cp == off_cp and on_dp == off_dp and len(on_cp) == 1
    answer = f"{SAMPLE_REPLY}\nSources: [1] acme_agreement.txt"
    _assert_document(on_c, on_d, answer, ["acme_agreement.txt"])
    assert emb.calls == 0 and sc.calls == 0


# -- (g) model errors mid-call ----------------------------------------------------------------


class _Raises:
    def encode_batch(self, texts: list[str]) -> Any:
        raise RuntimeError("embedder crashed")


class _NoScores:
    def score(self, query: str, texts: list[str]) -> None:
        return None


def test_mid_call_model_errors_keep_bm25_order_and_say_so(
    armed_openvault, dms_http, ranked_corpus, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(doc_rank.RANK_ENV, "dense,rerank")
    _loaders(monkeypatch, emb=(_Raises(), ""), sc=(_NoScores(), ""))

    contract, dms, c_prompts, d_prompts = _ask_both(dms_http, armed_openvault, RANK_Q, REPLY)

    top = _bm25_top(ranked_corpus)
    assert c_prompts == d_prompts == [documents.messages(RANK_Q, top)]
    note = (
        "Ranking: keyword match only. The local embedding model did not run (model error)."
        " The local reranker did not run (model error)."
    )
    _assert_document(
        contract, dms, f"{REPLY}\n{_source_line(top)}\n{note}", list(dict.fromkeys(p.source for p in top))
    )


# -- (h) a citation outside the shown passages ------------------------------------------------


def test_out_of_range_citation_with_rank_on_serves_no_document(
    armed_openvault, dms_http, ranked_corpus, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(doc_rank.RANK_ENV, "rerank")
    _loaders(monkeypatch, sc=(_PenaltyFirst(), ""))

    contract, dms, c_prompts, d_prompts = _ask_both(dms_http, armed_openvault, RANK_Q, "The penalty is 9% [7].")

    assert len(c_prompts) == 1 and len(d_prompts) == 1
    _assert_no_document(contract, dms, CORPUS_FILES)
    assert "9%" not in contract["answer"] and "9%" not in str(dms.get("answer"))
