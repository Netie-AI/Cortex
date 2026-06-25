"""Backward-compatible import alias: ``netie.*`` resolves to ``CortexOS.*``."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CORTEX_ROOT = _ROOT / "CortexOS"


class _NetieAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path, target=None):
        if fullname != "netie" and not fullname.startswith("netie."):
            return None
        cortex_name = fullname.replace("netie", "CortexOS", 1)
        try:
            cortex_spec = importlib.util.find_spec(cortex_name)
        except (ModuleNotFoundError, ValueError, AttributeError):
            return None
        if cortex_spec is None:
            return None
        return importlib.util.spec_from_loader(
            fullname,
            _NetieAliasLoader(cortex_name, cortex_spec),
            is_package=cortex_spec.submodule_search_locations is not None,
        )


class _NetieAliasLoader(importlib.abc.Loader):
    def __init__(self, cortex_name: str, cortex_spec) -> None:
        self.cortex_name = cortex_name
        self.cortex_spec = cortex_spec

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        cortex_mod = importlib.import_module(self.cortex_name)
        # Alias submodules to the same module object so mutable state (e.g. config cache) stays in sync.
        sys.modules[module.__name__] = cortex_mod


if not any(isinstance(f, _NetieAliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _NetieAliasFinder())

import CortexOS as _cortex  # noqa: E402

__path__ = _cortex.__path__
__doc__ = _cortex.__doc__
__all__ = getattr(_cortex, "__all__", [])
