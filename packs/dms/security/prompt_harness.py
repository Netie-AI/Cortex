"""Unified security choke-point: PII + injection + scam before any LLM path."""

from __future__ import annotations

from dataclasses import dataclass, field

from packs.dms.security.injection_guard import InjectionFinding, is_blocked, sanitize_for_prompt
from packs.dms.security.pii import redact_for_prompt
from packs.dms.security.scam_guard import ScamFinding, scan as scan_scam


@dataclass(frozen=True, slots=True)
class HarnessResult:
    original_len: int
    safe_text: str
    blocked: bool
    block_reason: str | None
    pii_redacted: bool
    injection_findings: list[InjectionFinding] = field(default_factory=list)
    scam_findings: list[ScamFinding] = field(default_factory=list)
    scam_risk: float = 0.0


def secure_for_prompt(
    text: str,
    *,
    block_injection: bool = True,
    block_scam: bool = False,
    scam_threshold: float = 0.85,
) -> HarnessResult:
    """
    Bank-grade pre-model gate. Order: scam block → injection block/sanitize → PII redact.
    """
    scam_findings = scan_scam(text)
    scam_risk = max((0.7 if f.severity == "high" else 1.0 for f in scam_findings), default=0.0)
    if block_scam and scam_risk >= scam_threshold:
        return HarnessResult(
            original_len=len(text),
            safe_text="[BLOCKED:scam_detected]",
            blocked=True,
            block_reason="scam_threshold",
            pii_redacted=False,
            scam_findings=scam_findings,
            scam_risk=scam_risk,
        )

    if block_injection and is_blocked(text):
        return HarnessResult(
            original_len=len(text),
            safe_text="[BLOCKED:prompt_injection]",
            blocked=True,
            block_reason="injection_critical",
            pii_redacted=False,
            injection_findings=[],
            scam_findings=scam_findings,
            scam_risk=scam_risk,
        )

    cleaned, injection_findings = sanitize_for_prompt(text)
    redacted = redact_for_prompt(cleaned)
    return HarnessResult(
        original_len=len(text),
        safe_text=redacted,
        blocked=False,
        block_reason=None,
        pii_redacted=redacted != cleaned,
        injection_findings=injection_findings,
        scam_findings=scam_findings,
        scam_risk=scam_risk,
    )
