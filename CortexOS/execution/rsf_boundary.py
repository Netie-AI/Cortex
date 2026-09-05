"""RSF-03 boundary gates: OpenVault leave-machine + fair-code study trees.

Research egress uses existing OpenVault ``check_gate`` (action=leave,
destination=freeroute). Unreachable OpenVault fails closed. OmniRoute :20128
is refused, never vendored.

Distill may list analog study trees (D:\\myn8n, langchain, langflow) but
must not promote them to Constructor ``product_engine``. RSF-06 owns the
distill harness; this slice only names the trees and keeps the role lock.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from CortexOS.execution.distill_options import (
    OMNIROUTE_VENDOR_PORT,
    PRODUCT_ENGINE_ROLE,
    get_option,
)

BAN_FLOORS: tuple[str, ...] = (
    "silent upstream engine",
    "skip OpenVault",
    "n8n/langchain/langflow as product_engine",
    "vendor OmniRoute :20128",
)

STUDY_TREE_OPTION_IDS: frozenset[str] = frozenset({"myn8n", "langchain", "langflow"})

_DEFAULT_STUDY_TREES: dict[str, Path] = {
    "myn8n": Path("D:/myn8n"),
    "langchain": Path("D:/langchain"),
    "langflow": Path("D:/langflow"),
}

_SAMPLE_CAP = 8


def is_omniroute_destination(destination: str) -> bool:
    blob = (destination or "").strip().lower()
    if not blob:
        return False
    if str(OMNIROUTE_VENDOR_PORT) in blob:
        return True
    return "omniroute" in blob


def study_tree_root(option_id: str, *, root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ.get(f"RSF_STUDY_TREE_{option_id.upper()}")
    if env:
        return Path(env)
    return _DEFAULT_STUDY_TREES[option_id]


def gate_research_egress(*, destination: str = "freeroute") -> dict[str, Any]:
    """Ask OpenVault leave-machine. Prefer FreeRoute. Fail closed.

    Does not fetch. RSF-04 owns the orchestrator hop after this gate.
    """
    dest = (destination or "").strip() or "freeroute"
    if is_omniroute_destination(dest):
        return {
            "ok": False,
            "allowed": False,
            "action": "leave",
            "requested": dest,
            "destination": "freeroute",
            "route": "omniroute_gated",
            "reasons": [
                "OmniRoute is gated; prefer FreeRoute. "
                f"Do not vendor :{OMNIROUTE_VENDOR_PORT}."
            ],
        }

    from CortexOS.integrations.openvault_gate import check_gate

    try:
        gate = check_gate(
            action="leave",
            destination="freeroute",
            required_providers=[],
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "allowed": False,
            "action": "leave",
            "requested": dest,
            "destination": "freeroute",
            "route": "freeroute",
            "reasons": [f"gate error: {exc}"[:240]],
        }

    allowed = gate.get("allowed") is True
    reasons = [str(r) for r in (gate.get("reasons") or [])]
    if not allowed and not reasons:
        reasons = ["leave-machine gate denied"]
    return {
        "ok": bool(gate.get("ok")) and allowed,
        "allowed": allowed,
        "action": "leave",
        "requested": dest,
        "destination": "freeroute",
        "route": "freeroute",
        "reasons": reasons,
        "openvault_url": gate.get("openvault_url"),
    }


def read_study_tree(option_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """List a distill analog tree. Never promotes ``engine_role`` to product_engine."""
    option = get_option(option_id)
    role = option["engine_role"]
    if role == PRODUCT_ENGINE_ROLE or (
        option_id in STUDY_TREE_OPTION_IDS and role != "distill_only"
    ):
        return {
            "ok": False,
            "allowed": False,
            "id": option_id,
            "engine_role": "distill_only",
            "reasons": ["study tree must not be Constructor product_engine"],
        }
    if option_id not in STUDY_TREE_OPTION_IDS:
        return {
            "ok": False,
            "allowed": False,
            "id": option_id,
            "engine_role": role if role != PRODUCT_ENGINE_ROLE else "distill_only",
            "reasons": ["not a distill study analog"],
        }

    path = study_tree_root(option_id, root=root)
    exists = path.is_dir()
    samples: list[str] = []
    if exists:
        # Names only. Do not import analog packages. RSF-06 distills later.
        try:
            samples = sorted(p.name for p in path.iterdir())[:_SAMPLE_CAP]
        except OSError as exc:
            return {
                "ok": False,
                "allowed": False,
                "id": option_id,
                "engine_role": "distill_only",
                "path": str(path),
                "exists": False,
                "samples": [],
                "reasons": [f"study tree unreadable: {exc}"[:240]],
            }

    return {
        "ok": True,
        "allowed": True,
        "id": option_id,
        "engine_role": "distill_only",
        "path": str(path),
        "exists": exists,
        "samples": samples,
        "reasons": [],
    }
