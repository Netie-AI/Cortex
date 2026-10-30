"""RAG-RANK-01: with ``CORTEX_DOC_RANK=dense,rerank`` every bench question is
answered byte-identically to the recorded main golden.

The sample corpus is one passage, so no question ever reaches a ranking model
(pool <= 4). This is the flag-on twin of
``test_ask_flag_off_golden_340.test_flag_unset_ask_is_byte_identical_to_main``,
with the same comparison rule and no re-record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from CortexOS.rag import doc_rank
from tests.dms.test_ask_flag_off_golden_340 import GOLDEN, _questions, _run
from tests.dms.test_query_skill_gate_340 import ask_http, skills_db  # noqa: F401


def test_rank_on_ask_is_byte_identical_to_main(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path  # noqa: F811
) -> None:
    for name in ("CORTEX_QUERY_SKILL", "DMS_QUERY_SKILL_CAPTURE", "DMS_L2_ENABLED", "CORTEX_SCORED_ROUND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(doc_rank.RANK_ENV, "dense,rerank")
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf-empty"))
    doc_rank.reset_for_tests()

    loads: list[str] = []
    selects: list[str] = []
    real_select = doc_rank.select

    def counting_select(question, passages, stages, **kwargs):
        selects.append(question)
        return real_select(question, passages, stages, **kwargs)

    monkeypatch.setattr(doc_rank, "select", counting_select)
    monkeypatch.setattr(doc_rank, "embedder", lambda: loads.append("dense") or (None, "weights_missing"))
    monkeypatch.setattr(doc_rank, "scorer", lambda: loads.append("rerank") or (None, "weights_missing"))
    from CortexOS.dms.answer_engine import match_certified

    assert doc_rank.stages_from_env() == frozenset({"dense", "rerank"})
    seen, _modes, bodies = _run(ask_http)

    # The flag was live: the document lane ran through the ranking path.
    assert selects, "no question reached doc_rank.select; the flag-on run proved nothing"
    assert loads == [], loads
    certified = {qid for qid, question in _questions() if match_certified(question) is not None}
    non_exact = set()
    for qid, body in bodies.items():
        badge = str((body.get("provenance") or {}).get("badge") or body.get("badge") or "")
        if badge == "reconfirm":
            assert qid not in certified, qid
            non_exact.add(qid)
            assert body.get("rows") in ([], None)
            assert not body.get("sql_used")
            text = str(body.get("answer") or "")
            assert "Yes runs that question" in text
            assert "not found in the database" in text
        assert "Ranking:" not in str(body.get("answer") or ""), qid
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["n"] == len(seen) == len(_questions()) > 150
    changed = sorted(
        qid
        for qid in golden["sha256"]
        if qid not in non_exact and golden["sha256"][qid] != seen.get(qid)
    )
    assert changed == [], f"{len(changed)} answers differ from main {golden['recorded_on']}: {changed}"
    doc_rank.reset_for_tests()
