"""RAG-RANK-01: local ranking of gated document passages, behind ``CORTEX_DOC_RANK``.

Founder ruling 2026-10-29 (AGENTS.md rule 5) lets the document lane run two
local models in-process: a bge-m3 embedding model and a bge-reranker-v2-m3
cross-encoder. Both only produce scores. The text of an answer is still written
only by the model behind FreeRoute (``query_service._doc_model``).

Order of work, only when the flag names a stage:

1. The RAG-01 relevance gate runs over every passage first
   (``documents.gated_ranking``). The models only reorder passages that already
   pass it; no score threshold is ever applied, so they never admit one.
2. A pool of ``_TOP_K`` or fewer gated passages is shown as-is. No model is
   loaded or called and no note is added.
3. ``dense``: question vs passage cosine, fused with the BM25 order by
   reciprocal rank fusion (BM25 list first, so ties keep BM25 rank). The fused
   order is cut to ``_DEPTH``.
4. ``rerank``: cross-encoder scores over that order; ties keep the fused rank.
5. The first ``_TOP_K`` are shown to the reader model.

A requested stage that did not run (weights not installed, extra missing, load
error, unusable output, model error) keeps the order it was given and is never
silent: the served answer carries one fixed engine line (``note_for``), a
WARNING is logged (once a minute per stage and reason), and every call logs one
INFO ``doc_rank_trace`` record.

Weights are loaded only from the absolute pinned snapshot directory
``<hub cache>/models--BAAI--<name>/snapshots/<pin>``, never from a repo id, so
the loader takes the local-directory branch and nothing is downloaded while
serving. ``DISABLE_SAFETENSORS_CONVERSION`` is forced to ``1`` before
construction as a second guard against a hub-calling conversion thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import weakref
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import numpy as np

from CortexOS.rag import documents
from CortexOS.rag.documents import Passage
from CortexOS.rag.fuser_rrf import rrf_fuse

log = logging.getLogger(__name__)

RANK_ENV = "CORTEX_DOC_RANK"
STAGES = ("dense", "rerank")

# Pinned snapshots. Constants on purpose: no env var names another model.
EMBED_MODEL = "BAAI/bge-m3"
EMBED_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"

# Every file the local loader reads from each snapshot. Any one absent means
# the weights are not installed and nothing is constructed.
_EMBED_FILES: tuple[str, ...] = (
    "modules.json",
    "config_sentence_transformers.json",
    "sentence_bert_config.json",
    "config.json",
    "pytorch_model.bin",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
    "1_Pooling/config.json",
)
_RERANK_FILES: tuple[str, ...] = (
    "config.json",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "sentencepiece.bpe.model",
)

_TOP_K = documents._TOP_K
_DEPTH = 20
_RRF_K = 60
_MAX_LEN = 512
_CACHE_MAX = 20_000
_RETRY_S = 300.0
_WARN_EVERY_S = 60.0
_DEGENERATE = 1e-6

SKIP_CODES = (
    "small_pool",
    "extra_missing",
    "weights_missing",
    "load_error",
    "degenerate",
    "encode_error",
    "predict_error",
)
_WHY = {
    "weights_missing": "weights not installed",
    "extra_missing": "not installed on this host",
    "load_error": "could not load",
    "degenerate": "model output unusable",
    "encode_error": "model error",
    "predict_error": "model error",
}
_HEAD = {
    frozenset(): "Ranking: keyword match only.",
    frozenset({"dense"}): "Ranking: keyword match + local embeddings.",
    frozenset({"rerank"}): "Ranking: keyword match + local reranker.",
}
_MISSING = {
    "dense": " The local embedding model did not run ({why}).",
    "rerank": " The local reranker did not run ({why}).",
}

_clock = time.monotonic


class TextEmbedder(Protocol):
    def encode_batch(self, texts: list[str]) -> Sequence[Sequence[float]] | np.ndarray: ...


class PairScorer(Protocol):
    def score(self, query: str, texts: list[str]) -> list[float] | None: ...


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()


@dataclass
class Trace:
    q_sha16: str = ""
    requested: list[str] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    pool: int = 0
    ranked_sha12: list[str] = field(default_factory=list)
    shown: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""
    ms: dict[str, float] = field(default_factory=dict)
    cache: dict[str, int] = field(default_factory=lambda: {"hits": 0, "misses": 0, "evictions": 0})
    load_ms: float = 0.0


@dataclass
class Selection:
    top: list[Passage]
    trace: Trace
    note: str


# -- flag -------------------------------------------------------------------


def stages_from_env() -> frozenset[str]:
    """The stages ``CORTEX_DOC_RANK`` names. Empty means today's BM25 path.

    Unset, ``""``, ``0`` and ``off`` are off. ``dense`` and ``rerank`` are
    accepted, comma-separated, in any case. Any other token turns ranking off
    and logs a WARNING, so a typo never half-enables it.
    """
    raw = os.environ.get(RANK_ENV, "")
    value = raw.strip().lower()
    if value in {"", "0", "off"}:
        return frozenset()
    tokens = {tok.strip() for tok in value.split(",") if tok.strip()}
    if not tokens or not tokens <= set(STAGES):
        _warn(("flag", value), "%s=%r is not a set of %s; document ranking is off", RANK_ENV, raw, "/".join(STAGES))
        return frozenset()
    return frozenset(tokens)


# -- loaders ----------------------------------------------------------------


def _hub_cache() -> str:
    explicit = os.environ.get("HF_HUB_CACHE")
    if explicit:
        return explicit
    home = os.environ.get("HF_HOME")
    if home:
        return os.path.join(home, "hub")
    return os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")


def _snapshot_dir(model: str, pin: str) -> str:
    """Absolute path of the pinned snapshot of ``model`` in the hub cache."""
    return os.path.abspath(
        os.path.join(_hub_cache(), "models--" + model.replace("/", "--"), "snapshots", pin)
    )


def _weights_present(path: str, files: Sequence[str]) -> bool:
    if not os.path.isdir(path):
        return False
    # isfile follows the snapshot's symlinks into blobs/.
    return all(os.path.isfile(os.path.join(path, name)) for name in files)


class _SerialEmbedder:
    """One loaded embedder; one inference at a time."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self._lock = threading.Lock()

    def encode_batch(self, texts: list[str]) -> Any:
        with self._lock:
            return self.model.encode_batch(texts)


class _SerialScorer:
    """One loaded cross-encoder; one inference at a time."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self._lock = threading.Lock()

    def score(self, query: str, texts: list[str]) -> list[float] | None:
        with self._lock:
            result: list[float] | None = self.model.score(query, texts)
            return result


@dataclass
class _Slot:
    model: Any = None
    failed_at: float | None = None


_LOAD_LOCK = threading.Lock()
_SLOTS: dict[str, _Slot] = {"dense": _Slot(), "rerank": _Slot()}


def _construct_embedder(path: str) -> Any:
    from CortexOS.nlp.embedder_bge import BGEM3Embedder

    return _SerialEmbedder(
        BGEM3Embedder(path, model_kwargs={"local_files_only": True}, max_seq_length=_MAX_LEN)
    )


def _construct_scorer(path: str) -> Any:
    from CortexOS.rag.reranker import BGEReranker

    # A fresh instance on every probe: a BGEReranker caches a load failure
    # for its lifetime. The lazy load is forced here, so a failure is a
    # load_error now and never a predict_error later.
    reranker = BGEReranker(path, model_kwargs={"local_files_only": True, "max_length": _MAX_LEN})
    if not reranker.ensure_loaded():
        raise RuntimeError("cross-encoder did not load")
    return _SerialScorer(reranker)


def _load(stage: str) -> tuple[Any, str]:
    if stage == "dense":
        model_id, pin, files, construct = EMBED_MODEL, EMBED_REVISION, _EMBED_FILES, _construct_embedder
    else:
        model_id, pin, files, construct = RERANK_MODEL, RERANK_REVISION, _RERANK_FILES, _construct_scorer
    with _LOAD_LOCK:
        slot = _SLOTS[stage]
        if slot.model is not None:
            return slot.model, ""
        if slot.failed_at is not None and _clock() - slot.failed_at < _RETRY_S:
            return None, "load_error"
        from CortexOS.packaging import extra_available

        if not extra_available("rag", modules=("sentence_transformers",)):
            return None, "extra_missing"
        path = _snapshot_dir(model_id, pin)
        if not _weights_present(path, files):
            return None, "weights_missing"
        # Second guard: no safetensors auto-conversion thread (a hub call)
        # whatever the operator set. The directory load never starts one.
        os.environ["DISABLE_SAFETENSORS_CONVERSION"] = "1"
        try:
            model = construct(path)
        except Exception as exc:
            slot.failed_at = _clock()
            log.warning("doc_rank %s model could not load: %s", stage, type(exc).__name__)
            return None, "load_error"
        slot.model, slot.failed_at = model, None
        return model, ""


def embedder() -> tuple[TextEmbedder | None, str]:
    """The local bge-m3 embedder, or None and why it is absent."""
    return _load("dense")


def scorer() -> tuple[PairScorer | None, str]:
    """The local bge-reranker-v2-m3 scorer, loaded now, or None and why it is absent."""
    return _load("rerank")


def reset_for_tests() -> None:
    with _LOAD_LOCK:
        for slot in _SLOTS.values():
            slot.model, slot.failed_at = None, None
    _WARNED.clear()


# -- vector cache -------------------------------------------------------------


class _VectorCache:
    def __init__(self) -> None:
        self.rows: OrderedDict[str, np.ndarray] = OrderedDict()

    def get(self, key: str) -> np.ndarray | None:
        row = self.rows.get(key)
        if row is not None:
            self.rows.move_to_end(key)
        return row

    def put(self, key: str, row: np.ndarray) -> int:
        self.rows[key] = row
        self.rows.move_to_end(key)
        evicted = 0
        while len(self.rows) > _CACHE_MAX:
            self.rows.popitem(last=False)
            evicted += 1
        return evicted


_CACHES: weakref.WeakKeyDictionary[Any, _VectorCache] = weakref.WeakKeyDictionary()
_CACHE_LOCK = threading.Lock()


def _cache_for(emb: Any) -> _VectorCache | None:
    with _CACHE_LOCK:
        try:
            cache = _CACHES.get(emb)
            if cache is None:
                cache = _VectorCache()
                _CACHES[emb] = cache
        except TypeError:
            return None
        return cache


# -- ranking ----------------------------------------------------------------


def _label(p: Passage) -> str:
    # The reader model sees each passage under its source name.
    return f"{p.source}\n{p.text}"


def _sha(text: str, n: int) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def _usable(scores: np.ndarray, n: int) -> bool:
    if scores.shape != (n,) or not bool(np.all(np.isfinite(scores))):
        return False
    return n < 2 or float(scores.max() - scores.min()) >= _DEGENERATE


def _dense_order(
    question: str, pool: list[Passage], order: list[int], emb: Any, trace: Trace
) -> list[int] | str:
    texts = [_label(pool[r]) for r in order]
    keys = [_sha(t, 64) for t in texts]
    cache = _cache_for(emb)
    rows: list[np.ndarray | None] = [cache.get(k) if cache is not None else None for k in keys]
    todo = [j for j, row in enumerate(rows) if row is None]
    trace.cache["hits"] += len(rows) - len(todo)
    trace.cache["misses"] += len(todo)
    try:
        t0 = time.perf_counter()
        if todo:
            fresh = np.asarray(emb.encode_batch([texts[j] for j in todo]), dtype=float)
            if fresh.ndim != 2 or fresh.shape[0] != len(todo) or not bool(np.all(np.isfinite(fresh))):
                # Never cache an unusable vector.
                return "degenerate"
            for j, row in zip(todo, fresh, strict=True):
                rows[j] = row
                if cache is not None:
                    trace.cache["evictions"] += cache.put(keys[j], row)
        trace.ms["embed_passages"] = _ms(t0)
        t0 = time.perf_counter()
        query = np.asarray(emb.encode_batch([question]), dtype=float).reshape(-1)
        trace.ms["embed_query"] = _ms(t0)
        matrix = np.vstack([np.asarray(row, dtype=float).reshape(-1) for row in rows if row is not None])
    except Exception as exc:
        log.debug("doc_rank embed failed: %s", type(exc).__name__)
        return "encode_error"
    if matrix.shape != (len(order), query.shape[0]):
        return "degenerate"
    with np.errstate(all="ignore"):
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query)
        cosine = (matrix @ query) / norms
    if not _usable(cosine, len(order)):
        return "degenerate"
    # Positions in ``order`` are BM25 ranks; a dense tie keeps BM25 rank.
    dense = sorted(range(len(order)), key=lambda j: (-float(cosine[j]), j))
    fused = rrf_fuse(
        [[str(order[j]) for j in range(len(order))], [str(order[j]) for j in dense]],
        k=_RRF_K,
        top_n=len(order),
    )
    return [int(doc_id) for doc_id, _ in fused]


def _rerank_order(
    question: str, pool: list[Passage], order: list[int], sc: Any, trace: Trace
) -> list[int] | str:
    t0 = time.perf_counter()
    try:
        raw = sc.score(question, [_label(pool[r]) for r in order])
    except Exception as exc:
        log.debug("doc_rank rerank failed: %s", type(exc).__name__)
        return "predict_error"
    trace.ms["rerank"] = _ms(t0)
    if raw is None:
        return "predict_error"
    try:
        scores = np.asarray(raw, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return "predict_error"
    if scores.shape != (len(order),) or not bool(np.all(np.isfinite(scores))):
        return "predict_error"
    if not _usable(scores, len(order)):
        return "degenerate"
    # A rerank tie keeps the fused rank.
    ranked = sorted(range(len(order)), key=lambda j: (-float(scores[j]), j))
    return [order[j] for j in ranked]


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 3)


def _call_loader(stage: str) -> tuple[Any, str]:
    # Looked up at call time so a test can replace the module-level loader.
    loader = globals()["embedder" if stage == "dense" else "scorer"]
    result: tuple[Any, str] = loader()
    return result


def select(
    question: str,
    passages: list[Passage],
    stages: frozenset[str] | set[str],
    *,
    embedder: Any = UNSET,
    scorer: Any = UNSET,
) -> Selection:
    """The gated passages to show the reader model, ranked by the requested stages.

    ``embedder`` / ``scorer`` default to the local loaders. A test injects an
    object implementing ``TextEmbedder`` / ``PairScorer``, or None for a model
    that is not installed.
    """
    t_start = time.perf_counter()
    requested = [s for s in STAGES if s in stages]
    trace = Trace(q_sha16=_sha(question or "", 16), requested=requested)
    t0 = time.perf_counter()
    gated = documents.gated_ranking(question, passages)
    trace.ms["bm25"] = _ms(t0)
    pool = [passages[i] for i in gated]
    trace.pool = len(pool)
    order = list(range(len(pool)))
    if len(pool) <= _TOP_K:
        for stage in requested:
            trace.skipped[stage] = "small_pool"
    else:
        injected = {"dense": embedder, "rerank": scorer}
        for stage in requested:  # dense before rerank
            if stage == "rerank":
                order = order[:_DEPTH]
            obj = injected[stage]
            if obj is UNSET:
                t0 = time.perf_counter()
                obj, code = _call_loader(stage)
                trace.load_ms += _ms(t0)
            else:
                code = "" if obj is not None else "weights_missing"
            if obj is None:
                trace.skipped[stage] = code or "load_error"
                continue
            rank = _dense_order if stage == "dense" else _rerank_order
            result = rank(question, pool, order, obj, trace)
            if isinstance(result, str):
                trace.skipped[stage] = result
            else:
                order = result
                trace.ran.append(stage)
    order = order[:_DEPTH]
    top = [pool[r] for r in order[:_TOP_K]]
    trace.ranked_sha12 = [_sha(_label(pool[r]), 12) for r in order]
    trace.shown = [(p.source, _sha(_label(p), 12)) for p in top]
    trace.note = note_for(trace)
    trace.ms["total"] = _ms(t_start)
    for stage, code in trace.skipped.items():
        if code != "small_pool":
            _warn((stage, code), "doc_rank %s stage did not run (%s); keeping the order it was given", stage, code)
    log.info("doc_rank_trace %s", json.dumps(asdict(trace), sort_keys=True))
    return Selection(top=top, trace=trace, note=trace.note)


def note_for(trace: Trace) -> str:
    """The fixed engine line for requested stages that did not run, else ""."""
    missing = [s for s in STAGES if trace.skipped.get(s, "small_pool") != "small_pool"]
    if not missing:
        return ""
    head = _HEAD.get(frozenset(s for s in trace.ran if s in STAGES))
    if head is None:
        return ""
    return head + "".join(_MISSING[s].format(why=_WHY[trace.skipped[s]]) for s in missing)


# -- logging ----------------------------------------------------------------

_WARNED: dict[tuple[str, str], float] = {}
_WARN_LOCK = threading.Lock()


def _warn(key: tuple[str, str], msg: str, *args: Any) -> None:
    now = _clock()
    with _WARN_LOCK:
        last = _WARNED.get(key)
        if last is not None and now - last < _WARN_EVERY_S:
            return
        _WARNED[key] = now
    log.warning(msg, *args)


__all__ = [
    "EMBED_MODEL",
    "EMBED_REVISION",
    "RANK_ENV",
    "RERANK_MODEL",
    "RERANK_REVISION",
    "SKIP_CODES",
    "STAGES",
    "UNSET",
    "PairScorer",
    "Selection",
    "TextEmbedder",
    "Trace",
    "embedder",
    "note_for",
    "reset_for_tests",
    "scorer",
    "select",
    "stages_from_env",
]
