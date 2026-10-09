"""Optional Gmail IMAP read. Drop .eml is the default. Crew never sends."""

from __future__ import annotations

import email
import imaplib
import os
from email.header import decode_header
from typing import Any

DEFAULT_HOST = "imap.gmail.com"


def configured() -> bool:
    return bool(
        os.environ.get("GMAIL_IMAP_USER", "").strip()
        and os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    )


def _decode(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    parts = decode_header(raw)
    out: list[str] = []
    for text, charset in parts:
        if isinstance(text, bytes):
            out.append(text.decode(charset or "utf-8", "replace"))
        else:
            out.append(str(text))
    return " ".join(out).strip()


def status(*, limit: int = 8) -> dict[str, Any]:
    if not configured():
        return {
            "ok": False,
            "connected": False,
            "messages": [],
            "detail": "Drop .eml/.txt onto Crew. IMAP needs GMAIL_IMAP_USER + GMAIL_APP_PASSWORD in Providers. Crew never sends mail.",
        }
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {
            "ok": True,
            "connected": True,
            "messages": [],
            "detail": "IMAP configured (live probes off)",
        }
    return fetch(limit=limit)


def fetch(limit: int = 8) -> dict[str, Any]:
    user = os.environ.get("GMAIL_IMAP_USER", "").strip()
    secret = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    host = os.environ.get("GMAIL_IMAP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    if not user or not secret:
        return status()
    client: imaplib.IMAP4_SSL | None = None
    try:
        client = imaplib.IMAP4_SSL(host, timeout=12)
        client.login(user, secret)
        client.select("INBOX", readonly=True)
        _typ, data = client.search(None, "ALL")
        ids = (data[0] or b"").split()
        take = ids[-limit:] if ids else []
        messages: list[dict[str, str]] = []
        for mid in take:
            _typ, payload = client.fetch(mid, "(RFC822.HEADER)")
            if not payload or payload[0] is None:
                continue
            blob = payload[0][1] if isinstance(payload[0], tuple) else payload[0]
            parsed = email.message_from_bytes(blob if isinstance(blob, bytes) else b"")
            messages.append(
                {
                    "from": _decode(parsed.get("From")),
                    "subject": _decode(parsed.get("Subject")) or "(no subject)",
                    "date": _decode(parsed.get("Date")),
                }
            )
        return {
            "ok": True,
            "connected": True,
            "messages": list(reversed(messages)),
            "detail": f"{len(messages)} recent headers. Human remains the sender.",
        }
    except (OSError, imaplib.IMAP4.error) as exc:
        return {
            "ok": False,
            "connected": False,
            "messages": [],
            "detail": f"IMAP failed ({type(exc).__name__}). Drop .eml instead. Crew never sends.",
        }
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - logout is best-effort
                pass
