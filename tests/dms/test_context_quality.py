"""Context-quality eval — proves the context stack helps and does not harm.

Harm would look like: instructions silently dropped under pressure, recent
turns paraphrased, critical entities lost in compaction, silent truncation,
cache answers leaking across dissimilar queries, or T0 paths still shipping
context to a model. Each test pins one of those failure modes shut.
"""

from __future__ import annotations

import pytest

from CortexOS.context_engineering.assembler import ContextRequest, assemble_context
from CortexOS.context_engineering.budget import estimate_tokens, fit_text
from CortexOS.context_engineering.compaction import (
    clear_stale_tool_results,
    compact_messages,
    summarize_for_reseed,
)
from CortexOS.memory.semantic_cache import SemanticCache
from CortexOS.ponytail.middleware import _compress_context


def test_tight_budget_protects_instructions_over_chatter():
    out = assemble_context(
        ContextRequest(
            instructions="Never delete records without steward approval.",
            messages=[f"turn {i}: long filler chatter about nothing much " * 6 for i in range(40)],
            token_budget=600,
        )
    )

    assert "Never delete records" in out.system  # the rules survive
    assert out.compacted or "messages" in out.truncated_layers  # history paid, not the rules


def test_assembled_total_respects_budget_with_reseed_guard():
    out = assemble_context(
        ContextRequest(
            instructions="short",
            messages=["x " * 400 for _ in range(20)],
            token_budget=800,
        )
    )

    assert out.token_estimate <= 800 + 100  # small overhead slack for layer tags


def test_truncation_is_transparent_never_silent():
    out = assemble_context(
        ContextRequest(
            instructions="i" * 8000,  # far over the instructions share
            token_budget=512,
        )
    )

    assert "instructions" in out.truncated_layers
    assert "[truncated]" in out.system


def test_critical_fact_survives_head_compaction():
    blocks = ["PO-4711 must ship to dock 9 before Friday."]
    blocks += [f"turn {i}: filler chatter about pallets, lanes and belts" for i in range(30)]
    blocks += ["latest: forklift 3 is charging."]

    compacted, was_compacted = compact_messages(blocks, token_budget=120)

    assert was_compacted
    joined = "\n".join(compacted)
    assert "PO-4711" in joined  # entity retained through the head summary
    assert compacted[-1] == blocks[-1]  # newest turn stays verbatim


def test_recent_tail_never_paraphrased():
    blocks = [f"old {i}" for i in range(20)] + ["decision: use preset dag for SOP-7"]

    compacted, _ = compact_messages(blocks, token_budget=200)

    assert compacted[-1] == "decision: use preset dag for SOP-7"


def test_stale_tool_results_stubbed_but_named_and_recent_kept():
    blocks = [
        'Tool search: {"ok": true, "preview": "' + "x" * 200 + '"}',
        'Tool fetch: {"ok": true, "preview": "' + "y" * 200 + '"}',
        'Tool read: {"ok": true, "preview": "fresh"}',
        'Tool write: {"ok": true, "preview": "fresh"}',
        "user: so what changed?",
    ]

    cleaned = clear_stale_tool_results(blocks, keep_last=2)

    assert "cleared" in cleaned[0] and "search" in cleaned[0]  # stub keeps orientation
    assert "cleared" in cleaned[1] and "fetch" in cleaned[1]
    assert cleaned[2] == blocks[2] and cleaned[3] == blocks[3]  # newest tools intact
    assert cleaned[4] == blocks[4]  # user text untouched


def test_fit_text_cuts_on_boundary_with_marker():
    text = " ".join(f"Sentence number {i} ends here." for i in range(200))

    fitted, was = fit_text(text, 60)

    assert was
    body, marker = fitted.rsplit("\n", 1)
    assert marker == "[truncated]"
    assert body.rstrip().endswith(".")  # boundary cut, no mid-word amputation


def test_reseed_summary_fits_budget_and_keeps_lines():
    blocks = [f"fact {i}: value-{i}" for i in range(10)]

    seed = summarize_for_reseed(blocks, token_budget=400)

    assert estimate_tokens(seed) <= 400
    assert "fact 0" in seed and "fact 9" in seed


def test_ponytail_t0_ships_zero_context():
    out, was = _compress_context("x" * 4000, 0)

    assert out == ""  # deterministic tier: the model never sees context
    assert was


def test_ponytail_within_budget_untouched():
    assert _compress_context("short text", 512) == ("short text", False)


def test_semantic_cache_no_false_hit_below_threshold():
    cache = SemanticCache(threshold=0.92)
    cache.put([1.0, 0.0], "answer-a")

    assert cache.get([0.6, 0.8]) is None  # cosine 0.6 — must miss
    assert cache.get([1.0, 0.0]) == "answer-a"
    assert cache.hit_count == 1
