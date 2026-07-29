"""Signed session manifest verification and enforcement.

DMS mints a manifest and signs it. Cortex verifies and enforces it. OpenVault
roots the key. No single one of those three can widen a session's reach on its
own, which is the whole point of splitting them.

Two halves live here:

**Verification** — is this manifest genuinely from an issuer we trust, right
now? Signature over :func:`canonical_manifest_bytes`, issuer resolved from a
JWKS cached on disk. Verification never touches the network: an OpenVault
outage must not take the appliance down, and a verify path that can block on a
socket is a verify path an attacker can stall.

**Enforcement** — does this SQL stay inside what the manifest grants? Answered
by walking the sqlglot AST, never by matching strings. Every table reference,
every ``read_parquet``/``read_csv``/``read_json`` argument and every ``ATTACH``
target is resolved and checked against ``allowed_paths``; every referenced table
carrying a predicate is rewritten to apply it.

The asymmetry is deliberate: anything this module cannot fully analyse is
refused. A query the enforcer does not understand is not a query it can prove
safe.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from CortexOS.paths import data_path

try:  # installed wheel ships the contract as a top-level package
    from cortex_contract.execution import Manifest, canonical_manifest_bytes
except ImportError:  # in-repo checkout
    from packages.cortex_contract.execution import Manifest, canonical_manifest_bytes

__all__ = [
    "ManifestError",
    "ManifestMalformed",
    "ManifestUnknownIssuer",
    "ManifestSignatureInvalid",
    "ManifestExpired",
    "ManifestNotYetValid",
    "JwksCache",
    "ManifestVerifier",
    "VerifiedManifest",
]


# ── errors ───────────────────────────────────────────────────────────────────
# Distinct classes, not one error with a string: DMS must react differently to
# "expired, re-mint once" and "rejected, log a security event and never
# re-mint", and a caller cannot branch safely on prose.


class ManifestError(Exception):
    """Base for every manifest refusal. Never raised directly."""

    #: Stable slug for logs, metrics and the DMS error-class mapping.
    code = "manifest_error"


class ManifestMalformed(ManifestError):
    """Structurally unusable — missing issuer, unparseable timestamp, bad signature encoding."""

    code = "manifest_malformed"


class ManifestUnknownIssuer(ManifestError):
    """``issuer_key_id`` is not in the cached JWKS, or its key has expired."""

    code = "manifest_unknown_issuer"


class ManifestSignatureInvalid(ManifestError):
    """Signature does not verify against the issuer's public key."""

    code = "manifest_signature_invalid"


class ManifestExpired(ManifestError):
    """``expires_at`` is in the past."""

    code = "manifest_expired"


class ManifestNotYetValid(ManifestError):
    """``issued_at`` is in the future by more than the allowed clock skew."""

    code = "manifest_not_yet_valid"


# ── time helpers ─────────────────────────────────────────────────────────────


def _parse_ts(raw: str | None, *, field_name: str) -> datetime:
    """Parse an ISO-8601 timestamp, requiring it to be unambiguous about zone.

    A naive timestamp is refused rather than assumed UTC. Guessing a zone in a
    security check means the same manifest is valid on one host and expired on
    another.
    """
    if not raw:
        raise ManifestMalformed(f"{field_name} is missing")
    text = raw.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ManifestMalformed(f"{field_name} is not ISO-8601: {raw!r}") from exc
    if parsed.tzinfo is None:
        raise ManifestMalformed(f"{field_name} has no timezone offset: {raw!r}")
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── JWKS cache ───────────────────────────────────────────────────────────────


def _b64url_decode(raw: str, *, what: str) -> bytes:
    """Decode unpadded base64url. Rejects anything else — one encoding, strictly."""
    text = raw.strip()
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (ValueError, TypeError) as exc:
        raise ManifestMalformed(f"{what} is not base64url") from exc


@dataclass(frozen=True, slots=True)
class IssuerKey:
    """One Ed25519 public key from the JWKS, with the validity window it declares."""

    kid: str
    public_key: Ed25519PublicKey
    not_before: datetime | None = None
    not_after: datetime | None = None

    def usable_at(self, when: datetime, *, skew_s: int) -> bool:
        if self.not_before and when < self.not_before - _delta(skew_s):
            return False
        if self.not_after and when > self.not_after + _delta(skew_s):
            return False
        return True


def _delta(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def _epoch_or_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        try:
            return _parse_ts(value, field_name="key validity")
        except ManifestMalformed:
            return None
    return None


def _parse_jwks(document: Any) -> dict[str, IssuerKey]:
    """Turn a JWKS document into usable keys, skipping entries we cannot use.

    A JWKS carrying one key type we do not support must not poison the whole
    set — otherwise OpenVault adding an RSA key for some unrelated purpose
    would silently stop every manifest from verifying.
    """
    keys: dict[str, IssuerKey] = {}
    if not isinstance(document, dict):
        return keys
    for entry in document.get("keys") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("kty") != "OKP" or entry.get("crv") != "Ed25519":
            continue
        kid = entry.get("kid")
        x = entry.get("x")
        if not isinstance(kid, str) or not isinstance(x, str):
            continue
        try:
            raw = _b64url_decode(x, what=f"jwk {kid}.x")
            public = Ed25519PublicKey.from_public_bytes(raw)
        except (ManifestMalformed, ValueError):
            continue
        keys[kid] = IssuerKey(
            kid=kid,
            public_key=public,
            not_before=_epoch_or_iso(entry.get("nbf")),
            not_after=_epoch_or_iso(entry.get("exp")),
        )
    return keys


DEFAULT_JWKS_PATH = "/keys/jwks"
DEFAULT_REFRESH_TTL_S = 900


class JwksCache:
    """Issuer public keys, cached on disk, refreshed off the hot path.

    Two clocks, deliberately separate:

    * the **cache TTL** says when a refresh is *wanted*. Passing it never makes
      cached keys unusable — that would turn an OpenVault outage into a total
      outage, which is exactly what the disk cache exists to prevent;
    * each key's own ``exp`` says when it stops being *trusted*. Intermediates
      are short-lived by design, so expiry is carried in the key material where
      an outage cannot quietly extend it.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        ttl_s: int = DEFAULT_REFRESH_TTL_S,
        jwks_path: str = DEFAULT_JWKS_PATH,
    ) -> None:
        self.path = path or Path(
            os.environ.get("CORTEX_JWKS_CACHE") or data_path("engine", "openvault_jwks.json")
        )
        self.ttl_s = ttl_s
        self.jwks_path = jwks_path
        self._keys: dict[str, IssuerKey] = {}
        self._fetched_at: float = 0.0
        self._loaded = False

    # -- disk ---------------------------------------------------------------

    def _load_from_disk(self) -> None:
        self._loaded = True
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(blob, dict):
            return
        self._keys = _parse_jwks(blob.get("jwks"))
        fetched = blob.get("fetched_at")
        self._fetched_at = float(fetched) if isinstance(fetched, (int, float)) else 0.0

    def _write_to_disk(self, document: Any, fetched_at: float) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"fetched_at": fetched_at, "jwks": document}, indent=2),
                encoding="utf-8",
            )
        except OSError:
            # A read-only or full disk must not break verification for a
            # process that already holds the keys in memory.
            pass

    # -- reads (hot path — never network) ------------------------------------

    def get(self, kid: str) -> IssuerKey | None:
        if not self._loaded:
            self._load_from_disk()
        return self._keys.get(kid)

    @property
    def is_stale(self) -> bool:
        if not self._loaded:
            self._load_from_disk()
        return (time.time() - self._fetched_at) > self.ttl_s

    @property
    def known_kids(self) -> tuple[str, ...]:
        if not self._loaded:
            self._load_from_disk()
        return tuple(sorted(self._keys))

    # -- writes (cold path — may touch the network) --------------------------

    def install(self, document: Any, *, fetched_at: float | None = None) -> int:
        """Replace the cached set from a JWKS document. Returns keys accepted."""
        keys = _parse_jwks(document)
        if not keys:
            # Never let an empty or unparseable response erase working keys.
            return 0
        stamp = time.time() if fetched_at is None else fetched_at
        self._keys = keys
        self._fetched_at = stamp
        self._loaded = True
        self._write_to_disk(document, stamp)
        return len(keys)

    def refresh(self, *, timeout: float = 2.0) -> bool:
        """Fetch the JWKS from OpenVault. Cold path only — never call from verify().

        Returns whether the cache was updated. Failure is not an error: the
        cached keys keep working and the caller keeps serving.
        """
        from CortexOS.integrations.openvault_client import get_json

        document = get_json(self.jwks_path, timeout=timeout)
        if document is None:
            return False
        return self.install(document) > 0


# ── verification ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class VerifiedManifest:
    """A manifest whose signature, issuer and validity window all checked out.

    Enforcement takes one of these, never a raw ``Manifest`` — the type is the
    proof that verification happened, so no call site can skip it by accident.
    """

    manifest: Manifest
    issuer_kid: str
    verified_at: datetime

    @property
    def allowed_paths(self) -> tuple[str, ...]:
        return tuple(self.manifest.allowed_paths)

    @property
    def row_predicates(self) -> dict[str, str]:
        return dict(self.manifest.row_predicates)


DEFAULT_CLOCK_SKEW_S = 120


class ManifestVerifier:
    """Verifies manifests against cached issuer keys. Never makes a network call."""

    def __init__(
        self,
        cache: JwksCache | None = None,
        *,
        clock_skew_s: int = DEFAULT_CLOCK_SKEW_S,
    ) -> None:
        self.cache = cache or JwksCache()
        self.clock_skew_s = clock_skew_s

    def verify(self, manifest: Manifest, *, now: datetime | None = None) -> VerifiedManifest:
        """Return a :class:`VerifiedManifest` or raise a :class:`ManifestError` subclass.

        Checked in this order so the cheapest refusal comes first and a caller
        reading logs sees the most specific reason, not a generic one.
        """
        when = now or _now()

        kid = (manifest.issuer_key_id or "").strip()
        if not kid:
            raise ManifestMalformed("issuer_key_id is missing; nothing to verify against")
        if not manifest.signature:
            raise ManifestMalformed("signature is missing")

        issued_at = _parse_ts(manifest.issued_at, field_name="issued_at")
        expires_at = _parse_ts(manifest.expires_at, field_name="expires_at")
        if expires_at <= issued_at:
            raise ManifestMalformed("expires_at is not after issued_at")

        skew = _delta(self.clock_skew_s)
        if issued_at - skew > when:
            raise ManifestNotYetValid(
                f"issued_at {manifest.issued_at} is ahead of this host by more than "
                f"{self.clock_skew_s}s"
            )
        if expires_at + skew < when:
            raise ManifestExpired(f"expired at {manifest.expires_at}")

        key = self.cache.get(kid)
        if key is None:
            raise ManifestUnknownIssuer(f"issuer_key_id {kid!r} is not in the cached JWKS")
        if not key.usable_at(when, skew_s=self.clock_skew_s):
            raise ManifestUnknownIssuer(f"issuer key {kid!r} is outside its own validity window")

        signature = _b64url_decode(manifest.signature, what="signature")
        try:
            key.public_key.verify(signature, canonical_manifest_bytes(manifest))
        except InvalidSignature as exc:
            raise ManifestSignatureInvalid(f"signature does not verify under key {kid!r}") from exc

        return VerifiedManifest(manifest=manifest, issuer_kid=kid, verified_at=when)
