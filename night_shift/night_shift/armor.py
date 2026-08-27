"""Model Armor stand-in: prompt injection, tool poisoning, PII. Fail closed."""

from __future__ import annotations

import re
from typing import Any

_INJECT = re.compile(
    r"(ignore (all )?(previous|prior) instructions|you are now|system prompt|"
    r"reveal (your )?(hidden )?prompt|jailbreak)",
    re.I,
)
_PII = re.compile(
    r"(\b\d{12,16}\b|"  # crude card / IC
    r"\b[A-Z]{1,2}\d{6,8}\b|"  # MY IC-ish
    r"\b\d{3}-\d{2}-\d{4}\b)",  # SSN-ish
    re.I,
)
_POISON = re.compile(
    r"(exfiltrate|send[_\s-]?all[_\s-]?keys|disable[_\s-]?armor|override[_\s-]?po_key)",
    re.I,
)


def scan(text: str, *, tool_name: str = "") -> dict[str, Any]:
    reasons: list[str] = []
    if _INJECT.search(text or ""):
        reasons.append("prompt_injection")
    if _PII.search(text or ""):
        reasons.append("pii")
    if tool_name and _POISON.search(tool_name):
        reasons.append("tool_poison")
    if _POISON.search(text or ""):
        reasons.append("tool_poison")
    ok = not reasons
    return {
        "ok": ok,
        "reasons": reasons,
        "action": "allow" if ok else "block",
    }
