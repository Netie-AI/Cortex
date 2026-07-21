"""Reversible PII tokenization with a local, never-egressed unmask vault.

Companion to the one-way ``pii.redact_for_prompt`` choke-point. Use this when the
model's answer must be **re-identified** afterwards: the *masked* text is safe to
send to an LLM, while the token -> plaintext map stays inside the process.

Design (see docs/ontology/SECURITY_TRACK_MAP.md):
  * Detection reuses ``pii.detect`` by default, but any callable returning
    ``PiiSpan``-like objects can be injected (e.g. the layered detector in
    ``pii_ner``) so NER-found entities tokenize through the same path.
  * Tokens are ASCII, uppercase, and stable across an LLM round-trip:
    ``NETIE_<KIND>_<hex6>``. The same plaintext yields the same token within one
    vault (per-vault salt, so tokens are not linkable across sessions).
  * The reverse map lives in memory. ``seal()`` encrypts it to an AES-256-GCM
    blob for optional at-rest persistence; ``purge()`` drops the references.

This module is **additive** — it does not modify the audited ``pii.py`` or
``prompt_harness.py`` gate. Wire it in behind an owner gate + adversarial re-test.
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Callable, Protocol

from packs.dms.security.pii import PiiSpan, detect as _regex_detect

Detector = Callable[[str], "list[PiiSpan]"]


class _SpanLike(Protocol):
    start: int
    end: int
    kind: str
    text: str


@dataclass(slots=True)
class MaskResult:
    """Outcome of a mask pass. ``masked`` is safe to egress; ``vault`` is not."""

    masked: str
    span_count: int
    kinds: tuple[str, ...]


@dataclass(slots=True)
class TokenVault:
    """Holds the token -> plaintext map for one session. Never serialize raw."""

    salt: bytes = field(default_factory=lambda: os.urandom(16))
    _rev: dict[str, str] = field(default_factory=dict)   # token -> plaintext
    _fwd: dict[str, str] = field(default_factory=dict)   # plaintext -> token

    # -- tokenization ------------------------------------------------------
    def _token_for(self, kind: str, value: str) -> str:
        existing = self._fwd.get(value)
        if existing is not None:
            return existing
        digest = hmac.new(self.salt, f"{kind}:{value}".encode("utf-8"), sha256).hexdigest()
        token = f"NETIE_{kind.upper()}_{digest[:6].upper()}"
        # Vanishingly unlikely, but keep tokens injective under collision.
        while token in self._rev and self._rev[token] != value:
            digest = sha256((digest + value).encode("utf-8")).hexdigest()
            token = f"NETIE_{kind.upper()}_{digest[:6].upper()}"
        self._rev[token] = value
        self._fwd[value] = token
        return token

    def mask(self, text: str, detector: Detector = _regex_detect) -> MaskResult:
        """Replace detected PII with reversible tokens; record the reverse map."""
        spans: list[_SpanLike] = sorted(detector(text), key=lambda s: s.start)
        if not spans:
            return MaskResult(masked=text, span_count=0, kinds=())
        parts: list[str] = []
        cursor = 0
        kinds: list[str] = []
        for span in spans:
            if span.start < cursor:  # skip overlaps defensively
                continue
            parts.append(text[cursor:span.start])
            parts.append(self._token_for(span.kind, span.text))
            kinds.append(span.kind)
            cursor = span.end
        parts.append(text[cursor:])
        return MaskResult(masked="".join(parts), span_count=len(kinds), kinds=tuple(kinds))

    def unmask(self, text: str) -> str:
        """Restore plaintext from tokens in an LLM response. Local only."""
        if not self._rev:
            return text
        # Longest tokens first is irrelevant (fixed format), but stable order helps tests.
        for token, value in self._rev.items():
            if token in text:
                text = text.replace(token, value)
        return text

    # -- introspection / lifecycle ----------------------------------------
    def audit_summary(self) -> dict[str, object]:
        """Non-sensitive view for the ledger — counts and kinds, never values."""
        kinds: dict[str, int] = {}
        for token in self._rev:
            kind = token.split("_")[1].lower() if "_" in token else "unknown"
            kinds[kind] = kinds.get(kind, 0) + 1
        return {"tokens": len(self._rev), "by_kind": kinds}

    def seal(self, key: bytes | None = None) -> tuple[bytes, bytes]:
        """AES-256-GCM encrypt the reverse map. Returns (key, nonce+ciphertext).

        Caller owns the key lifetime; never write it beside the ciphertext.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = key or AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        blob = json.dumps(self._rev, separators=(",", ":")).encode("utf-8")
        ct = AESGCM(key).encrypt(nonce, blob, b"netie-token-vault")
        return key, nonce + ct

    @classmethod
    def unseal(cls, key: bytes, sealed: bytes, salt: bytes | None = None) -> "TokenVault":
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce, ct = sealed[:12], sealed[12:]
        blob = AESGCM(key).decrypt(nonce, ct, b"netie-token-vault")
        rev = json.loads(blob.decode("utf-8"))
        vault = cls(salt=salt or os.urandom(16))
        vault._rev = dict(rev)
        vault._fwd = {v: k for k, v in rev.items()}
        return vault

    def purge(self) -> None:
        """Drop the plaintext map. Python can't hard-zeroize str, so also overwrite."""
        for k in list(self._rev):
            self._rev[k] = "\x00"
        self._rev.clear()
        self._fwd.clear()


def mask_text(text: str, detector: Detector = _regex_detect) -> tuple[MaskResult, TokenVault]:
    """One-shot helper: fresh vault, mask, return both. Keep the vault local."""
    vault = TokenVault()
    return vault.mask(text, detector=detector), vault
