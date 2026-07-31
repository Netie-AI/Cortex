"""C-VOICE-01 scaffold — governed voice session (not product default).

Local VAD / Pointer STT remains the primary mic path. Cloud duplex
(Gemini Live / OpenAI Realtime) is backup only behind NETIE_VOICE_CLOUD=1.

This module is a placeholder for the session state machine:
  arm → (optional cloud session) → tool events → Cortex /dms/secure → Act

Do not stream always-on mic to any cloud provider from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoiceSession:
    """Minimal session record — wire to API later."""

    session_id: str
    cloud_backup: bool = False
    provider: str | None = None  # gemini_live | openai_realtime | None
    events: list[dict[str, Any]] = field(default_factory=list)

    def note(self, kind: str, **payload: Any) -> None:
        self.events.append({"kind": kind, **payload})


def cloud_voice_allowed(env: dict[str, str] | None = None) -> bool:
    e = env or {}
    return str(e.get("NETIE_VOICE_CLOUD", "")).strip() in ("1", "true", "yes")
