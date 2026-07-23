"""Engine-canonical ontology registry (O1/O4) — pack-agnostic.

The YAML source of truth lives per pack (``packs/<pack>/ontology/*.yaml``); this
engine module is the one loader/compiler every pack shares. ``packs/dms/ontology``
is a thin binding kept for compatibility. See docs/strategy/CORTEX_FINAL_GOAL.md:
the engine owns capability, packs are consumer apps on top.
"""

from CortexOS.ontology.registry import (
    ActionType,
    FunctionDef,
    LinkType,
    ObjectType,
    Property,
    compile_to_sqlite,
    default_pack,
    load_action_types,
    load_functions,
    load_link_types,
    load_object_types,
    pack_dir_for,
)

__all__ = [
    "ActionType",
    "FunctionDef",
    "LinkType",
    "ObjectType",
    "Property",
    "compile_to_sqlite",
    "default_pack",
    "load_action_types",
    "load_functions",
    "load_link_types",
    "load_object_types",
    "pack_dir_for",
]
