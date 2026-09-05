"""Constructor distill-options registry (RSF-01).

Orchestrator-of-orchestrators contract: this is a meta-router route table,
not a Constructor embed and not a third orchestrator daemon.

Option ids may be distill_only / compete / learn. They are never
product_engine. Cortex Constructor remains the only product engine.

myn8n / langchain / langflow are listed so we can distill and compete against
them. Do not vendor those upstream trees into Constructor.

gencfsm_dag routes at the existing Cortex compile path
``CortexOS.execution.gen_cfsm`` → ``CortexOS.execution.dag_runner``.

Egress for option runs is OpenVault FreeRoute (config key / path only).
OmniRoute on :20128 is vendor/study (DR-0003 / TAS-OPENVAULT §10) — do not
vendor it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

EngineRole = Literal["distill_only", "compete", "learn"]

ALLOWED_ENGINE_ROLES: frozenset[str] = frozenset({"distill_only", "compete", "learn"})
PRODUCT_ENGINE_ROLE = "product_engine"

# Upstream packages that must never be imported as Constructor engine.
# CortexOS.execution.gen_cfsm is Cortex's own module and is allowed.
BANNED_ENGINE_IMPORT_PREFIXES: tuple[str, ...] = (
    "n8n",
    "langchain",
    "langchain_core",
    "langchain_community",
    "langflow",
    "langgraph",
    "gencfsm",
)

REQUIRED_OPTION_IDS: tuple[str, ...] = (
    "myn8n",
    "langchain",
    "langflow",
    "gencfsm_dag",
)

# OpenVault FreeRoute — comment + config key only. Do not vendor :20128.
EGRESS_CONFIG_KEY = "OPENVAULT_URL"
FREEROUTE_PATH = "/api/freeroute/ratelimit"
OMNIROUTE_VENDOR_PORT = 20128

_EGRESS = {
    "config_key": EGRESS_CONFIG_KEY,
    "freeroute_path": FREEROUTE_PATH,
    "note": (
        "Option-run egress is OpenVault FreeRoute via OPENVAULT_URL. "
        "OmniRoute :20128 stays vendor/study; do not vendor it."
    ),
}

_GENCFSM_ROUTE = {
    "kind": "meta_router_route",
    "module": "CortexOS.execution.gen_cfsm",
    "via": "CortexOS.execution.dag_runner",
    "note": "Existing Cortex compile path. No third orchestrator daemon.",
}

_DISTILL_ROUTE = {
    "kind": "meta_router_route",
    "module": None,
    "via": None,
    "note": "Distill-only analog. Not a Constructor engine import.",
}


def _option(
    option_id: str,
    *,
    name: str,
    engine_role: EngineRole,
    route: dict[str, Any],
    blurb: str,
) -> dict[str, Any]:
    return {
        "id": option_id,
        "name": name,
        "engine_role": engine_role,
        "adapter": "meta_router",
        "route": dict(route),
        "egress": dict(_EGRESS),
        "blurb": blurb,
    }


_BUILTIN: list[dict[str, Any]] = [
    _option(
        "myn8n",
        name="n8n (distill)",
        engine_role="distill_only",
        route=_DISTILL_ROUTE,
        blurb="Analog workflow UI to distill. Never the Constructor engine.",
    ),
    _option(
        "langchain",
        name="LangChain (distill)",
        engine_role="distill_only",
        route=_DISTILL_ROUTE,
        blurb="Analog chain/tool runtime to distill. Never the Constructor engine.",
    ),
    _option(
        "langflow",
        name="LangFlow (distill)",
        engine_role="distill_only",
        route=_DISTILL_ROUTE,
        blurb="Analog flow canvas to distill. Never the Constructor engine.",
    ),
    _option(
        "gencfsm_dag",
        name="gen-cFSM DAG (learn)",
        engine_role="learn",
        route=_GENCFSM_ROUTE,
        blurb="Learn/bake-off via CortexOS.execution.gen_cfsm → dag_runner.",
    ),
]

_REGISTRY: list[dict[str, Any]] = [dict(row) for row in _BUILTIN]


def _validate(entry: Mapping[str, Any]) -> None:
    option_id = entry.get("id")
    if not isinstance(option_id, str) or not option_id.strip():
        raise ValueError("distill option needs a string id")
    role = entry.get("engine_role")
    if role == PRODUCT_ENGINE_ROLE or role not in ALLOWED_ENGINE_ROLES:
        raise ValueError(
            f"{option_id!r} engine_role must be one of {sorted(ALLOWED_ENGINE_ROLES)}; "
            "never product_engine"
        )
    adapter = entry.get("adapter")
    if adapter != "meta_router":
        raise ValueError(f"{option_id!r} adapter must be meta_router (route table, not a daemon)")


def catalog() -> list[dict[str, Any]]:
    return [dict(row) for row in _REGISTRY]


def option_ids() -> frozenset[str]:
    return frozenset(str(row["id"]) for row in _REGISTRY)


def get_option(option_id: str) -> dict[str, Any]:
    for row in _REGISTRY:
        if row["id"] == option_id:
            return dict(row)
    raise KeyError(option_id)


def register_option(entry: Mapping[str, Any]) -> None:
    """RSF-03/04/06 hook: extra distill options. Does not start those slices."""
    _validate(entry)
    oid = str(entry["id"])
    if any(row["id"] == oid for row in _REGISTRY):
        raise ValueError(f"distill option {oid!r} already registered")
    _REGISTRY.append(dict(entry))


def reset_registry() -> None:
    """Test helper. Restores the built-in table."""
    _REGISTRY.clear()
    _REGISTRY.extend(dict(row) for row in _BUILTIN)


def is_banned_engine_import(module: str) -> bool:
    """True if ``module`` would mean shipping an analog as Constructor engine."""
    if module == "CortexOS.execution.gen_cfsm" or module.startswith(
        "CortexOS.execution.gen_cfsm."
    ):
        return False
    root = module.split(".", 1)[0]
    if root.startswith("langchain"):
        return True
    return any(root == prefix or module.startswith(prefix + ".") for prefix in BANNED_ENGINE_IMPORT_PREFIXES)


for _row in _BUILTIN:
    _validate(_row)
