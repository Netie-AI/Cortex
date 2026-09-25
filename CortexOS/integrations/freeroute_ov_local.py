"""Isolated OpenVault LOCAL-1 field adapter (Cortex #272).

OpenVault#71 adopts these wire names exactly: ``served_provider``,
``served_model``, ``served_local``, ``local_only``, ``local_reason``.
Those five are no longer PENDING.

PENDING OpenVault#71 extras Cortex does not read: ``X-OpenVault-Served-*``
headers, SSE ``served_*`` copies, internal ``ProviderSpec.local_hop`` /
``tier="local"``. Do not treat those as Cortex stamp fields.

Ceiling: local is not proven until a served run shows ``served_local=true``
on every call.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# OpenVault#71 confirmed wire names.
SERVED_PROVIDER = "served_provider"
SERVED_MODEL = "served_model"
SERVED_LOCAL = "served_local"
LOCAL_ONLY_REQUEST = "local_only"
LOCAL_ARMING_REASON = "local_reason"

# OpenVault#71 local-only refusal. Sealed stays 403 / openvault_vault_sealed.
LOCAL_ONLY_UNAVAILABLE_TYPE = "openvault_local_only_unavailable"
VAULT_SEALED_TYPE = "openvault_vault_sealed"
LOCAL_REASON_UNREACHABLE = "local_unreachable"
LOCAL_REASON_MODEL_NOT_LOADED = "local_model_not_loaded"
LOCAL_REASON_NOT_LOOPBACK = "local_base_url_not_loopback"
LOCAL_UNAVAILABLE_REASONS = frozenset(
    {
        LOCAL_REASON_UNREACHABLE,
        LOCAL_REASON_MODEL_NOT_LOADED,
        LOCAL_REASON_NOT_LOOPBACK,
    }
)
LOCAL_PROVIDER_ID = "local_qwen"

MISSING_PROVIDER = "OpenVault response omitted served_provider"
MISSING_MODEL = "OpenVault response omitted served_model"
MISSING_LOCAL = "OpenVault response omitted served_local"
NOT_LOCAL = "OpenVault served_local is not true"


def _as_mapping(data: Any) -> Mapping[str, Any]:
    return data if isinstance(data, Mapping) else {}


def _error_object(data: Any) -> Mapping[str, Any]:
    err = _as_mapping(data).get("error")
    return err if isinstance(err, Mapping) else {}


def served_from_response(data: Any) -> tuple[str | None, str | None, bool, str]:
    """Read OV#71 stamp fields. Missing => null/false plus a named reason.

    Never infers from a requested or OpenAI-compat ``model`` value.
    """
    body = _as_mapping(data)
    reasons: list[str] = []

    raw_provider = body.get(SERVED_PROVIDER)
    if isinstance(raw_provider, str) and raw_provider.strip():
        provider: str | None = raw_provider.strip()
    else:
        provider = None
        reasons.append(MISSING_PROVIDER)

    raw_model = body.get(SERVED_MODEL)
    if isinstance(raw_model, str) and raw_model.strip():
        model: str | None = raw_model.strip()
    else:
        model = None
        reasons.append(MISSING_MODEL)

    if SERVED_LOCAL not in body:
        local = False
        reasons.append(MISSING_LOCAL)
    elif body.get(SERVED_LOCAL) is True:
        local = True
    else:
        local = False
        reasons.append(NOT_LOCAL)

    return provider, model, local, "; ".join(reasons)


def hop_reported_local(row: Any) -> bool:
    """True only when the row explicitly sets served_local True.

    Never inferred from provider id or model name.
    """
    return _as_mapping(row).get(SERVED_LOCAL) is True


def local_only_request_fields() -> dict[str, Any]:
    """Body fragment sent when CORTEX_FREEROUTE_LOCAL_ONLY=1 (OV#71)."""
    return {LOCAL_ONLY_REQUEST: True}


def local_arming_reason(data: Any) -> str:
    """Pass-through of OpenVault ``local_reason`` (status or hop). Empty when absent."""
    raw = _as_mapping(data).get(LOCAL_ARMING_REASON)
    return raw.strip() if isinstance(raw, str) and raw.strip() else ""


def chat_error_fields(data: Any) -> tuple[str, str]:
    """Return ``(error.type, error.reason)`` from an OpenVault chat error body."""
    err = _error_object(data)
    raw_type = err.get("type")
    raw_reason = err.get("reason")
    typ = raw_type.strip() if isinstance(raw_type, str) and raw_type.strip() else ""
    reason = raw_reason.strip() if isinstance(raw_reason, str) and raw_reason.strip() else ""
    return typ, reason


def local_only_unavailable(data: Any) -> str:
    """Named OV#71 local-only refusal reason, or '' when this is not that shape."""
    typ, reason = chat_error_fields(data)
    if typ != LOCAL_ONLY_UNAVAILABLE_TYPE:
        return ""
    return reason if reason in LOCAL_UNAVAILABLE_REASONS else reason


def vault_sealed_error(data: Any) -> bool:
    typ, _reason = chat_error_fields(data)
    return typ == VAULT_SEALED_TYPE


def count_local_spendable(
    hops: Iterable[Any] | None,
    specs: Iterable[Any] | None,
    catalogue_ids: set[str] | frozenset[str],
) -> int:
    """How many catalogue ids OpenVault marked local (hop or spendable spec)."""
    found: set[str] = set()
    for spec in specs or ():
        if not isinstance(spec, Mapping):
            continue
        pid = str(spec.get("id") or "").strip()
        if pid in catalogue_ids and hop_reported_local(spec):
            found.add(pid)
    for hop in hops or ():
        if not isinstance(hop, Mapping):
            continue
        pid = str(hop.get("provider") or "").strip()
        if pid in catalogue_ids and hop_reported_local(hop):
            found.add(pid)
    return len(found)


__all__ = [
    "LOCAL_ARMING_REASON",
    "LOCAL_ONLY_REQUEST",
    "LOCAL_ONLY_UNAVAILABLE_TYPE",
    "LOCAL_PROVIDER_ID",
    "LOCAL_REASON_MODEL_NOT_LOADED",
    "LOCAL_REASON_NOT_LOOPBACK",
    "LOCAL_REASON_UNREACHABLE",
    "LOCAL_UNAVAILABLE_REASONS",
    "MISSING_LOCAL",
    "MISSING_MODEL",
    "MISSING_PROVIDER",
    "NOT_LOCAL",
    "SERVED_LOCAL",
    "SERVED_MODEL",
    "SERVED_PROVIDER",
    "VAULT_SEALED_TYPE",
    "chat_error_fields",
    "count_local_spendable",
    "hop_reported_local",
    "local_arming_reason",
    "local_only_request_fields",
    "local_only_unavailable",
    "served_from_response",
    "vault_sealed_error",
]
