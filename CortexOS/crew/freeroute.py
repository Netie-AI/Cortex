"""OpenVault FreeRoute as the central Cortex AI layer (Cortex #211).

Insights / generative-ask / prompt-think-act go through this module when a
model is needed. It is not a second vault: arming, keys, and completions
reuse ``CortexOS.crew.openvault``. Unarmed is fail-closed. No invent-green
keys. Crew chat may still pin litellm; this layer does not silently walk
that chain.

Measured climb numbers from DMS #180 stay cited as baseline only
(gen 57.69% vs exact 38.46%, WRONG=0 both @ d2f116a6). They are not model
scores. gen_cfsm + dag_runner already shipped G1 on tip; this slice does
not clone them.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any

from CortexOS.crew.llm import LLMError, LLMResult

# Stable in-Cortex identity. Keys stay in OpenVault custody. Not a minted secret.
CORTEX_IDENTITY = "cortex:crew"
IDENTITY_HEADER = "X-Cortex-Identity"
PURPOSES = ("prompt", "think", "act", "insights", "generative_ask")

DMS_180_BASELINE = {
    "cite": "DMS #180 Formal GREEN @ d2f116a6",
    "gen": "57.69%",
    "exact": "38.46%",
    "wrong": 0,
    "note": "baseline only; this slice does not invent a better climb",
}

# Bundled families FreeRoute may pick among. Catalog order is not a live score.
# DeepSeek + Qwen are first so a measured pick is never "grok is the only path".
_BUNDLED: tuple[tuple[str, str, str], ...] = (
    ("deepseek", "deepseek-chat", "cloud"),
    ("qwen", "qwen2.5-7b-instruct", "local"),
    ("openrouter", "deepseek/deepseek-chat", "cloud"),
    ("groq", "llama-3.3-70b-versatile", "cloud"),
    ("ollama", "qwen2.5-7b-instruct", "local"),
    ("google", "gemini-2.0-flash", "cloud"),
    ("mistral", "mistral-small-latest", "cloud"),
    ("cerebras", "llama3.1-8b", "cloud"),
    ("anthropic", "claude-sonnet-5", "cloud"),
    ("openai-compatible", "gpt-4o-mini", "cloud"),
    ("cursor", "grok-4.6", "cloud"),
    ("xai", "grok-4", "cloud"),
)

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.I | re.S)
_SELECT = re.compile(r"(SELECT\b.+)", re.I | re.S)
_FROM = re.compile(r"\b(?:FROM|JOIN)\s+(?:[\w]+\.)?([A-Za-z_][\w]*)", re.I)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|COPY|PRAGMA|INSTALL|LOAD)\b",
    re.I,
)

_MEASURED: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class RoutePick:
    label: str
    model: str
    kind: str
    why: str
    connector: str = "openvault"
    score: float | None = None
    identity: str = CORTEX_IDENTITY

    def as_public(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "kind": self.kind,
            "why": self.why,
            "connector": self.connector,
            "score": self.score,
            "identity": self.identity,
        }


def identity_for(purpose: str = "") -> str:
    raw = (purpose or "").strip().lower().replace("-", "_")
    if raw in PURPOSES:
        return f"{CORTEX_IDENTITY}:{raw.replace('_', '-')}"
    return CORTEX_IDENTITY


def reset_measurements() -> None:
    """Test hook. Production records live complete() latency only."""
    _MEASURED.clear()


def record_measurement(
    label: str,
    *,
    latency_ms: float,
    ok: bool,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    name = (label or "").strip() or "unknown"
    slot = _MEASURED.setdefault(
        name,
        {"calls": 0, "ok": 0, "errors": 0, "latency_ms": 0.0, "cost_usd": 0.0},
    )
    slot["calls"] = int(slot["calls"]) + 1
    if ok:
        slot["ok"] = int(slot["ok"]) + 1
    else:
        slot["errors"] = int(slot["errors"]) + 1
    n = int(slot["calls"])
    prev = float(slot["latency_ms"] or 0.0)
    slot["latency_ms"] = round(((prev * (n - 1)) + float(latency_ms)) / n, 3)
    slot["cost_usd"] = round(float(slot["cost_usd"] or 0.0) + float(cost_usd or 0.0), 6)
    return dict(slot)


def measurements() -> dict[str, dict[str, Any]]:
    return {name: dict(slot) for name, slot in _MEASURED.items()}


def _env_api_keys() -> bool:
    for key in (
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "CURSOR_API_KEY",
        "XAI_API_KEY",
        "GROQ_API_KEY",
        "GOOGLE_API_KEY",
        "CEREBRAS_API_KEY",
        "MISTRAL_API_KEY",
    ):
        if os.environ.get(key, "").strip():
            return True
    return False


def _local_ollama() -> str:
    if os.environ.get("CREW_ALLOW_OLLAMA", "1") == "0":
        return ""
    from CortexOS.crew.config import _ollama_first_model

    found = _ollama_first_model(
        os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    )
    return str(found or "")


def arming() -> dict[str, Any]:
    """Vault live + (local or cloud keys). Fail-closed. Never invents a key."""
    from CortexOS.crew.openvault import healthz, vault_armed_labels

    if os.environ.get("CREW_OPENVAULT", "1") == "0":
        return {
            "ok": False,
            "armed": False,
            "vault_live": False,
            "local_keys": False,
            "cloud_keys": False,
            "detail": "CREW_OPENVAULT=0 (no invent-green keys)",
            "custody": "openvault",
            "live_5000_ci": False,
        }
    st = healthz()
    live = bool(st.get("ok"))
    armed_labels = vault_armed_labels() if live else set()
    ollama = _local_ollama()
    local_keys = bool(ollama) or "ollama" in armed_labels or "qwen" in armed_labels
    cloud_keys = bool(armed_labels - {"ollama", "qwen"}) or _env_api_keys()
    armed = bool(live and (local_keys or cloud_keys or armed_labels))
    detail = "vault-armed" if armed else (
        str(st.get("detail") or "OpenVault not live")
        if not live
        else "OpenVault live but no local or cloud keys armed (no invent-green keys)"
    )
    return {
        "ok": armed,
        "armed": armed,
        "vault_live": live,
        "local_keys": local_keys,
        "cloud_keys": cloud_keys,
        "labels": sorted(armed_labels),
        "ollama": ollama or "",
        "detail": detail,
        "custody": "openvault",
        "live_5000_ci": False,
        "url": st.get("url"),
    }


def require_armed() -> dict[str, Any]:
    snap = arming()
    if not snap.get("armed"):
        raise LLMError(
            "OpenVault FreeRoute unarmed: "
            + str(snap.get("detail") or "unreachable")
            + " (no silent fallback; no invent-green keys)"
        )
    return snap


def public_identity() -> dict[str, Any]:
    """Stable Cortex API identity. Never returns a token or provider secret."""
    return {
        "ok": True,
        "identity": CORTEX_IDENTITY,
        "header": IDENTITY_HEADER,
        "surfaces": {p: identity_for(p) for p in PURPOSES},
        "custody": "openvault",
        "mint": False,
        "token_returned": False,
        "seeded_cortex_primary": "disabled (HTTP 404 seed is not this identity)",
        "live_5000_ci": False,
        "measured_baseline": dict(DMS_180_BASELINE),
    }


def _family_model(label: str, default: str) -> str:
    env_map = {
        "deepseek": "CREW_DEEPSEEK_MODEL",
        "openrouter": "CREW_OPENROUTER_MODEL",
        "groq": "CREW_GROQ_MODEL",
        "google": "CREW_GOOGLE_MODEL",
        "mistral": "CREW_MISTRAL_MODEL",
        "cerebras": "CREW_CEREBRAS_MODEL",
        "anthropic": "CREW_ANTHROPIC_MODEL",
        "openai-compatible": "CREW_OPENAI_MODEL",
        "cursor": "CREW_CURSOR_MODEL",
        "xai": "CREW_XAI_MODEL",
        "qwen": "CREW_QWEN_MODEL",
        "ollama": "",
    }
    env_name = env_map.get(label, "")
    if env_name:
        return os.environ.get(env_name, "").strip() or default
    return default


def _score(slot: dict[str, Any] | None) -> float | None:
    if not slot:
        return None
    calls = int(slot.get("calls") or 0)
    if calls <= 0:
        return None
    ok = int(slot.get("ok") or 0)
    errors = int(slot.get("errors") or 0)
    rate = ok / calls if calls else 0.0
    latency = float(slot.get("latency_ms") or 0.0)
    cost = float(slot.get("cost_usd") or 0.0)
    # Higher is better. Errors and latency lose. Not DMS #180 climb %.
    return round((rate * 1000.0) - (latency / 10.0) - (cost * 100.0) - (errors * 50.0), 3)


def _ov_model_ids() -> list[str]:
    from CortexOS.crew.openvault import list_models

    return [str(row.get("id") or "") for row in list_models() if row.get("id")]


def candidates() -> list[dict[str, Any]]:
    """Armed bundled families (local or cloud). Empty when unarmed."""
    snap = arming()
    if not snap.get("armed"):
        return []
    labels = set(snap.get("labels") or [])
    ollama = str(snap.get("ollama") or "")
    ov_ids = _ov_model_ids()
    env_labels: set[str] = set()
    env_to_label = {
        "ANTHROPIC_API_KEY": "anthropic",
        "OPENROUTER_API_KEY": "openrouter",
        "DEEPSEEK_API_KEY": "deepseek",
        "OPENAI_API_KEY": "openai-compatible",
        "CURSOR_API_KEY": "cursor",
        "XAI_API_KEY": "xai",
        "GROQ_API_KEY": "groq",
        "GOOGLE_API_KEY": "google",
        "CEREBRAS_API_KEY": "cerebras",
        "MISTRAL_API_KEY": "mistral",
    }
    for env_key, label in env_to_label.items():
        if os.environ.get(env_key, "").strip():
            env_labels.add(label)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for label, default, kind in _BUNDLED:
        armed = False
        model = _family_model(label, default)
        if label in labels or label in env_labels:
            armed = True
        if label == "ollama" and ollama:
            armed = True
            model = ollama
        if label == "qwen":
            if ollama and "qwen" in ollama.lower():
                armed = True
                model = ollama
                kind = "local"
            elif any("qwen" in mid.lower() for mid in ov_ids):
                armed = True
                qwen_id = next(mid for mid in ov_ids if "qwen" in mid.lower())
                model = qwen_id
            elif "qwen" in labels:
                armed = True
        if not armed:
            continue
        if ov_ids and label in {"deepseek", "qwen", "groq", "mistral"}:
            hit = next(
                (mid for mid in ov_ids if label in mid.lower() or default.split("/")[-1] in mid.lower()),
                "",
            )
            if hit:
                model = hit
        key = f"{label}:{model}"
        if key in seen:
            continue
        seen.add(key)
        slot = _MEASURED.get(label)
        out.append(
            {
                "label": label,
                "model": model,
                "kind": kind,
                "score": _score(slot),
                "measured": slot is not None,
            }
        )
    return out


def pick_route(*, purpose: str = "think") -> RoutePick:
    """Pick a measured FreeRoute model. Never hardcode one vendor as the only path."""
    ident = identity_for(purpose)
    rows = candidates()
    if not rows:
        raise LLMError(
            "OpenVault FreeRoute has no armed bundled models "
            "(DeepSeek+Qwen+...); no silent fallback; no invent-green keys"
        )
    measured_rows = [r for r in rows if r.get("score") is not None]
    if measured_rows:
        best = max(
            measured_rows,
            key=lambda r: (float(r["score"]), 0 if r["kind"] == "local" else -1, r["label"]),
        )
        return RoutePick(
            label=str(best["label"]),
            model=str(best["model"]),
            kind=str(best["kind"]),
            why=(
                f"measured {best['label']} score {best['score']} "
                f"among {len(rows)} armed families (not a hardcoded vendor)"
            ),
            score=float(best["score"]) if best.get("score") is not None else None,
            identity=ident,
        )
    if len(rows) == 1:
        row = rows[0]
        return RoutePick(
            label=str(row["label"]),
            model=str(row["model"]),
            kind=str(row["kind"]),
            why=f"only armed family {row['label']} (local or cloud key in OpenVault)",
            identity=ident,
        )
    # Several armed, none measured here: send auto and let OpenVault measure.
    kinds = ",".join(sorted({str(r["label"]) for r in rows}))
    return RoutePick(
        label="openvault",
        model="auto",
        kind="cloud" if any(r["kind"] == "cloud" for r in rows) else "local",
        why=(
            f"OpenVault FreeRoute auto among {kinds} "
            "(vault measures; crew does not hardcode grok or gpt-4o-mini)"
        ),
        identity=ident,
    )


def extract_sql(text: str) -> str | None:
    if not text:
        return None
    blobs: list[str] = []
    for match in _SQL_FENCE.finditer(text):
        blobs.append(match.group(1).strip())
    blobs.append(_SQL_FENCE.sub(" ", text).strip())
    blobs.append(text.strip())
    for body in blobs:
        found = _SELECT.search(body)
        if not found:
            continue
        sql = found.group(1).strip().rstrip(";")
        if ";" in sql:
            sql = sql.split(";", 1)[0].strip()
        if sql.upper().startswith("SELECT") and _FROM.search(sql):
            return sql
    return None


def validate_sql(sql: str, allowed_tables: set[str]) -> dict[str, Any]:
    """Ontology-constrained SELECT. Crew does not execute. Fail-closed."""
    text = (sql or "").strip()
    if not text:
        return {"ok": False, "sql": None, "reason": "empty sql"}
    if _FORBIDDEN.search(text):
        return {"ok": False, "sql": None, "reason": "non-select sql refused"}
    if not text.upper().lstrip().startswith("SELECT"):
        return {"ok": False, "sql": None, "reason": "not a select"}
    tables = {m.group(1).lower() for m in _FROM.finditer(text)}
    if not tables:
        return {"ok": False, "sql": None, "reason": "select has no from/join table"}
    allowed = {t.lower() for t in allowed_tables if t}
    extra = sorted(tables - allowed) if allowed else []
    if allowed and extra:
        return {
            "ok": False,
            "sql": None,
            "reason": "sql reads tables outside ontology ranking: " + ",".join(extra),
            "tables": sorted(tables),
        }
    return {"ok": True, "sql": text, "tables": sorted(tables), "reason": ""}


def public_status() -> dict[str, Any]:
    snap = arming()
    ident = public_identity()
    chosen: dict[str, Any] | None = None
    refused = None
    cands = candidates() if snap.get("armed") else []
    if snap.get("armed"):
        try:
            chosen = pick_route(purpose="insights").as_public()
        except LLMError as exc:
            refused = str(exc)
    else:
        refused = str(snap.get("detail") or "unarmed")
    return {
        "ok": bool(snap.get("armed")),
        "armed": bool(snap.get("armed")),
        "custody": "openvault",
        "identity": ident,
        "chosen": chosen,
        "candidates": cands,
        "refused": refused,
        "arming": snap,
        "live_5000_ci": False,
        "measured_baseline": dict(DMS_180_BASELINE),
        "gencfsm": "reuse G1 on tip; this slice does not clone gen_cfsm/dag_runner",
        "layer": "OpenVault FreeRoute is the central Cortex AI path when a model is needed",
    }


_PURPOSE_SYSTEM = {
    "prompt": (
        "You write one prompt for a later model call. Return the prompt only. "
        "Do not invent API keys, numbers, or live CI."
    ),
    "think": (
        "You think through the operator ask. No tools. Do not invent numbers. "
        "gen_cfsm and dag_runner already exist on Cortex; do not clone them."
    ),
    "act": (
        "You may call offered tools. Do not invent keys, numbers, or live CI. "
        "Refuse when OpenVault would be required and is unarmed."
    ),
    "insights": (
        "You help Cortex Insights. Ontology is already ranked. "
        "Do not invent warehouse numbers."
    ),
    "generative_ask": (
        "You write a single DuckDB SELECT for the ranked ontology tables. "
        "Use ONLY those tables and columns. No DDL/DML. SQL only. "
        "Do not invent numeric answers in prose."
    ),
}


async def complete(
    messages: list[dict[str, Any]] | None = None,
    *,
    purpose: str = "think",
    prompt: str = "",
    tools: list[dict[str, Any]] | None = None,
    pick: RoutePick | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    """Generate / think / act via OpenVault FreeRoute. Fail-closed when unarmed."""
    purpose_key = (purpose or "think").strip().lower().replace("-", "_")
    if purpose_key not in PURPOSES:
        return {
            "ok": False,
            "status": "REFUSE",
            "purpose": purpose,
            "refused": f"unknown purpose '{purpose}'",
            "values": [],
            "live_5000_ci": False,
        }
    try:
        arm = require_armed()
        chosen = pick or pick_route(purpose=purpose_key)
    except LLMError as exc:
        return {
            "ok": False,
            "status": "REFUSE",
            "purpose": purpose_key,
            "identity": identity_for(purpose_key),
            "refused": str(exc),
            "values": [],
            "live_5000_ci": False,
        }

    body = list(messages or [])
    if prompt.strip():
        body.append({"role": "user", "content": prompt.strip()})
    if not body:
        return {
            "ok": False,
            "status": "REFUSE",
            "purpose": purpose_key,
            "identity": chosen.identity,
            "route": chosen.as_public(),
            "refused": "empty messages",
            "values": [],
            "live_5000_ci": False,
        }
    if not any(m.get("role") == "system" for m in body):
        body = [{"role": "system", "content": _PURPOSE_SYSTEM[purpose_key]}, *body]

    from CortexOS.crew import openvault as ov

    ov.ratelimit(chosen.identity)
    started = time.monotonic()
    try:
        result: LLMResult = await ov.chat(
            body,
            tools=tools if purpose_key == "act" else None,
            timeout=timeout,
            model=chosen.model,
            identity=chosen.identity,
            measured=True,
        )
        ok = True
        refused = None
        text = result.text
    except LLMError as exc:
        result = LLMResult()
        ok = False
        refused = str(exc)
        text = ""
    latency_ms = (time.monotonic() - started) * 1000.0
    record_measurement(
        chosen.label,
        latency_ms=latency_ms,
        ok=ok,
        cost_usd=float(result.cost_usd or 0.0),
    )
    if not ok:
        return {
            "ok": False,
            "status": "REFUSE",
            "purpose": purpose_key,
            "identity": chosen.identity,
            "route": chosen.as_public(),
            "refused": refused,
            "values": [],
            "arming": arm,
            "live_5000_ci": False,
            "measured_baseline": dict(DMS_180_BASELINE),
        }
    return {
        "ok": True,
        "status": "OK",
        "purpose": purpose_key,
        "identity": chosen.identity,
        "route": chosen.as_public(),
        "text": text,
        "model": result.model or chosen.model,
        "values": [],
        "arming": arm,
        "live_5000_ci": False,
        "measured_baseline": dict(DMS_180_BASELINE),
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }
