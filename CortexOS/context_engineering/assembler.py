"""Assemble curated context for one inference turn."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .budget import estimate_tokens, fit_text, layer_budgets
from .compaction import compact_messages, summarize_for_reseed
from .layers import RENDER_ORDER, ContextLayer, LayerId, render_layer


@dataclass(slots=True)
class ContextRequest:
    """Inputs for one assembly pass. Prefer identifiers over raw dumps (JIT)."""

    instructions: str = ""
    tools: str = ""
    examples: str = ""
    memory: str = ""
    retrieval: str = ""
    state: str = ""
    messages: Sequence[str] = field(default_factory=tuple)
    token_budget: int = 4096
    valid_at: str | None = None
    invalid_at: str | None = None
    invalidated_layers: Sequence[str] = field(default_factory=tuple)
    system_altitude: str = (
        "Be specific enough to guide behavior; avoid brittle if-else rules and "
        "vague guidance that assumes unstated shared context."
    )


@dataclass(slots=True)
class AssembledContext:
    system: str
    user_context: str
    layers: dict[str, str]
    token_estimate: int
    truncated_layers: list[str]
    compacted: bool
    meta: dict[str, Any] = field(default_factory=dict)

    def as_prompt_pair(self) -> tuple[str, str]:
        return self.system, self.user_context


_INFERRED_LAYER_IDS = frozenset({"memory", "retrieval"})


def _as_of_live(as_of: str | None, invalid_at: str | None) -> bool:
    """Graphiti analog: a fact is live at as_of when invalid_at is unset or later.

    ISO-8601 Zulu strings compare lexicographically. No Neo4j. Ledger stays SoT.
    """
    if not invalid_at:
        return True
    if not as_of:
        return False
    return as_of < invalid_at


def _invalidated_inferred(req: ContextRequest) -> set[str]:
    explicit = {str(x).strip() for x in (req.invalidated_layers or ()) if str(x).strip()}
    if explicit:
        return {x for x in explicit if x in _INFERRED_LAYER_IDS}
    if _as_of_live(req.valid_at, req.invalid_at):
        return set()
    dropped: set[str] = set()
    if (req.memory or "").strip():
        dropped.add("memory")
    if (req.retrieval or "").strip():
        dropped.add("retrieval")
    return dropped


def _episode_meta(req: ContextRequest, drop: set[str]) -> dict[str, Any]:
    episode: dict[str, Any] = {}
    if req.valid_at:
        episode["valid_at"] = req.valid_at
    if req.invalid_at:
        episode["invalid_at"] = req.invalid_at
    if drop:
        episode["invalidated"] = sorted(drop)
    return episode


def assemble_context(req: ContextRequest) -> AssembledContext:
    """
    Build system + user context blocks under a shared token budget.

    System carries instructions + tools + examples (stable).
    User context carries state, memory, retrieval, and compacted messages.
    """
    budgets = layer_budgets(max(256, int(req.token_budget)))
    truncated: list[str] = []
    compacted = False

    def _fit(lid: LayerId, text: str) -> str:
        nonlocal truncated
        fitted, was = fit_text((text or "").strip(), budgets.get(lid, 0))
        if was:
            truncated.append(lid.value)
        return fitted

    drop = _invalidated_inferred(req)
    memory_text = "" if "memory" in drop else req.memory
    retrieval_text = "" if "retrieval" in drop else req.retrieval

    # Messages get compaction before hard trim.
    msg_blocks = [m for m in (req.messages or []) if (m or "").strip()]
    msg_budget = budgets.get(LayerId.MESSAGES, 0)
    if msg_blocks:
        msg_blocks, compacted = compact_messages(msg_blocks, token_budget=msg_budget)
    messages_text = "\n\n".join(msg_blocks)

    layers = [
        ContextLayer(LayerId.INSTRUCTIONS, _fit(LayerId.INSTRUCTIONS, req.instructions), priority=100),
        ContextLayer(LayerId.TOOLS, _fit(LayerId.TOOLS, req.tools), priority=90),
        ContextLayer(LayerId.EXAMPLES, _fit(LayerId.EXAMPLES, req.examples), priority=70),
        ContextLayer(LayerId.STATE, _fit(LayerId.STATE, req.state), priority=60),
        ContextLayer(LayerId.MEMORY, _fit(LayerId.MEMORY, memory_text), priority=50),
        ContextLayer(LayerId.RETRIEVAL, _fit(LayerId.RETRIEVAL, retrieval_text), priority=40),
        ContextLayer(LayerId.MESSAGES, _fit(LayerId.MESSAGES, messages_text), priority=30),
    ]

    system_parts = [
        render_layer(layers[0]),
        render_layer(layers[1]),
        render_layer(layers[2]),
    ]
    if req.system_altitude.strip():
        system_parts.insert(0, f"<background_information>\n{req.system_altitude.strip()}\n</background_information>")
    system = "\n\n".join(p for p in system_parts if p)

    user_parts = [render_layer(ly) for ly in layers[3:] if not ly.empty]
    user_context = "\n\n".join(user_parts)

    layer_map = {ly.id.value: ly.text for ly in layers if not ly.empty}
    total = estimate_tokens(system) + estimate_tokens(user_context)

    extracted = {
        LayerId.INSTRUCTIONS.value,
        LayerId.TOOLS.value,
        LayerId.EXAMPLES.value,
        LayerId.STATE.value,
        LayerId.MESSAGES.value,
    }
    inferred = {LayerId.MEMORY.value, LayerId.RETRIEVAL.value}
    layer_confidence = {
        lid: ("EXTRACTED" if lid in extracted else "INFERRED" if lid in inferred else "AMBIGUOUS")
        for lid in layer_map
    }

    # Final guard: if still over budget, reseed messages from summary.
    if total > req.token_budget and msg_blocks:
        seed = summarize_for_reseed(msg_blocks, token_budget=max(200, msg_budget // 2))
        layers[-1] = ContextLayer(LayerId.MESSAGES, seed, priority=30)
        user_parts = [render_layer(ly) for ly in layers[3:] if not ly.empty]
        user_context = "\n\n".join(user_parts)
        layer_map[LayerId.MESSAGES.value] = seed
        total = estimate_tokens(system) + estimate_tokens(user_context)
        compacted = True
        if LayerId.MESSAGES.value not in truncated:
            truncated.append(LayerId.MESSAGES.value)

    return AssembledContext(
        system=system,
        user_context=user_context,
        layers=layer_map,
        token_estimate=total,
        truncated_layers=truncated,
        compacted=compacted,
        meta={
            "budget": req.token_budget,
            "layer_budgets": {k.value: v for k, v in budgets.items()},
            "render_order": [x.value for x in RENDER_ORDER],
            "layer_confidence": layer_confidence,
            "episode": _episode_meta(req, drop),
        },
    )
