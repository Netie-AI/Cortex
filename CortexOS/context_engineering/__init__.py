"""Context engineering — curate the smallest high-signal token set for agents.

Implements the practices from Anthropic's *Effective context engineering for AI
agents* and the layered model popularized in context-engineering literature:

  instructions → tools → examples → memory/notes → retrieval → messages

Import as ``netie.context_engineering`` / ``CortexOS.context_engineering``.
"""
from __future__ import annotations

from .assembler import AssembledContext, ContextRequest, assemble_context
from .budget import estimate_tokens, layer_budgets
from .compaction import clear_stale_tool_results, compact_messages
from .layers import ContextLayer, LayerId
from .notes import NoteStore, default_notes_path

__all__ = [
    "AssembledContext",
    "ContextLayer",
    "ContextRequest",
    "LayerId",
    "NoteStore",
    "assemble_context",
    "clear_stale_tool_results",
    "compact_messages",
    "default_notes_path",
    "estimate_tokens",
    "layer_budgets",
]
