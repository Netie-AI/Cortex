"""OpenVault FreeRoute - the one Cortex model layer (Cortex #211 follow-up).

Every Cortex model call that thinks, generates, or acts goes through here:
engine generative-ask (NL -> ontology -> SQL -> validate), Crew Insights
generate, and the Crew chat OpenVault connector. One arming rule, one measured
route, one credential. Sync and stdlib only, so the engine answer path can call
it without an event loop and Crew can adapt it on a thread.

Custody. Provider secrets never enter this process. Cortex presents either an
OpenVault-issued ``ov_`` key (``CORTEX_FREEROUTE_TOKEN``, issued by the operator
with OpenVault ``POST /api/apikeys``) or nothing, which OpenVault serves as the
loopback tier. OpenVault verifies the bearer itself; no header Cortex writes
about itself carries authority (KB A-0009).

Armed means ``GET /api/freeroute/status`` says: reachable, ``sealed`` is False,
and either ``pooled_key_count`` > 0 with a spendable hop, or OpenVault reports
a LOCAL spendable hop (Cortex #272 / OpenVault#71). Process-env provider keys,
a process-local Ollama, ``/api/keys`` rows and ``/api/healthz`` never arm
FreeRoute. A hop is local only when OpenVault marks ``served_local`` true;
Cortex never infers that from a model name. Anything else is a named refusal
(R-0011).

The one exception is an explicit operator opt-in, ``CORTEX_MODEL_TRANSPORT=env-direct``
(see :mod:`CortexOS.integrations.direct_providers`): arming, the leave decision
and the transport then come from provider keys in the process env, and every
stamp reads ``NOT OpenVault FreeRoute (env-direct)`` so the custody change is
visible on every answer. ``CORTEX_FREEROUTE=0`` still switches the layer off in
this mode. The operator's env keys serve in-process callers only: a relayed
caller (``bearer`` given) is refused, never spent on. The opt-in is latched at
first read per process (:func:`reset` forgets it). Unset, nothing in this
module changes.

Measured route. OpenVault treats the requested model as a preference: it walks
hops in its own order and a hop that does not carry the requested id serves its
own first catalogued model. So every call records requested AND served, validity
is scored against the served model from validator verdicts (SQL gate,
plausibility, static guardrail), and a requested id OpenVault does not honour is
ranked by what it actually got. HTTP refusals are recorded but never scored.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from CortexOS.integrations import (
    direct_providers,
    freeroute_ov_local,
    freeroute_prespend,
    openvault_client,
    pii_mask,
)

IMPL = "openvault-freeroute"
TOKEN_ENV = "CORTEX_FREEROUTE_TOKEN"
MODELS_ENV = "CORTEX_FREEROUTE_MODELS"
STORE_ENV = "CORTEX_FREEROUTE_SCOREBOARD"
LEARN_ENV = "CORTEX_FREEROUTE_LEARN"
SWITCH_ENV = "CORTEX_FREEROUTE"
LOCAL_ONLY_ENV = "CORTEX_FREEROUTE_LOCAL_ONLY"

ARMING_TTL_S = 5.0
REJECT_TTL_S = 60.0
INELIGIBLE_S = 60.0
EXPLORE_REQUESTS = 2
# Across-call cooldown: a candidate whose most recent call (any task, any
# split) was a rate-limit / budget / no-hop refusal is skipped for this long
# when another eligible candidate exists. Not a score and not training data.
COOLDOWN_ENV = "CORTEX_FREEROUTE_COOLDOWN_S"
COOLDOWN_S = 30.0
COOLDOWN_STATUSES = frozenset({429, 402, 503})
SCORE_WINDOW = 200
# Cortex #270 ROUTER-2: opt-in cost-ordered ladder. Unset keeps one call per
# complete(). The value is how many step-ups one call may take.
LADDER_ENV = "CORTEX_FREEROUTE_LADDER"
LADDER_DEFAULT_STEPS = 2
# A measured model below this Laplace validity score is not a ladder rung.
LADDER_VALIDITY_BAR = 0.5
RUNG_DETERMINISTIC = "deterministic"
RUNG_CLASSIC_ML = "classic_ml"
RUNG_LOCAL = "local"
RUNG_CLOUD = "cloud"
SOLVER_IMPL = "cortex-solver"
MAX_CANDIDATES = 6
PER_PROVIDER = 2

_LOOPBACK_IDENTITIES = frozenset({"", "local", "127.0.0.1", "::1", "localhost", "testclient"})

GOOD_VERDICTS = frozenset({"gate_pass", "plausible", "static_valid"})
BAD_VERDICTS = frozenset({"gate_fail", "implausible", "static_fail"})
_FINAL_VERDICTS = frozenset({"plausible", "implausible"})

_STATUS_REASONS: dict[int, str] = {
    0: "OpenVault unreachable",
    400: "OpenVault non-retryable upstream error (HTTP 400)",
    401: "OpenVault rejected the credential (HTTP 401)",
    402: "FreeRoute pack budget exhausted (HTTP 402)",
    403: "OpenVault refused (HTTP 403)",
    429: "FreeRoute token budget exceeded (HTTP 429)",
    502: "FreeRoute fallback exhausted (HTTP 502)",
    503: "FreeRoute has no candidate hop (HTTP 503)",
}

_REDACT = re.compile(
    r"(ov_[A-Za-z0-9_\-]+|sk-[A-Za-z0-9_\-]{8,}|gsk_[A-Za-z0-9_\-]+"
    # Partial provider keys (NVIDIA nvapi-, Google AIza, Cerebras csk-) echoed
    # back in an env-direct error are shorter than the catch-all below.
    # Masked forms (``nvapi-****abcd``) count too, so the tail is any non-space.
    r"|\b(?:nvapi-|AIza|csk-)[^\s\"',;)]*"
    r"|[A-Za-z0-9_\-]{32,})"
)

_lock = threading.Lock()
_arming_cache: dict[tuple[str, str], tuple[float, Arming]] = {}
_vault_cache: dict[str, tuple[float, _Vault]] = {}
_rejected: dict[tuple[str, str], tuple[float, str]] = {}
_verified: dict[tuple[str, str], str] = {}
_last_arming: dict[tuple[str, str], Arming] = {}
_store_error: dict[str, str] = {}

_journal_var: ContextVar[list[RouteStamp] | None] = ContextVar("freeroute_journal", default=None)
_shadow_var: ContextVar[bool] = ContextVar("freeroute_shadow", default=False)
_split_var: ContextVar[str] = ContextVar("freeroute_split", default="product")
_transport_var: ContextVar[tuple[Callable[..., Any], str] | None] = ContextVar(
    "freeroute_transport", default=None
)

SPLIT_TRAIN = "train"
SPLIT_HELDOUT = "heldout"
SPLIT_PRODUCT = "product"
SPLIT_BENCHMARK = "benchmark"
SPLITS = frozenset({SPLIT_TRAIN, SPLIT_HELDOUT, SPLIT_PRODUCT, SPLIT_BENCHMARK})
NON_LEARNING_SPLITS = frozenset({SPLIT_HELDOUT, SPLIT_BENCHMARK})
# pick() reads through _stats/_rows. Shadow, held-out, and benchmark rows
# never train. Do not drop this filter to make a query pass.
_LEARNING_FILTER_SQL = (
    "shadow = 0 AND IFNULL(split, 'product') NOT IN ('heldout', 'benchmark')"
)
# Cortex #268: every call is PII-masked before the transport (pii_mask); there
# is no switch that turns it off.
MASKING_STATE = "on"
SERVED_PENDING_272 = (
    "served_provider/served_model/served_local wait for Cortex #272 LOCAL-1; "
    "not inferred from requested model or OpenVault chat model name"
)


# -- redaction and credential ---------------------------------------------------


def redact(text: object, *, limit: int = 200) -> str:
    """Token shapes out, whitespace collapsed, bounded. For any OpenVault/upstream text."""
    raw = " ".join(str(text or "").split())
    return _REDACT.sub("<redacted>", raw)[:limit]


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12] if value else ""


def _token() -> str:
    return (os.environ.get(TOKEN_ENV) or "").strip()


def child_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy of ``env`` without the Cortex OpenVault key or any env-direct provider key.

    For any subprocess: a child never inherits a credential this layer spends.
    """
    out = dict(os.environ if env is None else env)
    out.pop(TOKEN_ENV, None)
    for name in direct_providers.KEY_ENVS:
        out.pop(name, None)
    return out


def auth_headers(bearer: str | None = None) -> dict[str, str]:
    """``bearer=None``: Cortex's own key. ``""``: send nothing. Otherwise relay it."""
    value = _token() if bearer is None else bearer.strip()
    return {"Authorization": f"Bearer {value}"} if value else {}


def _credential_fp(bearer: str | None) -> str:
    return fingerprint(_token() if bearer is None else bearer.strip())


def identity() -> dict[str, Any]:
    """Public view of the Cortex credential. Never the token."""
    token = _token()
    url = openvault_client.openvault_base_url()
    fp = fingerprint(token)
    key_id = _verified.get((url, fp), "")
    return {
        "mode": "api_key" if token else "loopback",
        "env": TOKEN_ENV,
        "fingerprint": fp,
        "key_id": key_id,
        "verified_by": "openvault" if key_id else ("" if token else "socket peer (OpenVault decides)"),
        "attributed": bool(key_id),
        "authority": False,
        "issue_with": "OpenVault POST /api/apikeys (not the Cortex key screen)",
    }


# -- arming ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Vault:
    ok: bool
    reason: str
    sealed: bool | None = None
    pooled_keys: int | None = None
    hops: tuple[dict[str, Any], ...] = ()
    catalogue: tuple[tuple[str, tuple[str, ...]], ...] = ()
    local_spendable_hops: int = 0
    local_reason: str = ""


@dataclass(frozen=True)
class Arming:
    armed: bool
    reason: str
    url: str
    sealed: bool | None = None
    pooled_keys: int | None = None
    spendable_hops: int = 0
    hops_public: tuple[dict[str, Any], ...] = ()
    catalogue: tuple[tuple[str, tuple[str, ...]], ...] = ()
    checked_at: float = 0.0
    probed: bool = True
    local_spendable_hops: int = 0
    local_reason: str = ""
    custody: str = "openvault"

    def public(self) -> dict[str, Any]:
        return {
            "armed": self.armed,
            "reason": self.reason,
            "url": self.url,
            "sealed": self.sealed,
            "pooled_keys": self.pooled_keys,
            "spendable_hops": self.spendable_hops,
            "hops": [dict(h) for h in self.hops_public],
            "probed": self.probed,
            "custody": self.custody,
            "local_only": local_only_enabled(),
            "local_reason": self.local_reason,
        }


def unarmed(reason: str, *, url: str | None = None, probed: bool = True) -> Arming:
    return Arming(
        armed=False,
        reason=reason,
        url=url or openvault_client.openvault_base_url(),
        checked_at=time.time(),
        probed=probed,
    )


def local_only_enabled() -> bool:
    """True only for ``CORTEX_FREEROUTE_LOCAL_ONLY=1``. Fail-closed; no cloud fallback."""
    return (os.environ.get(LOCAL_ONLY_ENV) or "").strip() == "1"


def _hop_parked(hop: Mapping[str, Any]) -> bool:
    return str(hop.get("circuit") or "").lower() == "open" or bool(hop.get("park_until"))


def _read_vault(url: str, timeout: float) -> _Vault:
    status, body = openvault_client.request_json(
        "GET", "/api/freeroute/status", timeout=timeout, base=url
    )
    if status == 0:
        return _Vault(False, f"OpenVault unreachable at {url}")
    if status != 200 or not isinstance(body, dict):
        return _Vault(False, f"OpenVault /api/freeroute/status answered HTTP {status}")
    sealed = body.get("sealed")
    if not isinstance(sealed, bool):
        return _Vault(False, "OpenVault did not report seal state; FreeRoute cannot be proven armed")
    if sealed:
        return _Vault(
            False,
            "OpenVault vault is sealed; unseal it in OpenVault (Cortex never holds the passphrase)",
            sealed=True,
        )
    catalogue: dict[str, tuple[str, ...]] = {}
    raw_specs: list[dict[str, Any]] = []
    for spec in body.get("spendable") or []:
        if not isinstance(spec, dict):
            continue
        raw_specs.append(spec)
        pid = str(spec.get("id") or "").strip()
        models = tuple(str(m) for m in (spec.get("chat_models") or []) if str(m).strip())
        if pid and pid != "cortex" and models:
            catalogue[pid] = models
    raw_hops: list[dict[str, Any]] = []
    hops: list[dict[str, Any]] = []
    for hop in body.get("hops") or []:
        if not isinstance(hop, dict):
            continue
        raw_hops.append(hop)
        hops.append(
            {
                "provider": str(hop.get("provider") or ""),
                "priority": hop.get("priority"),
                "circuit": str(hop.get("circuit") or ""),
                "parked": _hop_parked(hop),
            }
        )
    spendable = [h for h in hops if h["provider"] in catalogue]
    local_n = freeroute_ov_local.count_local_spendable(raw_hops, raw_specs, set(catalogue))
    local_reason = freeroute_ov_local.local_arming_reason(body)
    pooled = body.get("pooled_key_count")
    pooled_keys = pooled if isinstance(pooled, int) and not isinstance(pooled, bool) else None
    pooled_ok = pooled_keys is not None and pooled_keys > 0
    if not pooled_ok and local_n <= 0:
        return _Vault(
            False,
            "OpenVault pools no keys for FreeRoute; add keys in OpenVault",
            sealed=False,
            pooled_keys=pooled_keys,
            local_reason=local_reason,
        )
    if not spendable and local_n <= 0:
        return _Vault(
            False,
            "OpenVault has no spendable FreeRoute hop (pooled rows are not provider keys)",
            sealed=False,
            pooled_keys=pooled_keys,
            hops=tuple(hops),
            local_reason=local_reason,
        )
    return _Vault(
        True,
        "",
        sealed=False,
        pooled_keys=pooled_keys,
        hops=tuple(hops),
        catalogue=tuple(sorted(catalogue.items())),
        local_spendable_hops=local_n,
        local_reason=local_reason,
    )


def _vault(url: str, *, fresh: bool, timeout: float) -> _Vault:
    now = time.monotonic()
    with _lock:
        hit = _vault_cache.get(url)
    if hit and not fresh and now - hit[0] < ARMING_TTL_S:
        return hit[1]
    got = _read_vault(url, timeout)
    with _lock:
        _vault_cache[url] = (now, got)
    return got


def _rejection(url: str, fp: str) -> str:
    with _lock:
        hit = _rejected.get((url, fp))
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    return ""


def note_rejected(credential_fp: str, reason: str, *, url: str | None = None) -> None:
    """Remember a credential OpenVault refused, keyed by the credential actually sent."""
    root = url or openvault_client.openvault_base_url()
    with _lock:
        _rejected[(root, credential_fp)] = (time.monotonic() + REJECT_TTL_S, redact(reason, limit=300))
        _arming_cache.pop((root, credential_fp), None)
        _verified.pop((root, credential_fp), None)


def _switched_off() -> str:
    """The operator kill switch. It stops the model layer whatever the transport."""
    if (os.environ.get(SWITCH_ENV) or "1").strip() == "0":
        return f"{SWITCH_ENV}=0: FreeRoute disabled by the operator (no fallback)"
    return ""


def _precheck(url: str, token: str, *, relay: bool) -> str:
    """Offline refusals in rule order. '' when nothing is wrong yet."""
    off = _switched_off()
    if off:
        return off
    conflict = openvault_client.openvault_base_url_conflict()
    if conflict:
        return conflict
    name = "the caller bearer" if relay else TOKEN_ENV
    if token and not (token.startswith("ov_") and len(token) >= 12):
        return f"{name} is not an OpenVault ov_ key; provider keys belong in OpenVault"
    if not openvault_client.is_loopback_url(url):
        if not token:
            return f"remote OpenVault at {url} needs an ov_ key ({name})"
        if not url.lower().startswith("https://"):
            return f"remote OpenVault at {url} needs https before an ov_ key is sent"
    return ""


def _verify_token(url: str, token: str, timeout: float) -> str:
    """'' when OpenVault attributes the key; else the named refusal (cached 60s)."""
    fp = fingerprint(token)
    status, body = openvault_client.request_json(
        "GET",
        "/api/freeroute/ratelimit",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
        base=url,
    )
    if status == 0:
        return f"OpenVault unreachable at {url}"
    who = str((body or {}).get("identity") or "") if isinstance(body, dict) else ""
    if status in (401, 403) or (status == 200 and who in _LOOPBACK_IDENTITIES):
        seen = f"HTTP {status}" if status != 200 else f"identity '{who or 'none'}'"
        reason = (
            f"OpenVault did not accept {TOKEN_ENV} (resolved to {seen}); "
            "issue it with OpenVault POST /api/apikeys"
        )
        note_rejected(fp, reason, url=url)
        return reason
    if status != 200:
        return f"OpenVault did not verify {TOKEN_ENV} (HTTP {status})"
    with _lock:
        _verified[(url, fp)] = who
    return ""


def _armed_from(vault: _Vault, url: str, mode: str) -> Arming:
    spendable = [h for h in vault.hops if h["provider"] in dict(vault.catalogue)]
    live = [h for h in spendable if not h["parked"]]
    reason = (
        f"armed: {vault.pooled_keys} pooled keys, {len(spendable)} spendable hops at {url} as {mode}"
    )
    if not live:
        reason += "; every spendable hop is circuit-open or parked (OpenVault half-opens)"
    return Arming(
        armed=True,
        reason=reason,
        url=url,
        sealed=False,
        pooled_keys=vault.pooled_keys,
        spendable_hops=len(spendable),
        hops_public=vault.hops,
        catalogue=vault.catalogue,
        checked_at=time.time(),
        local_spendable_hops=vault.local_spendable_hops,
        local_reason=vault.local_reason,
    )


def _vault_refusal(vault: _Vault, url: str) -> Arming:
    return Arming(
        armed=False,
        reason=vault.reason,
        url=url,
        sealed=vault.sealed,
        pooled_keys=vault.pooled_keys,
        spendable_hops=0,
        hops_public=vault.hops,
        checked_at=time.time(),
        local_spendable_hops=vault.local_spendable_hops,
        local_reason=vault.local_reason,
    )


def _direct_arming(*, relay: bool = False) -> Arming:
    """Env-direct opt-in: armed from provider keys in the process env. No OpenVault.

    ``relay``: the caller is relayed over HTTP. The operator's env keys serve
    in-process callers only, so that is a named refusal.
    """
    found = direct_providers.configured()
    off = _switched_off()
    if off or relay or not found:
        envs = ", ".join(n for p in direct_providers.PROVIDERS for n in p.key_envs)
        return Arming(
            armed=False,
            reason=off
            or (direct_providers.RELAY_REFUSED if relay else "")
            or f"{direct_providers.TRANSPORT_ENV}=env-direct but no provider key is set ({envs})",
            url=direct_providers.URL,
            checked_at=time.time(),
            custody=direct_providers.CUSTODY,
        )
    hops = tuple(
        {"provider": p.label, "parked": False, "key_env": name, "served_local": False}
        for p, name, _ in found
    )
    catalogue = tuple((p.label, tuple(f"{p.label}:{m}" for m in models)) for p, _, models in found)
    labels = ", ".join(f"{p.label} via {name}" for p, name, _ in found)
    return Arming(
        armed=True,
        reason=f"armed env-direct (NOT OpenVault): {labels}",
        url=direct_providers.URL,
        spendable_hops=len(hops),
        hops_public=hops,
        catalogue=catalogue,
        checked_at=time.time(),
        custody=direct_providers.CUSTODY,
        local_reason=direct_providers.CLOUD_ONLY,
    )


def arming(*, fresh: bool = False, timeout: float = 1.5, bearer: str | None = None) -> Arming:
    """Is FreeRoute spendable for this credential? First failing rule wins, named."""
    if direct_providers.enabled():
        return _direct_arming(relay=bearer is not None)
    url = openvault_client.openvault_base_url()
    relay = bearer is not None
    token = _token() if bearer is None else bearer.strip()
    fp = fingerprint(token)
    key = (url, fp)
    now = time.monotonic()
    with _lock:
        hit = _arming_cache.get(key)
    if hit and not fresh and now - hit[0] < ARMING_TTL_S:
        return hit[1]

    refused = _precheck(url, token, relay=relay) or _rejection(url, fp)
    if refused:
        result = unarmed(refused, url=url)
    else:
        vault = _vault(url, fresh=fresh, timeout=timeout)
        if not vault.ok:
            result = _vault_refusal(vault, url)
        elif token and not relay:
            bad = _verify_token(url, token, timeout)
            result = _vault_refusal(_Vault(False, bad), url) if bad else _armed_from(
                vault, url, "api_key"
            )
        else:
            result = _armed_from(vault, url, "relayed bearer" if token else "loopback tier")
    with _lock:
        _arming_cache[key] = (now, result)
        _last_arming[key] = result
    return result


def peek() -> Arming:
    """Last arming for the Cortex credential. No network.

    Under env-direct this is the arming :func:`complete` would use for an
    in-process caller (latched mode, kill switch, keys), never OpenVault custody.
    """
    if direct_providers.enabled():
        return _direct_arming()
    key = (openvault_client.openvault_base_url(), fingerprint(_token()))
    with _lock:
        got = _last_arming.get(key)
    return got or unarmed("arming not probed yet", url=key[0], probed=False)


def leave_gate() -> tuple[bool, str]:
    """OpenVault leave-machine gate for payloads that carry schema off the box."""
    if direct_providers.enabled():
        # The operator's explicit opt-in is the leave decision in this mode.
        return True, (
            f"ok:leave by operator opt-in {direct_providers.TRANSPORT_ENV}=env-direct "
            "(OpenVault gate not consulted)"
        )
    try:
        from CortexOS.integrations import openvault_gate

        gate = openvault_gate.check_gate(
            action="leave", destination="freeroute", required_providers=[]
        )
    except Exception as exc:  # noqa: BLE001 - a broken gate client is a refusal
        return False, redact(f"gate error: {exc}", limit=240)
    if gate.get("allowed") is True:
        return True, "ok:leave"
    reasons = gate.get("reasons") or ["leave-machine gate denied"]
    return False, redact("; ".join(str(r) for r in reasons), limit=240)


# -- route store ----------------------------------------------------------------


def store_path() -> Path:
    override = (os.environ.get(STORE_ENV) or "").strip()
    if override:
        return Path(override)
    from CortexOS.paths import data_path

    return data_path("engine", "freeroute_routes.db")


def _learning() -> bool:
    # Default stays 1 when unset (founder decision; not this slice).
    return (os.environ.get(LEARN_ENV) or "1").strip() != "0"


def learn_state() -> dict[str, Any]:
    """Effective CORTEX_FREEROUTE_LEARN plus whether it came from env or default."""
    if LEARN_ENV not in os.environ:
        return {"learn_enabled": _learning(), "learn_source": "default"}
    return {"learn_enabled": _learning(), "learn_source": "env"}


def _normalize_split(raw: str | None) -> str:
    val = (raw or "").strip().lower()
    return val if val in SPLITS else SPLIT_PRODUCT


_SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    call_id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    requested TEXT NOT NULL,
    served TEXT NOT NULL,
    status INTEGER NOT NULL,
    usable INTEGER NOT NULL,
    scored INTEGER NOT NULL,
    verdict TEXT,
    latency_ms REAL NOT NULL,
    impl TEXT NOT NULL,
    shadow INTEGER NOT NULL,
    ts REAL NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    split TEXT NOT NULL DEFAULT 'product'
);
CREATE INDEX IF NOT EXISTS routes_task_requested ON routes(task, requested, ts);
CREATE INDEX IF NOT EXISTS routes_task_served ON routes(task, served, ts);
"""

_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("prompt_tokens", "INTEGER"),
    ("completion_tokens", "INTEGER"),
    ("total_tokens", "INTEGER"),
    ("split", "TEXT NOT NULL DEFAULT 'product'"),
    # #271: the pre-spend prediction a call went out under (tuning is train-only).
    ("prespend_p", "REAL"),
    ("prespend_mode", "TEXT"),
)


def _apply_schema(con: sqlite3.Connection) -> None:
    """Create-if-missing, then ALTER ADD only. Never DROP / rename / retype."""
    con.executescript(_SCHEMA)
    existing = {row[1] for row in con.execute("PRAGMA table_info(routes)")}
    for name, decl in _ADDITIVE_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE routes ADD COLUMN {name} {decl}")


def _note_store_error(exc: BaseException) -> None:
    with _lock:
        _store_error["error"] = redact(f"route store unavailable: {type(exc).__name__}: {exc}")


def store_error() -> str:
    with _lock:
        return _store_error.get("error", "")


_store_lock = threading.RLock()
_initialized: set[str] = set()


def _init_file(path: Path) -> None:
    """Schema + WAL + additive columns once per path."""
    key = str(path)
    if key in _initialized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(key, timeout=5.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        _apply_schema(con)
        con.commit()
    finally:
        con.close()
    _initialized.add(key)


def _open_for_write(path: Path) -> sqlite3.Connection:
    """Schema and WAL once per path (both need an exclusive lock), then plain inserts."""
    _init_file(path)
    return sqlite3.connect(str(path), timeout=5.0)


@contextmanager
def _connect(*, write: bool) -> Iterator[sqlite3.Connection | None]:
    path = store_path()
    con: sqlite3.Connection | None = None
    held = False
    try:
        if write:
            _store_lock.acquire()
            held = True
            con = _open_for_write(path)
        else:
            if not path.is_file():
                yield None
                return
            with _store_lock:
                _init_file(path)
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        yield con
        if write:
            con.commit()
    except (sqlite3.Error, OSError) as exc:
        _note_store_error(exc)
        yield None
    finally:
        if con is not None:
            con.close()
        if held:
            _store_lock.release()


def _usage_tokens(
    usage: Mapping[str, Any] | None,
) -> tuple[int | None, int | None, int | None]:
    """Prompt / completion / total from the provider usage object. Not USD."""

    def _int(key: str) -> int | None:
        if not usage:
            return None
        val = usage.get(key)
        if isinstance(val, bool) or not isinstance(val, int | float):
            return None
        return int(val)

    prompt = _int("prompt_tokens")
    completion = _int("completion_tokens")
    total = _int("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = int(prompt or 0) + int(completion or 0)
    return prompt, completion, total


def _write_row(
    stamp: RouteStamp,
    *,
    scored: bool,
    usage: Mapping[str, Any] | None = None,
    split: str = SPLIT_PRODUCT,
) -> None:
    if not _learning():
        return
    prompt_tokens, completion_tokens, total_tokens = _usage_tokens(usage)
    with _connect(write=True) as con:
        if con is None:
            return
        con.execute(
            "INSERT OR REPLACE INTO routes ("
            "call_id, task, requested, served, status, usable, scored, verdict, "
            "latency_ms, impl, shadow, ts, prompt_tokens, completion_tokens, "
            "total_tokens, split, prespend_p, prespend_mode"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                stamp.call_id,
                stamp.task,
                stamp.requested,
                stamp.served,
                int(stamp.status),
                1 if stamp.usable else 0,
                1 if scored else 0,
                None,
                float(stamp.latency_ms),
                stamp.impl,
                1 if _shadow_var.get() else 0,
                time.time(),
                prompt_tokens,
                completion_tokens,
                total_tokens,
                _normalize_split(split),
                stamp.prespend.get("p_valid"),
                stamp.prespend.get("mode"),
            ),
        )


def _rows(task: str) -> list[sqlite3.Row]:
    """Learning rows only. Shadow / held-out / benchmark never train pick()."""
    with _connect(write=False) as con:
        if con is None:
            return []
        con.row_factory = sqlite3.Row
        try:
            return list(
                con.execute(
                    "SELECT * FROM routes WHERE task = ? AND "
                    + _LEARNING_FILTER_SQL
                    + " ORDER BY ts DESC LIMIT ?",
                    (task, SCORE_WINDOW * MAX_CANDIDATES),
                )
            )
        except sqlite3.Error as exc:
            _note_store_error(exc)
            return []


def _validity(row: Mapping[str, Any]) -> float:
    verdict = row["verdict"]
    if verdict in GOOD_VERDICTS:
        return 1.0
    if verdict in BAD_VERDICTS or not row["usable"]:
        return 0.0
    return 0.5


def _same_model(requested: str, served: str) -> bool:
    if not requested or not served:
        return False
    return requested == served or requested.rsplit("/", 1)[-1] == served.rsplit("/", 1)[-1]


@dataclass
class ModelStats:
    requested: str
    requests: int = 0
    scored: int = 0
    honored_rate: float | None = None
    served_most: str = ""
    score: float | None = None
    scored_n: int = 0
    mean_latency_ms: float = 0.0
    mean_cost: float | None = None
    ineligible: str = ""
    cooling: str = ""
    cooling_until: float = 0.0


def _cooldown_s() -> float:
    raw = (os.environ.get(COOLDOWN_ENV) or "").strip()
    if not raw:
        return COOLDOWN_S
    try:
        value = float(raw)
    except ValueError:
        return COOLDOWN_S
    return value if value >= 0.0 else COOLDOWN_S


def _cool_key(model: str) -> str:
    """env-direct refusals are per provider (one key, one quota); else per model."""
    provider, _ = direct_providers.split_id(model)
    return f"provider:{provider.label}" if provider is not None else model


def _cooling(models: list[str], now: float) -> dict[str, tuple[float, str]]:
    """Most recent call per cool key across every task and split.

    Read-only fact about the provider right now (was it just refused?), never a
    validity score, so the learning filter does not apply: a 429 met during a
    benchmark or shadow call is still a 429. Retry-After is not stored, so the
    window is ``COOLDOWN_S`` (``CORTEX_FREEROUTE_COOLDOWN_S``).
    """
    window = _cooldown_s()
    if window <= 0.0 or not models:
        return {}
    wanted = {_cool_key(m) for m in models}
    latest: dict[str, tuple[float, int]] = {}
    with _connect(write=False) as con:
        if con is None:
            return {}
        try:
            rows = list(
                con.execute(
                    "SELECT requested, status, ts FROM routes WHERE ts >= ? ORDER BY ts DESC",
                    (now - window,),
                )
            )
        except sqlite3.Error as exc:
            _note_store_error(exc)
            return {}
    for requested, status, ts in rows:
        key = _cool_key(str(requested or ""))
        if key in wanted and key not in latest:
            latest[key] = (float(ts), int(status))
    out: dict[str, tuple[float, str]] = {}
    for model in models:
        hit = latest.get(_cool_key(model))
        if hit is None or hit[1] not in COOLDOWN_STATUSES:
            continue
        until = hit[0] + window
        if until > now:
            out[model] = (until, f"HTTP {hit[1]} {round(now - hit[0], 1)}s ago")
    return out


def _row_cost(row: Mapping[str, Any]) -> float | None:
    """Token cost from stored provider usage. Not USD; never invented."""
    keys = row.keys() if hasattr(row, "keys") else ()
    total = row["total_tokens"] if "total_tokens" in keys else None
    if total is not None:
        try:
            return float(total)
        except (TypeError, ValueError):
            return None
    prompt = row["prompt_tokens"] if "prompt_tokens" in keys else None
    completion = row["completion_tokens"] if "completion_tokens" in keys else None
    if prompt is None and completion is None:
        return None
    return float(prompt or 0) + float(completion or 0)


def _stats(task: str, models: list[str]) -> dict[str, ModelStats]:
    rows = _rows(task)
    by_served: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if row["scored"] and row["served"]:
            by_served.setdefault(row["served"], []).append(row)
    out: dict[str, ModelStats] = {}
    now = time.time()
    for model in models:
        mine = [r for r in rows if r["requested"] == model]
        st = ModelStats(requested=model, requests=len(mine))
        answered = [r for r in mine if r["status"] == 200 and r["served"]]
        if answered:
            honored = sum(1 for r in answered if _same_model(model, r["served"]))
            st.honored_rate = round(honored / len(answered), 3)
            counts: dict[str, int] = {}
            for r in answered:
                counts[r["served"]] = counts.get(r["served"], 0) + 1
            st.served_most = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        scored = [r for r in mine if r["scored"]]
        st.scored = len(scored)
        if scored:
            st.mean_latency_ms = round(sum(float(r["latency_ms"]) for r in scored) / len(scored), 1)
            costs = []
            for row in scored:
                cost = _row_cost(row)
                if cost is not None:
                    costs.append(cost)
            if costs:
                st.mean_cost = round(sum(costs) / len(costs), 1)
        # Validity belongs to the model that answered. OpenVault may serve a
        # different one than was asked for, and two requested ids can share a
        # served model, so scoring by request would grade the wrong subject.
        served_key = st.served_most or model
        window = (by_served.get(served_key) or [])[:SCORE_WINDOW]
        if window and st.scored:
            total = sum(_validity(r) for r in window)
            st.score = round((total + 1.0) / (len(window) + 2.0), 4)
            st.scored_n = len(window)
        last3 = mine[:3]
        if (
            len(last3) == 3
            and all(not r["scored"] for r in last3)
            and now - float(last3[0]["ts"]) < INELIGIBLE_S
        ):
            st.ineligible = f"last 3 calls refused (HTTP {last3[0]['status']})"
        out[model] = st
    for model, (until, why) in _cooling(models, now).items():
        out[model].cooling = why
        out[model].cooling_until = until
    return out


# -- candidates and pick --------------------------------------------------------


@dataclass(frozen=True)
class Pick:
    requested: str
    reason: str
    source: str
    candidates: tuple[str, ...]
    measured_n: int = 0
    measured_score: float | None = None


def candidates(arm: Arming, *, pin: str = "", pin_source: str = "") -> tuple[tuple[str, ...], str]:
    pinned = (pin or "").strip()
    if pinned:
        return (pinned,), f"operator pin {pin_source or 'pin'}"
    bundle = [m.strip() for m in (os.environ.get(MODELS_ENV) or "").split(",") if m.strip()]
    if bundle:
        return tuple(dict.fromkeys(bundle))[:MAX_CANDIDATES], f"operator bundle {MODELS_ENV}"
    catalogue = dict(arm.catalogue)
    found: list[str] = []
    for hop in arm.hops_public:
        if hop.get("parked"):
            continue
        for model in catalogue.get(str(hop.get("provider") or ""), ())[:PER_PROVIDER]:
            if model not in found:
                found.append(model)
    if found:
        where = "OpenVault" if arm.custody == "openvault" else "env-direct"
        return tuple(found[:MAX_CANDIDATES]), f"live hops x {where} catalogue"
    return ("auto",), "delegated to OpenVault (not measured by Cortex)"


def pick(task: str, arm: Arming, *, pin: str = "", pin_source: str = "") -> Pick:
    models, source = candidates(arm, pin=pin, pin_source=pin_source)
    if len(models) == 1:
        st = _stats(task, list(models))[models[0]]
        return Pick(models[0], source, source, models, st.scored_n, st.score)
    stats = _stats(task, list(models))
    eligible = [m for m in models if not stats[m].ineligible]
    if not eligible:
        first = models[0]
        return Pick(
            first,
            f"every candidate refused recently ({stats[first].ineligible}); trying {first}",
            source,
            models,
        )
    warm = [m for m in eligible if not stats[m].cooling]
    cooling = [m for m in eligible if stats[m].cooling]
    cool_note = (
        "; cooling (skipped): "
        + ", ".join(f"{m} ({stats[m].cooling})" for m in cooling)
        if cooling
        else ""
    )
    if not warm:
        now = time.time()
        order_c = {m: i for i, m in enumerate(models)}
        soonest = min(cooling, key=lambda m: (stats[m].cooling_until, order_c[m]))
        st = stats[soonest]
        left = max(0.0, st.cooling_until - now)
        return Pick(
            soonest,
            f"every eligible candidate cooling after a refusal; trying {soonest}, "
            f"whose cooldown ends first ({st.cooling}, {left:.1f}s left)",
            source,
            models,
            st.scored_n,
            st.score,
        )
    eligible = warm
    for model in eligible:
        st = stats[model]
        if st.requests < EXPLORE_REQUESTS:
            skipped = [m for m in models if stats[m].ineligible]
            note = f"; ineligible: {', '.join(skipped)}" if skipped else ""
            return Pick(
                model,
                f"exploring {model} ({st.requests}/{EXPLORE_REQUESTS} requests) among {len(models)}{note}{cool_note}",
                source,
                models,
                st.scored_n,
                st.score,
            )
    ranked = [m for m in eligible if stats[m].score is not None]
    if not ranked:
        return Pick(
            eligible[0], f"no scored route yet; trying {eligible[0]}{cool_note}", source, models
        )
    order = {m: i for i, m in enumerate(models)}
    best = min(
        ranked,
        key=lambda m: (-(stats[m].score or 0.0), stats[m].mean_latency_ms, order[m]),
    )
    st = stats[best]
    reason = f"measured best validity {st.score} over n={st.scored_n} among {len(ranked)} scored"
    if st.served_most and not _same_model(best, st.served_most) and (st.honored_rate or 0.0) < 0.5:
        reason += f"; not honored (served {st.served_most})"
    reason += cool_note
    return Pick(best, reason, source, models, st.scored_n, st.score)


# -- stamps, journal, transport -------------------------------------------------


@dataclass
class RouteStamp:
    call_id: str
    task: str
    requested: str
    served: str = ""
    honored: bool = False
    pick_reason: str = ""
    source: str = ""
    candidates: tuple[str, ...] = ()
    status: int | None = None
    usable: bool = False
    latency_ms: float = 0.0
    error: str = ""
    impl: str = IMPL
    credential: str = ""
    # The leave-machine decision this call went out under ("" = not a leave call).
    leave_gate: str = ""
    measured_n: int = 0
    measured_score: float | None = None
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False
    served_reason: str = ""
    # #270: which rung this call was ("" = not a ladder call) and, on the stamp
    # a ladder call returns, every rung tried in order.
    rung: str = ""
    ladder: list[dict[str, Any]] = field(default_factory=list)
    # #271: the pre-spend prediction this call went out (or was skipped) under.
    prespend: dict[str, Any] = field(default_factory=dict)
    # Cortex #268: what was masked before sending, counts by kind only.
    masked: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        """Customer-safe: what was asked and what served. Never counts or scores."""
        prefix = "" if self.impl == IMPL else f"NOT OpenVault FreeRoute ({self.impl}): "
        if self.impl == SOLVER_IMPL:
            text = f"FreeRoute {self.task}: {self.rung} solver {self.requested} (no model call)"
        elif self.status is None:
            text = f"FreeRoute {self.task}: not sent ({self.error})"
        else:
            text = f"FreeRoute {self.task}: asked {self.requested}, served {self.served or 'none'} ({self.source})"
        if self.ladder:
            text += f" [ladder: {len(self.ladder)} rung(s) tried]"
        err = store_error()
        if err:
            text += f" [{err}]"
        return prefix + text

    def public(self) -> dict[str, Any]:
        body = asdict(self)
        body["candidates"] = list(self.candidates)
        body["line"] = self.line()
        return body


@dataclass
class Completion:
    ok: bool
    text: str = ""
    message: dict[str, Any] = field(default_factory=dict)
    stamp: RouteStamp | None = None
    reason: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@contextmanager
def journal(*, shadow: bool = False, split: str | None = None) -> Iterator[list[RouteStamp]]:
    """Collect every stamp written inside this block (threads via copy_context)."""
    stamps: list[RouteStamp] = []
    token = _journal_var.set(stamps)
    shadow_token = _shadow_var.set(shadow)
    split_token = _split_var.set(_normalize_split(split))
    try:
        yield stamps
    finally:
        _split_var.reset(split_token)
        _shadow_var.reset(shadow_token)
        _journal_var.reset(token)


@contextmanager
def use_transport(fn: Callable[..., Any], impl: str) -> Iterator[None]:
    """Replace the OpenVault transport (replay cassettes). Stamps say NOT OpenVault."""
    token = _transport_var.set((fn, impl))
    try:
        yield
    finally:
        _transport_var.reset(token)


def _journal_add(stamp: RouteStamp) -> None:
    active = _journal_var.get()
    if active is not None:
        active.append(stamp)


def note_verdict(target: RouteStamp | str | list[RouteStamp] | None, verdict: str) -> None:
    """Credit a validator verdict to a call. No-op for unknown ids or when not learning."""
    if not target or not _learning():
        return
    if verdict not in GOOD_VERDICTS and verdict not in BAD_VERDICTS:
        return
    if isinstance(target, list):
        ids = [s.call_id for s in target if s.status is not None]
    elif isinstance(target, RouteStamp):
        ids = [target.call_id] if target.status is not None else []
    else:
        ids = [str(target)]
    if not ids:
        return
    with _connect(write=True) as con:
        if con is None:
            return
        for call_id in ids:
            if verdict in _FINAL_VERDICTS:
                con.execute("UPDATE routes SET verdict = ? WHERE call_id = ?", (verdict, call_id))
            else:
                con.execute(
                    "UPDATE routes SET verdict = ? WHERE call_id = ? "
                    "AND (verdict IS NULL OR verdict NOT IN ('plausible','implausible'))",
                    (verdict, call_id),
                )


def last_line(stamps: list[RouteStamp] | None) -> str:
    return stamps[-1].line() if stamps else ""


def _transport() -> tuple[Callable[..., Any], str]:
    override = _transport_var.get()
    if override is not None:
        return override
    if direct_providers.enabled():
        return direct_providers.request_json, direct_providers.IMPL
    return openvault_client.request_json, IMPL


def _error_message(body: Any) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return redact(err.get("message") or err.get("type") or "")
        if isinstance(err, str):
            return redact(err)
        if body.get("detail"):
            return redact(body.get("detail"))
    return ""


# -- ladder (Cortex #270 ROUTER-2) ----------------------------------------------


class Solver(Protocol):
    """A no-model rung. ``kind`` is :data:`RUNG_DETERMINISTIC` or :data:`RUNG_CLASSIC_ML`.

    ``solve`` returns a candidate answer or ``None`` (abstain). The candidate is
    checked by the same ``accept`` a model answer is; it is never trusted
    unchecked. A classic-ML solver also needs a truthy ``heldout`` mapping (its
    held-out numbers) or the rung stays disabled.
    """

    name: str
    kind: str

    def solve(self, task: str, messages: list[dict[str, Any]]) -> str | None: ...


def _ladder_steps(ladder: int | None) -> tuple[int, str]:
    """Step-ups allowed for this call, plus a note when the env value was ignored."""
    if ladder is not None:
        return max(0, int(ladder)), ""
    raw = (os.environ.get(LADDER_ENV) or "").strip()
    if not raw:
        return 0, ""
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        return 0, f"{LADDER_ENV}={raw[:16]!r} ignored (not a step count >= 0); no ladder"
    return value, ""


def ladder_order(
    task: str, arm: Arming, *, pin: str = "", pin_source: str = ""
) -> tuple[list[Pick], str]:
    """Model rungs, cheapest measured cost first. Returns (rungs, what was left out).

    A rung must clear the bar pick() uses: not ineligible, not cooling after a
    429/402/503, and not measured below :data:`LADDER_VALIDITY_BAR`. Unmeasured
    cost sorts after measured cost, in candidate order. When nothing clears the
    bar the one rung is whatever :func:`pick` would send, so the 429 cooldown
    and exploration rules keep the final say.
    """
    models, source = candidates(arm, pin=pin, pin_source=pin_source)
    stats = _stats(task, list(models))
    order = {m: i for i, m in enumerate(models)}
    keep: list[str] = []
    left_out: list[str] = []
    for model in models:
        st = stats[model]
        why = st.ineligible or (f"cooling ({st.cooling})" if st.cooling else "")
        if not why and st.score is not None and st.score < LADDER_VALIDITY_BAR:
            why = f"validity {st.score} < {LADDER_VALIDITY_BAR}"
        if why:
            left_out.append(f"{model} ({why})")
        else:
            keep.append(model)
    dropped = "; left out: " + ", ".join(left_out) if left_out else ""
    if not keep:
        chosen = pick(task, arm, pin=pin, pin_source=pin_source)
        return [
            Pick(
                chosen.requested,
                f"ladder rung 1/1: no candidate clears the bar; {chosen.reason}",
                chosen.source,
                chosen.candidates,
                chosen.measured_n,
                chosen.measured_score,
            )
        ], dropped
    keep.sort(key=lambda m: (stats[m].mean_cost is None, stats[m].mean_cost or 0.0, order[m]))
    rungs: list[Pick] = []
    for i, model in enumerate(keep, start=1):
        st = stats[model]
        cost = "unmeasured" if st.mean_cost is None else f"{st.mean_cost} tokens"
        rungs.append(
            Pick(
                model,
                f"ladder rung {i}/{len(keep)}: cheapest-first by mean cost ({cost}){dropped}",
                source,
                tuple(keep),
                st.scored_n,
                st.score,
            )
        )
    return rungs, dropped


def _step(stamp: RouteStamp, verdict: str) -> dict[str, Any]:
    return {
        "rung": stamp.rung,
        "call_id": stamp.call_id,
        "requested": stamp.requested,
        "served": stamp.served,
        "honored": stamp.honored,
        "served_provider": stamp.served_provider,
        "served_model": stamp.served_model,
        "served_local": stamp.served_local,
        "status": stamp.status,
        "verdict": verdict,
        "final": False,
    }


def _solver_rung(
    task: str,
    messages: list[dict[str, Any]],
    solver: Solver,
    accept: Callable[[str], Any] | None,
    credential: str,
) -> tuple[RouteStamp, str, str]:
    """Run one no-model rung under the checker. Returns (stamp, text, verdict)."""
    name = str(getattr(solver, "name", "") or type(solver).__name__)
    kind = str(getattr(solver, "kind", "") or "")
    stamp = RouteStamp(
        call_id=uuid.uuid4().hex,
        task=task,
        requested=name,
        impl=SOLVER_IMPL,
        credential=credential,
        rung=kind or "unknown",
        served_provider=SOLVER_IMPL,
        served_model=name,
        served_local=False,
        served_reason="in-process solver; no model hop",
    )
    text = ""
    if kind not in (RUNG_DETERMINISTIC, RUNG_CLASSIC_ML):
        verdict = f"skipped: unknown rung kind {kind!r}"
    elif kind == RUNG_CLASSIC_ML and not getattr(solver, "heldout", None):
        verdict = "disabled: classic ML rung has no held-out numbers"
    elif accept is None:
        verdict = "skipped: no checker (a solver answer is never unchecked)"
    else:
        try:
            got = solver.solve(task, messages)
        except Exception as exc:  # noqa: BLE001 - a broken solver is a rung failure
            got, verdict = None, f"error: {type(exc).__name__}"
        else:
            verdict = "abstained" if not (got or "").strip() else ""
        if got and got.strip() and not verdict:
            try:
                ok = bool(accept(got))
            except Exception:  # noqa: BLE001 - a raising checker means rejected
                ok = False
            verdict = "accepted" if ok else "rejected"
            if ok:
                text = got
                stamp.served = name
                stamp.usable = True
    if verdict != "accepted":
        stamp.error = f"{kind or 'unknown'} rung {name}: {verdict}"
    _journal_add(stamp)
    return stamp, text, verdict


def _model_verdict(out: Completion) -> str:
    stamp = out.stamp
    status = int(stamp.status or 0) if stamp is not None and stamp.status is not None else 0
    if out.ok:
        return "accepted"
    if status == 200:
        return "rejected"
    if status in _STATUS_REASONS:
        return f"refused (HTTP {status})"
    return f"failed (HTTP {status})"


def _ladder_complete(
    task: str,
    messages: list[dict[str, Any]],
    arm: Arming,
    *,
    steps: int,
    note: str,
    solvers: tuple[Solver, ...],
    accept: Callable[[str], Any] | None,
    pin: str,
    pin_source: str,
    send_kw: dict[str, Any],
    skip: freeroute_prespend.Gate | None = None,
) -> Completion:
    """No-model rungs, then model rungs cheapest-first; step up only on a checker reject.

    - Every rung is checked by ``accept`` (plus the usable-answer rule).
    - A rejected answer, or a non-refusal failure (500, 504, ...), steps up one
      rung, at most ``steps`` times.
    - A refusal status (402, 429 and the rest of ``_STATUS_REASONS``) stops the
      ladder: it is a budget / custody answer, not a verdict on the model.
    - Every rung tried is on the returned stamp's ``ladder``, in order, and
      the one that answered is ``final``. No rung passing is an honest
      refusal with no text, never the best rejected guess.
    """
    credential = str(send_kw.get("credential") or "")
    tried: list[dict[str, Any]] = []
    for solver in solvers:
        stamp, text, verdict = _solver_rung(task, messages, solver, accept, credential)
        tried.append(_step(stamp, verdict))
        if verdict == "accepted":
            tried[-1]["final"] = True
            stamp.ladder = tried
            return Completion(ok=True, text=text, message={"role": "assistant", "content": text}, stamp=stamp)
    if skip is not None:
        # #271: the no-model rungs were free; the first paid rung is not sent.
        return _prespend_skipped(task, credential, skip, tried)

    rungs, _ = ladder_order(task, arm, pin=pin, pin_source=pin_source)
    last: Completion | None = None
    stop = ""
    for i, chosen in enumerate(rungs[: steps + 1]):
        if note:
            chosen = Pick(
                chosen.requested,
                f"{chosen.reason}; {note}",
                chosen.source,
                chosen.candidates,
                chosen.measured_n,
                chosen.measured_score,
            )
        out = _send(task, messages, arm, chosen, **send_kw)
        assert out.stamp is not None  # _send always stamps
        # Local only when OpenVault said so for this call (#272), never by name.
        out.stamp.rung = (
            RUNG_LOCAL if out.stamp.served_local else RUNG_CLOUD if out.stamp.status == 200 else "model"
        )
        verdict = _model_verdict(out)
        tried.append(_step(out.stamp, verdict))
        last = out
        if out.ok:
            tried[-1]["final"] = True
            out.stamp.ladder = tried
            return out
        if verdict.startswith("refused"):
            stop = f"; stopped at rung {i + 1} on a refusal status, no step-up"
            break
    assert last is not None and last.stamp is not None  # ladder_order returns >= 1 rung
    if not stop and len(rungs) > steps + 1:
        stop = f"; step limit {steps} reached"
    reason = f"ladder: no rung passed the checker ({len(tried)} tried{stop}): {last.reason}"
    last.stamp.error = reason
    last.stamp.ladder = tried
    return Completion(ok=False, stamp=last.stamp, reason=reason, usage=last.usage)


# -- complete -------------------------------------------------------------------


def complete(
    task: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 600,
    temperature: float | None = None,
    timeout: float = 45.0,
    accept: Callable[[str], Any] | None = None,
    pin: str = "",
    pin_source: str = "",
    egress: str = "",
    tools: list[dict[str, Any]] | None = None,
    bearer: str | None = None,
    split: str = "",
    ladder: int | None = None,
    solvers: Sequence[Solver] | None = None,
    predict_state: Mapping[str, Any] | None = None,
) -> Completion:
    """One FreeRoute call. Never raises. Refusals are named and stamped.

    ``ladder`` / ``solvers`` (or ``CORTEX_FREEROUTE_LADDER``) opt into the #270
    ladder: see :func:`_ladder_complete`. Unset, this is one model request.
    ``predict_state`` is the plan the #271 pre-spend gate is asked about
    (``CORTEX_FREEROUTE_PRESPEND``; see :mod:`freeroute_prespend`).
    """
    task = (task or "unnamed").strip()
    arm = arming(bearer=bearer)
    direct = direct_providers.enabled()
    credential = (
        "process-env provider key" if direct and bearer is None
        else "relayed caller (refused: env-direct)" if direct
        else "cortex api_key" if bearer is None and _token()
        else "relayed bearer" if bearer else "loopback tier (unattributed)"
    )
    if not arm.armed:
        reason = f"FreeRoute not armed: {arm.reason}"
        if direct and bearer is not None:
            # Stamped so the envelope names the refusal and says NOT OpenVault.
            stamp = RouteStamp(
                call_id=uuid.uuid4().hex,
                task=task,
                requested="",
                error=reason,
                credential=credential,
                impl=direct_providers.IMPL,
            )
            _journal_add(stamp)
            return Completion(ok=False, stamp=stamp, reason=reason)
        return Completion(ok=False, reason=reason)
    if direct and local_only_enabled():
        reason = (
            f"{LOCAL_ONLY_ENV}=1: {direct_providers.CLOUD_ONLY}; "
            "no local hop and no cloud fallback"
        )
        stamp = RouteStamp(
            call_id=uuid.uuid4().hex,
            task=task,
            requested="",
            error=reason,
            credential=credential,
            impl=direct_providers.IMPL,
            served_local=False,
            served_reason=direct_providers.CLOUD_ONLY,
        )
        _journal_add(stamp)
        return Completion(ok=False, stamp=stamp, reason=reason, text="")
    if local_only_enabled() and arm.local_spendable_hops <= 0:
        named = arm.local_reason
        extra = (
            f" ({freeroute_ov_local.LOCAL_ONLY_UNAVAILABLE_TYPE} {named})"
            if named in freeroute_ov_local.LOCAL_UNAVAILABLE_REASONS
            else ""
        )
        reason = (
            f"{LOCAL_ONLY_ENV}=1: OpenVault reports no local spendable hop "
            f"(no cloud fallback){extra}"
        )
        stamp = RouteStamp(
            call_id=uuid.uuid4().hex,
            task=task,
            requested="",
            error=reason,
            credential=credential,
            impl=_transport()[1],
            served_local=False,
            served_reason=named or "OpenVault reported no local spendable hop",
        )
        _journal_add(stamp)
        return Completion(ok=False, stamp=stamp, reason=reason, text="")
    leave_decision = ""
    if egress == "leave":
        allowed, why = leave_gate()
        if not allowed:
            reason = f"OpenVault leave-machine gate denied: {why}"
            stamp = RouteStamp(
                call_id=uuid.uuid4().hex,
                task=task,
                requested="",
                error=reason,
                credential=credential,
                leave_gate=f"denied: {why}",
            )
            _journal_add(stamp)
            return Completion(ok=False, stamp=stamp, reason=reason)
        leave_decision = why

    gate = freeroute_prespend.check(predict_state)
    prespend = dict(gate.record) if gate is not None else {}
    skip = gate if gate is not None and not gate.spend else None
    steps, ladder_note = _ladder_steps(ladder)
    if skip is not None and not solvers:
        return _prespend_skipped(task, credential, skip, [])
    if steps or solvers:
        return _ladder_complete(
            task,
            messages,
            arm,
            steps=steps,
            note=ladder_note,
            solvers=tuple(solvers or ()),
            accept=accept,
            pin=pin,
            pin_source=pin_source,
            skip=skip,
            send_kw={
                "max_tokens": max_tokens,
                "temperature": temperature,
                "timeout": timeout,
                "accept": accept,
                "tools": tools,
                "bearer": bearer,
                "split": split,
                "credential": credential,
                "leave_decision": leave_decision,
                "prespend": prespend,
            },
        )
    chosen = pick(task, arm, pin=pin, pin_source=pin_source)
    if ladder_note:
        chosen = Pick(
            chosen.requested,
            f"{chosen.reason}; {ladder_note}",
            chosen.source,
            chosen.candidates,
            chosen.measured_n,
            chosen.measured_score,
        )
    return _send(
        task,
        messages,
        arm,
        chosen,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        accept=accept,
        tools=tools,
        bearer=bearer,
        split=split,
        credential=credential,
        leave_decision=leave_decision,
        prespend=prespend,
    )


def _prespend_skipped(
    task: str, credential: str, gate: freeroute_prespend.Gate, tried: list[dict[str, Any]]
) -> Completion:
    """The #271 gate said do not spend: a named refusal, stamped, nothing sent."""
    stamp = RouteStamp(
        call_id=uuid.uuid4().hex,
        task=task,
        requested="",
        error=gate.reason,
        credential=credential,
        impl=_transport()[1],
        prespend=dict(gate.record),
        ladder=tried,
    )
    _journal_add(stamp)
    return Completion(ok=False, stamp=stamp, reason=gate.reason)


def _send(
    task: str,
    messages: list[dict[str, Any]],
    arm: Arming,
    chosen: Pick,
    *,
    max_tokens: int,
    temperature: float | None,
    timeout: float,
    accept: Callable[[str], Any] | None,
    tools: list[dict[str, Any]] | None,
    bearer: str | None,
    split: str,
    credential: str,
    leave_decision: str,
    prespend: dict[str, Any] | None = None,
) -> Completion:
    """One model request for ``chosen``: stamped, stored, journalled. Never raises."""
    # Cortex #268: mask PII before anything is sent, on every request (single
    # call and every ladder rung). Fail closed: a masker error refuses the call;
    # nothing goes out unmasked.
    try:
        masked = pii_mask.mask_messages(messages)
    except pii_mask.MaskingFailed as exc:
        reason = f"PII masking failed, not sent ({pii_mask.REFUSED_REASON}): {redact(exc)}"
        stamp = RouteStamp(
            call_id=uuid.uuid4().hex,
            task=task,
            requested="",
            error=reason,
            credential=credential,
            impl=_transport()[1],
            leave_gate=leave_decision,
            prespend=dict(prespend or {}),
        )
        _journal_add(stamp)
        return Completion(ok=False, stamp=stamp, reason=reason)
    body: dict[str, Any] = {
        "model": chosen.requested,
        "messages": masked.messages,
        "max_tokens": int(max_tokens),
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if local_only_enabled():
        body.update(freeroute_ov_local.local_only_request_fields())

    send, impl = _transport()
    started = time.monotonic()
    try:
        status, data = send(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers=auth_headers(bearer),
            timeout=timeout,
            base=arm.url,
        )
    except Exception as exc:  # noqa: BLE001 - a transport must not crash the ask
        status, data = 0, {"error": {"message": f"transport error: {type(exc).__name__}"}}
    latency_ms = round((time.monotonic() - started) * 1000.0, 1)

    stamp = RouteStamp(
        call_id=uuid.uuid4().hex,
        task=task,
        requested=chosen.requested,
        pick_reason=chosen.reason,
        source=chosen.source,
        candidates=chosen.candidates,
        status=int(status),
        latency_ms=latency_ms,
        impl=impl,
        credential=credential,
        leave_gate=leave_decision,
        measured_n=chosen.measured_n,
        measured_score=chosen.measured_score,
        prespend=dict(prespend or {}),
        masked=dict(masked.counts),
    )
    text = ""
    message: dict[str, Any] = {}
    usage: dict[str, Any] = {}
    scored = False
    reason = ""
    provider, served_model, is_local, served_reason = freeroute_ov_local.served_from_response(
        data if isinstance(data, dict) else None
    )
    stamp.served_provider = provider
    stamp.served_model = served_model
    stamp.served_local = is_local
    stamp.served_reason = served_reason
    if status == 200 and isinstance(data, dict):
        stamp.served = str(data.get("model") or "")
        stamp.honored = _same_model(chosen.requested, stamp.served)
        try:
            choice = (data.get("choices") or [{}])[0] or {}
            message = dict(choice.get("message") or {})
        except (IndexError, TypeError, AttributeError):
            message = {}
        # Placeholders come back as the real values before any parse or validation.
        message = pii_mask.restore_value(message, masked.restore)
        text = str(message.get("content") or "")
        usage = dict(data.get("usage") or {}) if isinstance(data.get("usage"), dict) else {}
        usable = bool(text.strip()) or bool(message.get("tool_calls"))
        if usable and accept is not None:
            try:
                usable = bool(accept(text))
            except Exception:  # noqa: BLE001 - a raising validator means unusable
                usable = False
        stamp.usable = usable
        scored = True
        if not usable:
            reason = f"FreeRoute answer unusable for {task} (served {stamp.served or 'unknown'})"
    else:
        direct_wire = impl == direct_providers.IMPL
        base = (
            f"env-direct provider answered HTTP {status}"
            if direct_wire
            else _STATUS_REASONS.get(int(status), f"OpenVault answered HTTP {status}")
        )
        detail = _error_message(data)
        reason = f"{base}: {detail}" if detail else base
        # Refusals about custody, budget or the hop pool say nothing about the
        # model asked for; only other failures (500, 504, ...) count against it.
        scored = int(status) not in _STATUS_REASONS
        # A provider 401 in env-direct says nothing about the OpenVault credential.
        if int(status) in (401, 403) and not direct_wire:
            if int(status) == 401:
                note_rejected(_credential_fp(bearer), reason, url=arm.url)
            else:
                with _lock:
                    _arming_cache.clear()
                    _vault_cache.clear()
    if local_only_enabled():
        err_type, err_reason = freeroute_ov_local.chat_error_fields(
            data if isinstance(data, dict) else None
        )
        if status == 403 and err_type == freeroute_ov_local.VAULT_SEALED_TYPE:
            reason = f"{LOCAL_ONLY_ENV}=1: {freeroute_ov_local.VAULT_SEALED_TYPE}"
            text = ""
            message = {}
            stamp.usable = False
        elif status == 503 and err_type == freeroute_ov_local.LOCAL_ONLY_UNAVAILABLE_TYPE:
            named = (
                err_reason
                if err_reason in freeroute_ov_local.LOCAL_UNAVAILABLE_REASONS
                else err_reason
            )
            reason = (
                f"{LOCAL_ONLY_ENV}=1: {freeroute_ov_local.LOCAL_ONLY_UNAVAILABLE_TYPE}"
                + (f" ({named})" if named else "")
            )
            text = ""
            message = {}
            stamp.usable = False
        elif status != 200:
            reason = (
                f"{LOCAL_ONLY_ENV}=1: OpenVault could not honour local-only"
                + (f" ({reason})" if reason else "")
            )
            text = ""
            message = {}
            stamp.usable = False
        elif not stamp.served_local:
            reason = (
                f"{LOCAL_ONLY_ENV}=1: OpenVault did not serve locally"
                + (f" ({stamp.served_reason})" if stamp.served_reason else "")
            )
            text = ""
            message = {}
            stamp.usable = False
    stamp.error = reason
    _write_row(
        stamp,
        scored=scored,
        usage=usage,
        split=split or _split_var.get(),
    )
    _journal_add(stamp)
    return Completion(
        ok=status == 200 and stamp.usable,
        text=text,
        message=message,
        stamp=stamp,
        reason=reason,
        usage=usage,
    )


# -- status ---------------------------------------------------------------------


def scoreboard(task: str | None = None) -> list[dict[str, Any]]:
    with _connect(write=False) as con:
        if con is None:
            return []
        con.row_factory = sqlite3.Row
        try:
            where = "WHERE task = ?" if task else ""
            args: tuple[Any, ...] = (task,) if task else ()
            rows = list(
                con.execute(
                    "SELECT task, requested, served, COUNT(*) AS n, SUM(scored) AS scored, "
                    "SUM(CASE WHEN status = 200 THEN 1 ELSE 0 END) AS answered "
                    f"FROM routes {where} GROUP BY task, requested, served ORDER BY task, n DESC",
                    args,
                )
            )
        except sqlite3.Error as exc:
            _note_store_error(exc)
            return []
    return [dict(r) for r in rows]


def store_state() -> dict[str, Any]:
    """Route-store id at call time: row count plus a hash of call_ids. No secrets."""
    path = store_path()
    empty = {"id": "n=0:empty", "row_count": 0, "sha256": "", "path": str(path)}
    if not path.is_file():
        return empty
    with _connect(write=False) as con:
        if con is None:
            return empty
        rows = list(con.execute("SELECT call_id FROM routes ORDER BY ts, call_id"))
    n = len(rows)
    digest = hashlib.sha256("\n".join(r[0] for r in rows).encode("utf-8")).hexdigest()[:16] if n else ""
    return {
        "id": f"n={n}:{digest}" if n else "n=0:empty",
        "row_count": n,
        "sha256": digest,
        "path": str(path),
    }


def router_fingerprint(stamp: RouteStamp | None = None) -> dict[str, Any]:
    """Insights setup fields. served_* copy a RouteStamp or stay unproven.

    Never inferred from requested / served / route.model. Empty stamp (no
    provider, no model, not local, empty reason) keeps SERVED_PENDING_272.
    """
    learn = learn_state()
    store = store_state()
    if stamp is None:
        served_provider: str | None = None
        served_model: str | None = None
        served_local = False
        served_reason = SERVED_PENDING_272
    else:
        served_provider = stamp.served_provider
        served_model = stamp.served_model
        served_local = bool(stamp.served_local)
        empty = (
            served_provider is None
            and served_model is None
            and served_local is False
            and not (stamp.served_reason or "").strip()
        )
        served_reason = SERVED_PENDING_272 if empty else stamp.served_reason
    out: dict[str, Any] = {
        "served_provider": served_provider,
        "served_model": served_model,
        "served_local": served_local,
        "served_reason": served_reason,
        "learn_enabled": learn["learn_enabled"],
        "learn_source": learn["learn_source"],
        "route_store_id": store["id"],
        "route_store": store,
        "masking_state": MASKING_STATE,
    }
    if stamp is not None and stamp.ladder:
        # #270: every rung tried, in order, with the answering one marked final.
        out["ladder"] = [dict(step) for step in stamp.ladder]
    return out


def stamp_router_fingerprint(
    envelope: dict[str, Any],
    stamp: RouteStamp | None = None,
) -> dict[str, Any]:
    envelope.update(router_fingerprint(stamp))
    return envelope


def public_status(task: str | None = None) -> dict[str, Any]:
    arm = arming()
    models, source = candidates(arm) if arm.armed else ((), "")
    stats = _stats(task, list(models)) if task and models else {}
    learn = learn_state()
    store = store_state()
    return {
        "layer": (
            "env-direct provider keys (NOT OpenVault)"
            if direct_providers.enabled()
            else "OpenVault FreeRoute (one Cortex model layer)"
        ),
        "impl": _transport()[1],
        "arming": arm.public(),
        "identity": identity(),
        "candidates": list(models),
        "candidate_source": source,
        "measured": {m: asdict(s) for m, s in stats.items()},
        "measured_excludes": ["shadow", "heldout", "benchmark"],
        "scoreboard": scoreboard(task),
        "store": str(store_path()),
        "store_error": store_error(),
        "learning": learn["learn_enabled"],
        "learn_source": learn["learn_source"],
        "route_store_id": store["id"],
        "masking_state": MASKING_STATE,
    }


def reset() -> None:
    """Drop in-process caches (arming, rejections, verification, the latched
    env-direct opt-in). The store stays."""
    direct_providers.reset_opt_in()
    with _lock:
        _arming_cache.clear()
        _vault_cache.clear()
        _rejected.clear()
        _verified.clear()
        _last_arming.clear()
        _store_error.clear()
    with _store_lock:
        _initialized.clear()


__all__ = [
    "Arming",
    "Completion",
    "IMPL",
    "LEARN_ENV",
    "LOCAL_ONLY_ENV",
    "MASKING_STATE",
    "Pick",
    "RouteStamp",
    "SERVED_PENDING_272",
    "SPLIT_BENCHMARK",
    "SPLIT_HELDOUT",
    "SPLIT_PRODUCT",
    "SPLIT_TRAIN",
    "Solver",
    "TOKEN_ENV",
    "arming",
    "auth_headers",
    "candidates",
    "child_env",
    "complete",
    "fingerprint",
    "identity",
    "journal",
    "ladder_order",
    "last_line",
    "learn_state",
    "leave_gate",
    "local_only_enabled",
    "note_rejected",
    "note_verdict",
    "peek",
    "pick",
    "public_status",
    "redact",
    "reset",
    "router_fingerprint",
    "scoreboard",
    "stamp_router_fingerprint",
    "store_error",
    "store_path",
    "store_state",
    "unarmed",
    "use_transport",
]
