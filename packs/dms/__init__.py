"""DMS Brain — warehouse / SME vertical pack (plug-and-play surface)."""

from __future__ import annotations

from typing import Any

__all__ = [
    "plug_in",
    "secure_message",
    "classify_message",
    "append_ledger",
    "verify_ledger",
]


def secure_message(text: str, *, block_scam: bool = True) -> dict[str, Any]:
    """Bank-grade pre-LLM gate: scam → injection → PII (optionally reversible).

    Default (``DMS_REVERSIBLE_PII`` unset): identical to audited ``secure_for_prompt``.
    Flag on: harness ∘ TokenVault via ``secure_reversible`` — vault never returned.
    """
    from packs.dms.security.reversible import secure_reversible

    r = secure_reversible(text, block_injection=True, block_scam=block_scam)
    return {
        "safe_text": r.safe_text,
        "blocked": r.blocked,
        "block_reason": r.block_reason,
        "scam_risk": r.harness.scam_risk,
        "pii_redacted": r.harness.pii_redacted,
        "reversible": r.reversible,
    }


def classify_message(text: str) -> dict[str, Any]:
    """F3 warehouse intent + sentiment + psychological state."""
    from packs.dms.classify.intent import classify

    r = classify(text)
    return {
        "intent": r.intent,
        "sentiment": r.sentiment,
        "confidence": r.confidence,
        "blocked": r.blocked,
        "psychological_state": r.psychological_state,
    }


def append_ledger(actor: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    from packs.dms.audit.ledger import append

    e = append(actor, event_type, payload)
    return {"seq": e.seq, "entry_hash": e.entry_hash, "event_type": e.event_type}


def verify_ledger() -> dict[str, Any]:
    from packs.dms.audit.ledger import verify

    r = verify()
    return {"ok": r.ok, "broken_at": r.broken_at}


def plug_in(app: Any | None = None, *, pack: str = "dms") -> dict[str, Any]:
    """
    One-call DMS integration for FastAPI apps or scripts.

    Example:
        from fastapi import FastAPI
        from packs.dms import plug_in
        app = FastAPI()
        plug_in(app)  # registers /dms/* routes
    """
    import os

    os.environ.setdefault("PACK", pack)
    registered: list[str] = []
    if app is not None:
        from CortexOS.api.dms_query import register_dms_routes

        register_dms_routes(app)
        registered.append("dms_routes")
        try:
            from CortexOS.api.chat_routes import register_chat_routes

            register_chat_routes(app)
            registered.append("chat_routes")
        except ImportError:
            pass
        try:
            from CortexOS.api.warehouse_routes import register_warehouse_routes

            register_warehouse_routes(app)
            registered.append("warehouse_routes")
        except ImportError:
            pass
        try:
            from CortexOS.api.brain_routes import register_brain_routes

            register_brain_routes(app)
            registered.append("brain_routes")
        except ImportError:
            pass
    return {
        "pack": pack,
        "version": "0.1.0",
        "routes_registered": registered,
        "security": ["pii", "injection", "scam", "ledger"],
    }
