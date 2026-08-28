"""Opaque fleet-telemetry intake for Netie Clicks.

The intake deliberately never unwraps or decrypts an envelope. Fleet KEKs are
encrypted with the existing DMS AES-256-GCM field-encryption helper when
``DMS_MASTER_KEY`` is configured. For local deployments without that key, each
KEK is kept in a private (0600) JSON file; Windows deployments must additionally
restrict the telemetry directory ACL because POSIX mode bits are advisory there.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v1/telemetry", tags=["telemetry"])

_FILE_LOCK = threading.RLock()
_MAX_ENVELOPE_BYTES = 2 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _telemetry_dir() -> Path:
    configured = os.environ.get("CORTEX_TELEMETRY_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "data" / "telemetry"


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        # Windows ACLs, not chmod, are authoritative. See the module note.
        pass


def _write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a JSON document and request owner-only permissions."""
    _ensure_private_dir(path.parent)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _device_key_path(device_id: str) -> Path:
    digest = hashlib.sha256(device_id.encode("utf-8")).hexdigest()
    return _telemetry_dir() / "keys" / f"{digest}.json"


def _seal_kek(kek_b64: str) -> tuple[str, str]:
    if os.environ.get("DMS_MASTER_KEY"):
        from packs.dms.security.crypto import encrypt_field

        return "dms-aes256-gcm-v1", encrypt_field(kek_b64)
    return "filesystem-private-v1", kek_b64


def _open_kek(record: dict[str, Any]) -> str:
    storage = record.get("key_storage")
    value = record.get("netie_kek")
    if not isinstance(value, str) or not value:
        raise RuntimeError("fleet KEK record is invalid")
    if storage == "dms-aes256-gcm-v1":
        from packs.dms.security.crypto import decrypt_field

        return decrypt_field(value)
    if storage == "filesystem-private-v1":
        return value
    raise RuntimeError("fleet KEK record uses an unsupported storage format")


def _append_audit(event: dict[str, Any]) -> None:
    """Append non-secret metadata only; never record envelopes or KEKs."""
    path = _telemetry_dir() / "audit.jsonl"
    _ensure_private_dir(path.parent)
    line = json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
    with _FILE_LOCK:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()


class RegisterRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=256)
    product: str = Field(default="netie-clicks", min_length=1, max_length=64)


class TelemetryRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=256)
    lineage_id: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=64)
    envelope: dict[str, Any]
    user_verified: bool
    flush_reason: str = Field(default="unspecified", max_length=128)


@router.post("/register")
def register_device(req: RegisterRequest) -> dict[str, Any]:
    """Return a stable random 256-bit fleet KEK for a Clicks device."""
    if req.product != "netie-clicks":
        raise HTTPException(status_code=400, detail="unsupported telemetry product")

    path = _device_key_path(req.device_id)
    with _FILE_LOCK:
        existing = path.exists()
        if existing:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                kek_b64 = _open_kek(record)
            except Exception as exc:
                # Never rotate silently: existing envelopes depend on this KEK.
                raise HTTPException(status_code=503, detail="fleet KEK unavailable") from exc
        else:
            kek_b64 = b64encode(secrets.token_bytes(32)).decode("ascii")
            storage, stored_kek = _seal_kek(kek_b64)
            record = {
                "version": 1,
                "device_id": req.device_id,
                "product": req.product,
                "created_at": _now(),
                "key_storage": storage,
                "netie_kek": stored_kek,
                "mode_note": (
                    "AES-256-GCM envelope encryption via DMS_MASTER_KEY"
                    if storage == "dms-aes256-gcm-v1"
                    else "0600 file; restrict directory ACL on Windows"
                ),
            }
            _write_private_json(path, record)

    _append_audit(
        {
            "ts": _now(),
            "event": "fleet_device_registered",
            "device_hash": hashlib.sha256(req.device_id.encode("utf-8")).hexdigest()[:16],
            "existing": existing,
        }
    )
    return {"ok": True, "netie_kek_b64": kek_b64, "device_id": req.device_id}


@router.post("")
def ingest_telemetry(req: TelemetryRequest) -> dict[str, Any]:
    """Persist a sealed envelope verbatim; no decrypt operation exists here."""
    if req.user_verified is not True:
        raise HTTPException(status_code=403, detail="user_verified must be true")
    if not isinstance(req.envelope.get("wrap_netie"), dict) or not req.envelope["wrap_netie"]:
        raise HTTPException(status_code=400, detail="envelope.wrap_netie is required")

    envelope_bytes = json.dumps(
        req.envelope, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    if len(envelope_bytes) > _MAX_ENVELOPE_BYTES:
        raise HTTPException(status_code=413, detail="telemetry envelope exceeds 2 MiB")

    ingest_id = str(uuid.uuid4())
    stored = {
        "version": 1,
        "ingest_id": ingest_id,
        "received_at": _now(),
        "device_id": req.device_id,
        "lineage_id": req.lineage_id,
        "kind": req.kind,
        "flush_reason": req.flush_reason,
        "user_verified": True,
        "envelope": req.envelope,
    }
    with _FILE_LOCK:
        _write_private_json(_telemetry_dir() / "envelopes" / f"{ingest_id}.json", stored)
        _append_audit(
            {
                "ts": stored["received_at"],
                "event": "telemetry_envelope_stored",
                "ingest_id": ingest_id,
                "kind": req.kind,
                "device_hash": hashlib.sha256(req.device_id.encode("utf-8")).hexdigest()[:16],
                "lineage_hash": hashlib.sha256(req.lineage_id.encode("utf-8")).hexdigest()[:16],
            }
        )
    return {"ok": True, "stored": True, "ingest_id": ingest_id}


@router.get("/health")
def telemetry_health() -> dict[str, Any]:
    root = _telemetry_dir()
    return {
        "ok": True,
        "storage_ready": root.exists() or root.parent.exists(),
        "kek_encryption": "aes-256-gcm" if os.environ.get("DMS_MASTER_KEY") else "filesystem",
    }


def register_telemetry_routes(app: Any) -> None:
    app.include_router(router)


__all__ = ["register_telemetry_routes", "router"]
