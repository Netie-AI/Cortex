"""C-SEC-1 — ``secure_reversible()``: audited harness ∘ TokenVault, flag-gated.

Composition contract (the audited gate is NEVER modified, only called):

  1. ``secure_for_prompt(raw)`` runs FIRST on the raw text. Block decisions
     (scam / injection) are therefore byte-identical to the audited gate. A
     blocked input returns blocked with **no vault** — nothing reversible may
     exist for text the gate refused.
  2. Only if the gate passed AND ``DMS_REVERSIBLE_PII=1``: the vault masks the
     raw text (PII → ``NETIE_<KIND>_<hex6>`` tokens), then the harness runs a
     SECOND pass over the masked text. The regex floor in that pass one-way
     redacts anything the vault's detector missed — the floor never fails open.
  3. Flag off (default): behavior is exactly the audited one-way path and the
     returned ``vault`` is ``None`` — callers can adopt this function today
     with zero semantic change.

The vault must never leave the process; ``ledger_safe_summary()`` is the only
shape of it that may be written to the audit ledger (counts and kinds, no
values).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from packs.dms.security.prompt_harness import HarnessResult, secure_for_prompt
from packs.dms.security.token_vault import Detector, TokenVault
from packs.dms.security.pii import detect as _regex_detect

FLAG = "DMS_REVERSIBLE_PII"


def reversible_enabled() -> bool:
    return os.environ.get(FLAG, "").strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True, slots=True)
class ReversibleResult:
    """``safe_text`` may egress to a model; ``vault`` must not leave the process."""

    safe_text: str
    blocked: bool
    block_reason: str | None
    reversible: bool                 # True only when a live vault backs the tokens
    span_count: int
    harness: HarnessResult           # the audited gate's verdict on the raw text
    vault: TokenVault | None = None


def secure_reversible(
    text: str,
    *,
    detector: Detector = _regex_detect,
    block_injection: bool = True,
    block_scam: bool = False,
    scam_threshold: float = 0.85,
) -> ReversibleResult:
    """Reversible-capable choke-point. See module docstring for the contract."""
    gate = secure_for_prompt(
        text,
        block_injection=block_injection,
        block_scam=block_scam,
        scam_threshold=scam_threshold,
    )
    if gate.blocked:
        # Fail-closed: no vault is created for blocked input.
        return ReversibleResult(
            safe_text=gate.safe_text, blocked=True, block_reason=gate.block_reason,
            reversible=False, span_count=0, harness=gate, vault=None,
        )

    if not reversible_enabled():
        return ReversibleResult(
            safe_text=gate.safe_text, blocked=False, block_reason=None,
            reversible=False, span_count=0, harness=gate, vault=None,
        )

    vault = TokenVault()
    masked = vault.mask(text, detector=detector)
    # Second audited pass over the masked text: injection sanitize re-applies and
    # the regex floor one-way redacts anything the vault detector missed.
    floor = secure_for_prompt(
        masked.masked,
        block_injection=block_injection,
        block_scam=block_scam,
        scam_threshold=scam_threshold,
    )
    if floor.blocked:  # pragma: no cover — masking cannot introduce new attacks,
        # but if the gate ever disagrees we fail closed and drop the vault.
        vault.purge()
        return ReversibleResult(
            safe_text=floor.safe_text, blocked=True, block_reason=floor.block_reason,
            reversible=False, span_count=0, harness=floor, vault=None,
        )
    return ReversibleResult(
        safe_text=floor.safe_text, blocked=False, block_reason=None,
        reversible=True, span_count=masked.span_count, harness=floor, vault=vault,
    )


def ledger_safe_summary(result: ReversibleResult) -> dict[str, object]:
    """The ONLY vault-derived shape allowed into ``ledger.append`` payloads."""
    summary: dict[str, object] = {
        "reversible": result.reversible,
        "blocked": result.blocked,
        "span_count": result.span_count,
    }
    if result.vault is not None:
        summary["vault"] = result.vault.audit_summary()  # counts + kinds only
    return summary
