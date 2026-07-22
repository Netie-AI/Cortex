"""AES-256-GCM envelope encryption for sensitive DMS fields (F7)."""

from __future__ import annotations

import base64
import os
import struct
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ENV_MASTER_KEY: Final[str] = "DMS_MASTER_KEY"
_VERSION: Final[int] = 1
_NONCE_LEN: Final[int] = 12
_TAG_LEN: Final[int] = 16
_KEY_LEN: Final[int] = 32


def _load_master_key() -> bytes:
    raw = os.environ.get(_ENV_MASTER_KEY)
    if not raw:
        raise ValueError(f"{_ENV_MASTER_KEY} environment variable is required")
    try:
        key = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError(f"{_ENV_MASTER_KEY} must be valid base64") from exc
    if len(key) != _KEY_LEN:
        raise ValueError(f"{_ENV_MASTER_KEY} must decode to {_KEY_LEN} bytes")
    return key


def encrypt_field(plaintext: str) -> str:
    """Encrypt *plaintext* with a per-field data key wrapped by the master key."""
    master_key = _load_master_key()
    data_key = os.urandom(_KEY_LEN)

    data_nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(data_key).encrypt(data_nonce, plaintext.encode("utf-8"), None)

    key_nonce = os.urandom(_NONCE_LEN)
    wrapped_key = AESGCM(master_key).encrypt(key_nonce, data_key, None)

    blob = struct.pack(
        ">B",
        _VERSION,
    ) + key_nonce + wrapped_key + data_nonce + ciphertext
    return base64.b64encode(blob).decode("ascii")


def decrypt_field(blob_b64: str) -> str:
    """Decrypt a value produced by :func:`encrypt_field`."""
    master_key = _load_master_key()
    raw = base64.b64decode(blob_b64, validate=True)

    version = raw[0]
    if version != _VERSION:
        raise ValueError(f"unsupported ciphertext version: {version}")

    offset = 1
    key_nonce = raw[offset : offset + _NONCE_LEN]
    offset += _NONCE_LEN
    wrapped_key = raw[offset : offset + _KEY_LEN + _TAG_LEN]
    offset += _KEY_LEN + _TAG_LEN
    data_nonce = raw[offset : offset + _NONCE_LEN]
    offset += _NONCE_LEN
    ciphertext = raw[offset:]

    data_key = AESGCM(master_key).decrypt(key_nonce, wrapped_key, None)
    plaintext = AESGCM(data_key).decrypt(data_nonce, ciphertext, None)
    return plaintext.decode("utf-8")
