"""Context layers — distinct sections at the right altitude for the model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LayerId(str, Enum):
    """Canonical layers (book Ch.1–7 + Anthropic anatomy of effective context)."""

    INSTRUCTIONS = "instructions"  # system prompt / rules / skills
    TOOLS = "tools"  # tool schemas — minimal viable set
    EXAMPLES = "examples"  # few canonical shots, not laundry lists
    MEMORY = "memory"  # notes + long-term carry pack
    RETRIEVAL = "retrieval"  # JIT refs or prefetched snippets
    STATE = "state"  # workspace fingerprint, session flags
    MESSAGES = "messages"  # turn history / tool loop


# Render order: stable → volatile. Messages last so compaction hits them first.
RENDER_ORDER: tuple[LayerId, ...] = (
    LayerId.INSTRUCTIONS,
    LayerId.TOOLS,
    LayerId.EXAMPLES,
    LayerId.STATE,
    LayerId.MEMORY,
    LayerId.RETRIEVAL,
    LayerId.MESSAGES,
)

# XML-ish headers — models parse these reliably; keep altitude concrete.
SECTION_TAGS: dict[LayerId, str] = {
    LayerId.INSTRUCTIONS: "instructions",
    LayerId.TOOLS: "tool_guidance",
    LayerId.EXAMPLES: "examples",
    LayerId.MEMORY: "memory",
    LayerId.RETRIEVAL: "retrieval",
    LayerId.STATE: "state",
    LayerId.MESSAGES: "messages",
}


@dataclass(slots=True)
class ContextLayer:
    """One curated block of context."""

    id: LayerId
    text: str = ""
    priority: int = 0  # higher = keep preferentially under pressure
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.text or "").strip()


def render_layer(layer: ContextLayer) -> str:
    body = (layer.text or "").strip()
    if not body:
        return ""
    tag = SECTION_TAGS.get(layer.id, layer.id.value)
    return f"<{tag}>\n{body}\n</{tag}>"
