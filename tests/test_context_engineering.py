"""Context engineering unit tests (CortexOS)."""
from __future__ import annotations

from pathlib import Path

from CortexOS.context_engineering import (
    ContextRequest,
    NoteStore,
    assemble_context,
    clear_stale_tool_results,
    compact_messages,
    estimate_tokens,
    layer_budgets,
)
from CortexOS.context_engineering.layers import LayerId


def test_estimate_tokens_and_budgets():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    budgets = layer_budgets(1000)
    assert budgets[LayerId.INSTRUCTIONS] > 0
    assert sum(budgets.values()) == 1000


def test_assemble_layers_and_tags():
    out = assemble_context(
        ContextRequest(
            instructions="Be a careful warehouse agent.",
            tools='[{"name":"stock_count"}]',
            examples="User: count SKU-1 → call stock_count",
            memory="Last cycle count was Tuesday.",
            state="branch=main dirty=0",
            retrieval="item_id=SKU-1",
            messages=["User asked for count", "Tool stock_count: {\"ok\": true, \"n\": 12}"],
            token_budget=2000,
        )
    )
    assert "<instructions>" in out.system
    assert "<tool_guidance>" in out.system
    assert out.token_estimate > 0
    assert "memory" in out.layers or "state" in out.layers


def test_clear_stale_tool_results_keeps_tail():
    blocks = [
        "Tool vault_read: {\"ok\": true, \"preview\": \"a\"}",
        "Tool web_search: {\"ok\": true, \"preview\": \"b\"}",
        "Tool fs_patch: {\"ok\": true, \"path\": \"x.py\"}",
        "final analysis so far",
    ]
    cleaned = clear_stale_tool_results(blocks, keep_last=1)
    assert "cleared" in cleaned[0].lower()
    assert "cleared" in cleaned[1].lower()
    assert "fs_patch" in cleaned[2]
    assert cleaned[3] == "final analysis so far"


def test_compact_messages_under_budget():
    blocks = [f"Tool step_{i}: " + ("x" * 200) for i in range(20)]
    out, compacted = compact_messages(blocks, token_budget=200, keep_tail=3)
    assert compacted is True
    assert len(out) <= 4
    assert estimate_tokens("\n\n".join(out)) <= 250


def test_note_store_roundtrip(tmp_path: Path):
    path = tmp_path / ".airgpt" / "NOTES.md"
    store = NoteStore(path, max_read_chars=500)
    assert store.append("Train Pikachu to L10", heading="Objectives")["ok"]
    assert store.replace_section("Objectives", "- Train Pikachu to L10\n- Explore Route 1")["ok"]
    text = store.read_recent()
    assert "Pikachu" in text
    assert path.is_file()
