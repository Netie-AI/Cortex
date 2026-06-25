"""Re-export agents from the active vertical pack."""

from __future__ import annotations

import importlib


def _load_agent_exports():
    from netie.config import get_config

    pack_name = get_config().pack
    mod = importlib.import_module(f"packs.{pack_name}.agents")
    return mod


_mod = _load_agent_exports()
BuyerAgent = _mod.BuyerAgent
SellerAgent = _mod.SellerAgent
CloserAgent = _mod.CloserAgent
ComplianceAgent = _mod.ComplianceAgent

__all__ = ["BuyerAgent", "SellerAgent", "CloserAgent", "ComplianceAgent"]
