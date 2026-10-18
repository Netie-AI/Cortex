"""Document answers: retrieve passages, then answer only from them, with citations.

Retrieve-then-read, as in Meta's RAG and its CRAG benchmark: rank passages
(BM25 over chunks), gate on relevance, let the model answer only from the
numbered passages and cite them, and let it say the passages do not answer.
A missing answer costs less than an invented one, so a miss serves nothing.

With no model available the answer quotes the best-matching sentences and says
it is a quote. The answer always names its sources and how it was produced.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

NOT_IN_DOCUMENTS = "NOT_IN_DOCUMENTS"
TASK_DOC_ANSWER = "doc-answer"
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_CITE = re.compile(r"\[(\d+)\]")
# Function words carry no evidence that a passage answers the question.
_STOP = frozenset(
    "a an the and or to of in on for with from per under about what which how does do "
    "did is are was were our my your this that say says state states according explain "
    "tell me who when where why there their they it its be by as at can could would "
    "should will we us any all some list show give get have has".split()
)
_MAX_PASSAGE = 600
_TOP_K = 4
_MIN_TERMS = 2
_QUOTE_COVERAGE = 0.5

Complete = Callable[[list[dict[str, str]]], "ModelReply"]


@dataclass(frozen=True)
class Passage:
    source: str
    text: str


@dataclass(frozen=True)
class ModelReply:
    ok: bool
    text: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DocAnswer:
    answer: str
    sources: list[str]
    method: str


def terms(text: str) -> list[str]:
    """Content terms, with a plural folded so "terms" matches "term"."""
    out = []
    for tok in _TOKEN.findall((text or "").lower()):
        if tok in _STOP or len(tok) < 3:
            continue
        out.append(tok[:-1] if len(tok) > 4 and tok.endswith("s") else tok)
    return out


def chunk(source: str, text: str, *, max_chars: int = _MAX_PASSAGE) -> list[Passage]:
    """Line-aligned passages of at most ``max_chars``."""
    passages: list[Passage] = []
    buf: list[str] = []
    size = 0
    for line in (ln.strip() for ln in (text or "").splitlines()):
        if not line:
            continue
        if buf and size + len(line) > max_chars:
            passages.append(Passage(source, "\n".join(buf)))
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        passages.append(Passage(source, "\n".join(buf)))
    return passages


def bm25(query: str, passages: list[Passage], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    """Okapi BM25 score of each passage for ``query``."""
    docs = [Counter(terms(p.text)) for p in passages]
    n = len(docs)
    if not n:
        return []
    avg = sum(sum(d.values()) for d in docs) / n or 1.0
    df: Counter[str] = Counter()
    for d in docs:
        df.update(d.keys())
    scores = []
    for d in docs:
        length = sum(d.values())
        score = 0.0
        for t in set(terms(query)):
            tf = d.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / avg))
        scores.append(score)
    return scores


def matched_terms(query: str, text: str) -> set[str]:
    return set(terms(query)) & set(terms(text))


def coverage(query: str, text: str) -> float:
    wanted = set(terms(query))
    return len(matched_terms(query, text)) / len(wanted) if wanted else 0.0


def is_relevant(query: str, text: str) -> bool:
    """At least two of the question's content terms (one if it has only one)."""
    wanted = set(terms(query))
    if not wanted:
        return False
    return len(matched_terms(query, text)) >= min(_MIN_TERMS, len(wanted))


def retrieve(query: str, passages: list[Passage], *, top_k: int = _TOP_K) -> list[Passage]:
    """Relevant passages, best first. Empty when nothing clears the gate."""
    scored = [
        (score, i, p)
        for i, (score, p) in enumerate(zip(bm25(query, passages), passages, strict=True))
        if score > 0 and is_relevant(query, p.text)
    ]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [p for _, _, p in scored[:top_k]]


def load_passages(paths: Iterable[Path]) -> list[Passage]:
    passages: list[Passage] = []
    for path in sorted(paths):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        passages.extend(chunk(path.name, text))
    return passages


def _sources(passages: list[Passage]) -> list[str]:
    return list(dict.fromkeys(p.source for p in passages))


def _source_line(passages: list[Passage]) -> str:
    return "Sources: " + "; ".join(f"[{i}] {p.source}" for i, p in enumerate(passages, 1))


def messages(question: str, passages: list[Passage]) -> list[dict[str, str]]:
    numbered = "\n\n".join(f"[{i}] ({p.source})\n{p.text}" for i, p in enumerate(passages, 1))
    return [
        {
            "role": "system",
            "content": (
                "Answer the question using only the numbered passages. Cite every "
                "claim with its passage number, like [1]. Do not use outside "
                "knowledge. If the passages do not answer the question, reply "
                f"exactly {NOT_IN_DOCUMENTS}."
            ),
        },
        {"role": "user", "content": f"Passages:\n{numbered}\n\nQuestion: {question}"},
    ]


def cited_answer(text: str, n_passages: int) -> str | None:
    """The model text if it cites only passages it was given, else None."""
    body = (text or "").strip()
    cites = {int(c) for c in _CITE.findall(body)}
    if not body or not cites or not cites <= set(range(1, n_passages + 1)):
        return None
    return body


def _best_sentences(
    question: str, passages: list[Passage], *, limit: int = 2
) -> list[tuple[int, str]]:
    ranked: list[tuple[int, int, int, str]] = []
    for i, p in enumerate(passages, 1):
        for j, sentence in enumerate(s.strip() for s in _SENTENCE.split(p.text)):
            hits = len(matched_terms(question, sentence))
            if sentence and hits:
                ranked.append((-hits, i, j, sentence))
    ranked.sort()
    return [(i, s) for _, i, _, s in ranked[:limit]]


def extractive(question: str, passages: list[Passage], reason: str) -> DocAnswer | None:
    """Quote the best sentences. No reader judges answerability here, so the
    passages must cover at least half of the question's terms."""
    passages = [p for p in passages if coverage(question, p.text) >= _QUOTE_COVERAGE]
    picked = _best_sentences(question, passages)
    if not picked:
        return None
    quote = " ".join(f'"{s}" [{i}]' for i, s in picked)
    note = f"Quoted from the documents; no model summarised them ({reason})."
    return DocAnswer(
        answer=f"{quote}\n{_source_line(passages)}\n{note}",
        sources=_sources(passages),
        method="extractive",
    )


def answer(question: str, passages: list[Passage], complete: Complete | None) -> DocAnswer | None:
    """A cited answer from ``passages``, or None when they do not answer it."""
    top = retrieve(question, passages)
    if not top:
        return None
    if complete is None:
        return extractive(question, top, "no model configured")
    reply = complete(messages(question, top))
    if not reply.ok:
        return extractive(question, top, reply.reason or "model unavailable")
    if NOT_IN_DOCUMENTS in reply.text:
        return None
    body = cited_answer(reply.text, len(top))
    if body is None:
        return extractive(question, top, "model answer cited no given passage")
    return DocAnswer(answer=f"{body}\n{_source_line(top)}", sources=_sources(top), method="model")
