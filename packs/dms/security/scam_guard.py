"""Scam, bait, and social-engineering pattern detection for inbound messages."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Urgency + payment pressure (common BEC / warehouse fraud)
_URGENCY_PAY = re.compile(
    r"(?i)(urgent\s+(?:payment|wire\s+transfer)"
    r"|wire\s+transfer\s+immediately"
    r"|change\s+(?:the\s+)?bank\s+account"
    r"|new\s+(?:payment|bank)\s+details"
    r"|pay\s+before\s+(?:pickup|release|shipment)"
    r"|crypto\s+only|bitcoin\s+address)"
)

# Credential / OTP harvest
_CRED_HARVEST = re.compile(
    r"(?i)(send\s+(?:me\s+)?(?:your\s+)?(?:otp|verification\s+code|password|pin)"
    r"|confirm\s+(?:your\s+)?(?:bank|account)\s+(?:login|details)"
    r"|click\s+this\s+link\s+to\s+(?:verify|unlock|confirm))"
)

# Impersonation (boss/vendor/supplier)
_IMPERSONATE = re.compile(
    r"(?i)(i\s+am\s+(?:the\s+)?(?:ceo|director|owner)\s+"
    r"|acting\s+on\s+behalf\s+of\s+(?:management|head\s+office)"
    r"|do\s+not\s+tell\s+(?:anyone|colleagues|staff)"
    r"|confidential\s+request\s+from\s+(?:hq|head\s+office))"
)

# Phishing URLs (suspicious TLDs / IP literals)
_PHISH_URL = re.compile(
    r"(?i)(https?://(?:\d{1,3}\.){3}\d{1,3}"
    r"|https?://[^\s]+\.(?:tk|ml|ga|cf|gq|xyz|top|bond)(?:/|\s|$))"
)

# Advance-fee / overpayment scam
_ADVANCE_FEE = re.compile(
    r"(?i)(overpaid\s+invoice|refund\s+the\s+difference"
    r"|send\s+back\s+(?:the\s+)?excess"
    r"|processing\s+fee\s+required\s+before)"
)

_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("urgency_payment", _URGENCY_PAY, "critical"),
    ("credential_harvest", _CRED_HARVEST, "critical"),
    ("impersonation", _IMPERSONATE, "high"),
    ("phishing_url", _PHISH_URL, "high"),
    ("advance_fee", _ADVANCE_FEE, "high"),
)


@dataclass(frozen=True, slots=True)
class ScamFinding:
    kind: str
    severity: str
    matched: str


def scan(text: str) -> list[ScamFinding]:
    out: list[ScamFinding] = []
    for kind, pattern, severity in _PATTERNS:
        for match in pattern.finditer(text):
            out.append(ScamFinding(kind=kind, severity=severity, matched=match.group(0)))
    return out


def risk_score(text: str) -> float:
    """0.0 = clean, 1.0 = highest scam signals."""
    findings = scan(text)
    if not findings:
        return 0.0
    weights = {"low": 0.2, "medium": 0.4, "high": 0.7, "critical": 1.0}
    return min(1.0, max(weights.get(f.severity, 0.5) for f in findings))


def is_scam_likely(text: str, *, threshold: float = 0.7) -> bool:
    return risk_score(text) >= threshold
