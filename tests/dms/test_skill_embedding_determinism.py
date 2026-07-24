"""Skill-retrieval correctness under a multi-process engine runtime.

The embedding must be identical in every worker and after every restart —
builtin hash() is PYTHONHASHSEED-randomised, so a skill captured by one
worker was unmatchable by the next. These tests pin that down, plus the
precision/recall of trigger matching itself.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid

import pytest

from packs.dms.skills.capture import (
    _MATCH_THRESHOLD,
    boost_candidates_from_skills,
    init_skills_schema,
    match_trigger,
    normalize_trigger,
    text_embedding,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (query, stored trigger pattern, should_match)
RETRIEVAL_CASES = [
    ("audit stale inventory warehouse zone a", "audit stale inventory warehouse zone a", True),
    ("please audit stale inventory warehouse zone a now", "audit stale inventory warehouse zone a", True),
    ("audit stale inventory in warehouse zone a", "audit stale inventory warehouse zone a", True),
    ("unrelated random message", "audit stale inventory warehouse zone a", False),
    ("send the quarterly invoice to finance", "audit stale inventory warehouse zone a", False),
    ("what is the weather today", "urgent audit stale inventory warehouse zone a", False),
    ("reorder low stock parts", "audit stale inventory warehouse zone a", False),
]


def _embed_in_subprocess(text: str, seed: str) -> list[float]:
    """Compute text_embedding in a fresh interpreter with a fixed hash seed."""
    code = (
        "import json;"
        "from packs.dms.skills.capture import text_embedding;"
        f"print(json.dumps(text_embedding({text!r})))"
    )
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip())


def test_embedding_identical_across_hash_seeds():
    """The regression test: same text, different worker, same vector."""
    text = "audit stale inventory warehouse zone a"
    baseline = _embed_in_subprocess(text, "0")
    for seed in ("1", "42", "12345"):
        assert _embed_in_subprocess(text, seed) == baseline, (
            f"embedding changed under PYTHONHASHSEED={seed} — "
            "skills captured by one worker are unmatchable by another"
        )


def test_embedding_matches_in_process_value():
    """A worker's vector must equal the one a separate process would store."""
    text = "reorder low stock parts"
    assert _embed_in_subprocess(text, "7") == pytest.approx(text_embedding(text))


def test_embedding_is_normalised():
    vec = text_embedding("audit stale inventory warehouse zone a")
    assert sum(x * x for x in vec) == pytest.approx(1.0)
    assert text_embedding("") == [0.0] * len(vec)


@pytest.mark.parametrize("query,pattern,should_match", RETRIEVAL_CASES)
def test_trigger_retrieval_precision_and_recall(query, pattern, should_match):
    emb = json.dumps(text_embedding(normalize_trigger(pattern)))
    score = match_trigger(query, pattern, emb)
    if should_match:
        assert score >= _MATCH_THRESHOLD, f"missed recall: {query!r} vs {pattern!r} = {score:.3f}"
    else:
        assert score < _MATCH_THRESHOLD, f"false positive: {query!r} vs {pattern!r} = {score:.3f}"


def test_retrieval_quality_is_seed_independent():
    """Every case must hold in a differently-seeded worker too."""
    code = (
        "import json;"
        "from packs.dms.skills.capture import match_trigger, text_embedding, "
        "normalize_trigger, _MATCH_THRESHOLD;"
        f"cases={RETRIEVAL_CASES!r};"
        "print(json.dumps([match_trigger(q, p, json.dumps("
        "text_embedding(normalize_trigger(p)))) >= _MATCH_THRESHOLD for q, p, _ in cases]))"
    )
    expected = [c[2] for c in RETRIEVAL_CASES]
    for seed in ("0", "99", "31337"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout.strip()) == expected, f"retrieval drifted at seed {seed}"


UNRELATED_OPS_PHRASES = [
    "audit stale inventory warehouse zone a",
    "send quarterly invoice to finance team",
    "reorder low stock spare parts",
    "schedule forklift maintenance next week",
    "update supplier contact details",
    "generate monthly compliance report",
    "check cold storage temperature log",
    "assign picker to outbound dock",
    "reconcile cycle count variance",
    "escalate damaged pallet claim",
    "what is the weather today",
    "unrelated random message",
    "book meeting room for standup",
    "archive old shipping manifests",
]


def test_no_false_positives_across_unrelated_phrases():
    """Hash collisions must not push unrelated triggers over the threshold.

    At _EMBED_DIM=32 two of these pairs scored >=0.5 (worst 0.722).
    """
    import itertools

    offenders = []
    for a, b in itertools.combinations(UNRELATED_OPS_PHRASES, 2):
        wa, wb = set(a.split()), set(b.split())
        if len(wa & wb) / len(wa | wb) >= 0.5:
            continue  # genuinely related — not a false-positive candidate
        score = match_trigger(a, b, json.dumps(text_embedding(b)))
        if score >= _MATCH_THRESHOLD:
            offenders.append((a, b, round(score, 3)))
    assert not offenders, f"unrelated triggers matched: {offenders}"


def test_legacy_embedding_blob_self_heals(tmp_path, monkeypatch):
    """A row stored under the old random-hash space must still match."""
    import sqlite3

    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    init_skills_schema(con)
    pattern = normalize_trigger("audit stale inventory warehouse zone a")
    con.execute(
        "INSERT INTO dms_skills (id, intent, trigger_pattern, embedding, task_id, "
        "template, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            str(uuid.uuid4()),
            "warehouse_audit",
            pattern,
            json.dumps([9.9] * 32),  # garbage from the pre-fix hash space
            "audit_stale_items",
            "{}",
            "test",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    con.commit()
    con.close()

    matched = boost_candidates_from_skills(
        [{"task_id": "audit_stale_items", "confidence": 0.4}], pattern
    )
    assert "skill_match" in matched[0], "stale embedding blob defeated retrieval"

    unrelated = boost_candidates_from_skills(
        [{"task_id": "audit_stale_items", "confidence": 0.4}], "unrelated random message"
    )
    assert "skill_match" not in unrelated[0], "stale embedding blob caused a false positive"


def test_matching_scales_to_many_skills(tmp_path, monkeypatch):
    """Correctness and latency hold with a realistic skill library."""
    import sqlite3

    db = tmp_path / "dms_ops.db"
    monkeypatch.setenv("DMS_OPS_DB", str(db))
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    init_skills_schema(con)
    target = normalize_trigger("audit stale inventory warehouse zone a")
    rows = [
        (
            str(uuid.uuid4()),
            f"intent_{i}",
            normalize_trigger(f"distinct task number {i} for department {i}"),
            json.dumps(text_embedding(f"distinct task number {i} for department {i}")),
            "audit_stale_items",
            "{}",
            "test",
            "2026-01-01T00:00:00+00:00",
        )
        for i in range(500)
    ]
    rows.append(
        (
            str(uuid.uuid4()),
            "warehouse_audit",
            target,
            json.dumps(text_embedding(target)),
            "audit_stale_items",
            "{}",
            "test",
            "2026-01-01T00:00:00+00:00",
        )
    )
    con.executemany(
        "INSERT INTO dms_skills (id, intent, trigger_pattern, embedding, task_id, "
        "template, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()

    start = time.perf_counter()
    hit = boost_candidates_from_skills(
        [{"task_id": "audit_stale_items", "confidence": 0.4}], target
    )
    elapsed = time.perf_counter() - start
    assert "skill_match" in hit[0], "target skill lost among 500 distractors"
    assert elapsed < 2.0, f"matching 501 skills took {elapsed:.2f}s"

    miss = boost_candidates_from_skills(
        [{"task_id": "audit_stale_items", "confidence": 0.4}], "completely offtopic weather chatter"
    )
    assert "skill_match" not in miss[0], "distractor library produced a false positive"
