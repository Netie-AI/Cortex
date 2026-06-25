"""Prompt-injection and jailbreak pattern detection (F7 extension)."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Instruction override / role hijack
_OVERRIDE = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?"
    r"|disregard\s+(your\s+)?(rules|instructions|policy)"
    r"|you\s+are\s+now\s+(?:a|an)\s+"
    r"|new\s+system\s+prompt"
    r"|forget\s+(everything|all)\s+you"
    r"|override\s+(system|safety)"
    r"|act\s+as\s+(?:if\s+)?(?:you\s+have\s+)?no\s+(?:rules|restrictions))"
)

# Data exfiltration / credential fishing
_EXFIL = re.compile(
    r"(?i)(reveal\s+(?:your\s+)?(?:system|hidden|secret)\s+prompt"
    r"|print\s+(?:the\s+)?(?:system|initial)\s+prompt"
    r"|show\s+me\s+(?:your\s+)?(?:instructions|rules|api\s*key)"
    r"|what\s+(?:is|are)\s+your\s+(?:hidden\s+)?(?:api\s*key|system\s+prompt|instructions)"
    r"|(?:api\s*key|initial\s+prompt|hidden\s+prompt)"
    r"|dump\s+(?:all\s+)?(?:memory|context|secrets))"
)

# Delimiter / markup injection
_DELIMITER = re.compile(
    r"(<\s*/?\s*(?:system|assistant|user|instruction|tool)\s*>"
    r"|\[\[\s*(?:SYSTEM|INST|SYS)\s*\]\]"
    r"|```\s*(?:system|hidden|admin))"
)

# Indirect injection via encoded payloads
_ENCODED = re.compile(
    r"(?i)(base64\s*decode|eval\s*\(|exec\s*\(|__import__|os\.environ|subprocess\.)"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("instruction_override", _OVERRIDE, "critical"),
    ("exfiltration", _EXFIL, "critical"),
    ("delimiter_injection", _DELIMITER, "high"),
    ("encoded_payload", _ENCODED, "critical"),
)


@dataclass(frozen=True, slots=True)
class InjectionFinding:
    kind: str
    severity: str
    matched: str
    start: int
    end: int


def scan(text: str) -> list[InjectionFinding]:
    findings: list[InjectionFinding] = []
    for kind, pattern, severity in _PATTERNS:
        for match in pattern.finditer(text):
            findings.append(
                InjectionFinding(
                    kind=kind,
                    severity=severity,
                    matched=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )
    return findings


def is_blocked(text: str, *, block_severity: str = "critical") -> bool:
    order = ("low", "medium", "high", "critical")
    threshold = order.index(block_severity)
    for f in scan(text):
        if order.index(f.severity) >= threshold:
            return True
    return False


def sanitize_for_prompt(text: str) -> tuple[str, list[InjectionFinding]]:
    """Strip high/critical injection spans; return cleaned text + findings."""
    findings = scan(text)
    if not findings:
        return text, []
    order = ("low", "medium", "high", "critical")
    to_strip = [f for f in findings if order.index(f.severity) >= order.index("high")]
    if not to_strip:
        return text, findings
    parts: list[str] = []
    cursor = 0
    for f in sorted(to_strip, key=lambda x: x.start):
        if f.start < cursor:
            continue
        parts.append(text[cursor : f.start])
        parts.append("[BLOCKED:injection]")
        cursor = f.end
    parts.append(text[cursor:])
    return "".join(parts), findings
