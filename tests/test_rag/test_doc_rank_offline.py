"""RAG-RANK-01 loader limits (founder ruling 2026-10-29, AGENTS.md rule 5).

The two local models load only from weights already on the host, from the
absolute pinned snapshot directory, never from a repo id, and nothing reaches
the network. ``DISABLE_SAFETENSORS_CONVERSION`` is forced to ``1`` before
construction, whatever the operator set. CI has no weights: the constructors
here are recording fakes and the snapshot trees are empty placeholder files.
"""

from __future__ import annotations

import ast
import os
import re
import socket
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from CortexOS.rag import doc_rank, documents
from CortexOS.rag.documents import Passage

ROOT = Path(__file__).resolve().parents[2]
_REPO_ID = re.compile(r"^[\w.-]+/[\w.-]+$")


def make_snapshot_tree(
    hf_home: Path,
    *,
    embed_pin: str = doc_rank.EMBED_REVISION,
    rerank_pin: str = doc_rank.RERANK_REVISION,
    skip: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Placeholder files at every path the loader checks, under ``hf_home/hub``."""
    out = []
    for model, pin, files in (
        (doc_rank.EMBED_MODEL, embed_pin, doc_rank._EMBED_FILES),
        (doc_rank.RERANK_MODEL, rerank_pin, doc_rank._RERANK_FILES),
    ):
        snap = hf_home / "hub" / ("models--" + model.replace("/", "--")) / "snapshots" / pin
        for name in files:
            if name in skip:
                continue
            path = snap / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}", encoding="utf-8")
        out.append(snap)
    return out[0], out[1]


class Recorder:
    def __init__(self) -> None:
        self.st: list[dict[str, Any]] = []
        self.ce: list[dict[str, Any]] = []
        self.st_raises = 0
        self.ce_raises = 0


def install_recording_st(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    """A ``sentence_transformers`` stand-in that records each construction."""
    rec = Recorder()

    class SentenceTransformer:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            rec.st.append(
                {
                    "args": args,
                    "kwargs": kwargs,
                    "env": os.environ.get("DISABLE_SAFETENSORS_CONVERSION"),
                    "self": self,
                }
            )
            if rec.st_raises:
                rec.st_raises -= 1
                raise OSError("weights unreadable")
            self.max_seq_length = 8192

        def get_sentence_embedding_dimension(self) -> int:
            return 2

        def encode(self, sentences: Any, **kwargs: Any) -> Any:
            texts = [sentences] if isinstance(sentences, str) else list(sentences)
            return [[float(len(t) % 7), 1.0] for t in texts]

    class CrossEncoder:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            rec.ce.append(
                {
                    "args": args,
                    "kwargs": kwargs,
                    "env": os.environ.get("DISABLE_SAFETENSORS_CONVERSION"),
                    "self": self,
                }
            )
            if rec.ce_raises:
                rec.ce_raises -= 1
                raise OSError("weights unreadable")

        def predict(self, pairs: Any, **kwargs: Any) -> list[float]:
            return [float(len(b)) for _, b in pairs]

    fake = types.ModuleType("sentence_transformers")
    fake.SentenceTransformer = SentenceTransformer  # type: ignore[attr-defined]
    fake.CrossEncoder = CrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    return rec


@pytest.fixture(autouse=True)
def offline_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """HF_HOME is a tmp dir, HF_HUB_CACHE is unset, and the operator asked for
    conversion (``0``): the loader must override it."""
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("CORTEX_PROFILE", raising=False)
    hf_home = tmp_path / "hf"
    hf_home.mkdir()
    monkeypatch.setenv("HF_HOME", str(hf_home))
    monkeypatch.setenv("DISABLE_SAFETENSORS_CONVERSION", "0")
    doc_rank.reset_for_tests()
    yield hf_home
    doc_rank.reset_for_tests()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    attempts: list[Any] = []

    def connect(self: Any, address: Any) -> None:
        attempts.append(address)
        raise OSError("network blocked by test")

    def create_connection(address: Any, *args: Any, **kwargs: Any) -> None:
        attempts.append(address)
        raise OSError("network blocked by test")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    return attempts


def test_constructs_from_absolute_snapshot_dir(monkeypatch: pytest.MonkeyPatch, offline_env: Path) -> None:
    monkeypatch.setenv("EMBEDDER_MODEL", "foo")
    monkeypatch.setenv("NETIE_EMBEDDER_MODEL", "bar")
    rec = install_recording_st(monkeypatch)
    embed_dir, rerank_dir = make_snapshot_tree(offline_env)

    emb, ecode = doc_rank.embedder()
    sc, scode = doc_rank.scorer()

    assert (ecode, scode) == ("", "")
    assert emb is not None and sc is not None
    [st] = rec.st
    [ce] = rec.ce
    for call, want in ((st, embed_dir), (ce, rerank_dir)):
        p = call["args"][0]
        assert os.path.isabs(p) and os.path.isdir(p), p
        assert p == str(want), p
        assert p not in (doc_rank.EMBED_MODEL, doc_rank.RERANK_MODEL)
        assert not _REPO_ID.match(p), p
        assert call["kwargs"].get("local_files_only") is True, call["kwargs"]
        assert "revision" not in call["kwargs"] and "cache_folder" not in call["kwargs"], call["kwargs"]
        # Second guard, set before construction whatever the operator chose.
        assert call["env"] == "1"
    assert st["args"][0] == str(
        offline_env / "hub" / "models--BAAI--bge-m3" / "snapshots" / doc_rank.EMBED_REVISION
    )
    assert ce["args"][0] == str(
        offline_env / "hub" / "models--BAAI--bge-reranker-v2-m3" / "snapshots" / doc_rank.RERANK_REVISION
    )
    assert ce["kwargs"].get("max_length") == 512
    assert st["self"].max_seq_length == 512


def test_hf_hub_cache_takes_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rec = install_recording_st(monkeypatch)
    cache = tmp_path / "elsewhere"
    embed_dir, _ = make_snapshot_tree(cache)
    # make_snapshot_tree writes under <dir>/hub; point HF_HUB_CACHE at that.
    monkeypatch.setenv("HF_HUB_CACHE", str(cache / "hub"))

    emb, code = doc_rank.embedder()

    assert code == "" and emb is not None
    assert rec.st[0]["args"][0] == str(embed_dir)


def test_scorer_constructs_inside_scorer(monkeypatch: pytest.MonkeyPatch, offline_env: Path) -> None:
    rec = install_recording_st(monkeypatch)
    make_snapshot_tree(offline_env)

    sc, code = doc_rank.scorer()

    assert code == "" and sc is not None
    assert len(rec.ce) == 1, "the cross-encoder must load inside scorer(), before any score()"
    assert sc.score("q", ["a", "bb"]) == [1.0, 2.0]


def test_scorer_constructor_raise_is_load_error(monkeypatch: pytest.MonkeyPatch, offline_env: Path) -> None:
    rec = install_recording_st(monkeypatch)
    rec.ce_raises = 1
    make_snapshot_tree(offline_env)

    assert doc_rank.scorer() == (None, "load_error")
    assert len(rec.ce) == 1


def test_empty_hf_home_is_weights_missing_with_no_network(
    monkeypatch: pytest.MonkeyPatch, no_network: list[Any]
) -> None:
    rec = install_recording_st(monkeypatch)

    assert doc_rank.embedder() == (None, "weights_missing")
    assert doc_rank.scorer() == (None, "weights_missing")
    assert rec.st == [] and rec.ce == []
    assert no_network == []


def test_select_with_empty_hf_home_has_no_network(no_network: list[Any]) -> None:
    passages = [Passage(f"{i}.txt", "late delivery penalty " + "x " * i) for i in range(6)]

    sel = doc_rank.select("late delivery penalty", passages, frozenset({"dense", "rerank"}))

    assert sel.top == documents.retrieve("late delivery penalty", passages)
    assert sel.trace.skipped == {"dense": "weights_missing", "rerank": "weights_missing"}
    assert no_network == []


def test_other_snapshot_only_is_weights_missing(monkeypatch: pytest.MonkeyPatch, offline_env: Path) -> None:
    rec = install_recording_st(monkeypatch)
    make_snapshot_tree(offline_env, embed_pin="0" * 40, rerank_pin="1" * 40)

    assert doc_rank.embedder() == (None, "weights_missing")
    assert doc_rank.scorer() == (None, "weights_missing")
    assert rec.st == [] and rec.ce == []


@pytest.mark.parametrize("missing", ["pytorch_model.bin", "1_Pooling/config.json", "model.safetensors"])
def test_one_missing_file_is_weights_missing(
    monkeypatch: pytest.MonkeyPatch, offline_env: Path, missing: str
) -> None:
    rec = install_recording_st(monkeypatch)
    make_snapshot_tree(offline_env, skip=(missing,))

    codes = (doc_rank.embedder()[1], doc_rank.scorer()[1])

    want_embed = "weights_missing" if missing in doc_rank._EMBED_FILES else ""
    want_rerank = "weights_missing" if missing in doc_rank._RERANK_FILES else ""
    assert codes == (want_embed, want_rerank)
    assert len(rec.st) == (0 if want_embed else 1)
    assert len(rec.ce) == (0 if want_rerank else 1)


def test_core_profile_is_extra_missing(monkeypatch: pytest.MonkeyPatch, offline_env: Path) -> None:
    rec = install_recording_st(monkeypatch)
    make_snapshot_tree(offline_env)
    monkeypatch.setenv("CORTEX_PROFILE", "core")

    assert doc_rank.embedder() == (None, "extra_missing")
    assert doc_rank.scorer() == (None, "extra_missing")
    assert rec.st == [] and rec.ce == []


@pytest.mark.parametrize("stage", ["dense", "rerank"])
def test_load_error_retries_with_a_new_instance(
    monkeypatch: pytest.MonkeyPatch, offline_env: Path, stage: str
) -> None:
    rec = install_recording_st(monkeypatch)
    make_snapshot_tree(offline_env)
    now = [500.0]
    monkeypatch.setattr(doc_rank, "_clock", lambda: now[0])
    load = doc_rank.embedder if stage == "dense" else doc_rank.scorer
    calls = rec.st if stage == "dense" else rec.ce
    if stage == "dense":
        rec.st_raises = 1
    else:
        rec.ce_raises = 1

    assert load() == (None, "load_error")
    now[0] += doc_rank._RETRY_S - 1
    assert load() == (None, "load_error")
    assert len(calls) == 1, "no reconstruction inside the retry window"

    now[0] += 2
    model, code = load()

    assert code == "" and model is not None
    assert len(calls) == 2
    assert calls[0]["self"] is not calls[1]["self"]
    # Loaded once, then reused.
    assert load()[0] is model and len(calls) == 2


def test_loaded_reranker_is_a_fresh_bge_instance_after_retry(
    monkeypatch: pytest.MonkeyPatch, offline_env: Path
) -> None:
    """BGEReranker caches a load failure for its lifetime, so a retry must build a new one."""
    from CortexOS.rag import reranker as reranker_mod

    built: list[Any] = []
    real_init = reranker_mod.BGEReranker.__init__

    def spy_init(self: Any, *args: Any, **kwargs: Any) -> None:
        built.append(self)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(reranker_mod.BGEReranker, "__init__", spy_init)
    rec = install_recording_st(monkeypatch)
    rec.ce_raises = 1
    make_snapshot_tree(offline_env)
    now = [0.0]
    monkeypatch.setattr(doc_rank, "_clock", lambda: now[0])

    assert doc_rank.scorer() == (None, "load_error")
    now[0] += doc_rank._RETRY_S + 1
    sc, code = doc_rank.scorer()

    assert code == "" and sc is not None
    assert len(built) == 2 and built[0] is not built[1]


# -- source scan --------------------------------------------------------------------


def _doc_rank_tree() -> ast.Module:
    return ast.parse((ROOT / "CortexOS" / "rag" / "doc_rank.py").read_text(encoding="utf-8"))


def test_doc_rank_imports_no_network_or_model_path() -> None:
    banned = ("freeroute", "requests", "urllib", "httpx", "huggingface_hub", "transformers")
    for node in ast.walk(_doc_rank_tree()):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""] + [a.name for a in node.names]
        for name in names:
            parts = set(name.split("."))
            assert not parts & set(banned), f"doc_rank imports {name}"


def test_doc_rank_names_no_download_call_or_hub_kwarg() -> None:
    banned_names = {"snapshot_download", "hf_hub_download", "from_pretrained"}
    for node in ast.walk(_doc_rank_tree()):
        if isinstance(node, ast.Name):
            assert node.id not in banned_names, node.id
        elif isinstance(node, ast.Attribute):
            assert node.attr not in banned_names, node.attr
        elif isinstance(node, ast.keyword):
            assert node.arg not in {"revision", "cache_folder"}, node.arg
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value not in {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"}, node.value
            assert node.value not in {"revision", "cache_folder"}, node.value
