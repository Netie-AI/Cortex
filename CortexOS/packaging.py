"""Optional install profiles for Cortex / netie.

Profiles (see ``pyproject.toml`` / ``docs/RELEASING.md``):

- **base** — answer engine, execution primitives, ledger, F5/F7, semantic layer,
  context assembly
- **agentic** — DAG, OSR, seeker, routines, commitments
- **rag** — embeddings + document index (Qdrant / tantivy / sentence-transformers)
- **full** — agentic + rag
"""

from __future__ import annotations

import importlib.util
import os
from typing import Iterable


class FeatureNotInstalled(ImportError):
    """Raised when code needs an optional install extra that is not present."""

    def __init__(
        self,
        extra: str,
        *,
        feature: str | None = None,
        missing: Iterable[str] = (),
    ) -> None:
        self.extra = extra
        self.feature = feature
        self.missing = tuple(missing)
        label = f"'{extra}'"
        if feature:
            label = f"{label} (feature: {feature})"
        msg = (
            f"Optional extra {label} is not installed. "
            f"Install with: pip install 'netie[{extra}]' "
            f"(or pip install 'netie[full]')."
        )
        if self.missing:
            msg += f" Missing: {', '.join(self.missing)}."
        super().__init__(msg)


# Third-party modules that must be importable when the named extra is installed.
_EXTRA_MODULES: dict[str, tuple[str, ...]] = {
    "rag": ("qdrant_client", "tantivy", "sentence_transformers"),
    # Agentic is mostly first-party; ``dbos`` is the optional third-party marker
    # when the agentic extra is installed. Core/base profile forces agentic off.
    "agentic": ("dbos",),
}


def _profile() -> str:
    return os.environ.get("CORTEX_PROFILE", "").strip().lower()


def _modules_missing(modules: tuple[str, ...]) -> list[str]:
    missing: list[str] = []
    for name in modules:
        try:
            spec = importlib.util.find_spec(name)
        except (ModuleNotFoundError, ValueError):
            # ValueError: mocked modules in tests may lack a real __spec__.
            missing.append(name)
            continue
        if spec is None:
            missing.append(name)
    return missing


def extra_available(extra: str) -> bool:
    """Return whether the named optional extra appears available at runtime."""
    profile = _profile()
    if profile in {"core", "base"}:
        if extra in {"agentic", "rag", "full"}:
            return False
    if extra == "full":
        return extra_available("agentic") and extra_available("rag")

    modules = _EXTRA_MODULES.get(extra)
    if modules is None:
        return True

    if extra == "agentic":
        # Default: first-party agentic surface is available when not in core
        # profile. Strict marker mode (Docker / CI) requires dbos from [agentic].
        if os.environ.get("CORTEX_REQUIRE_AGENTIC_MARKER", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            return not _modules_missing(modules)
        return True

    return not _modules_missing(modules)


def require_extra(extra: str, *, feature: str | None = None) -> None:
    """Raise :class:`FeatureNotInstalled` if ``extra`` is not available."""
    if extra_available(extra):
        return
    modules = _EXTRA_MODULES.get(extra, ())
    missing = _modules_missing(modules)
    if _profile() in {"core", "base"} and not missing:
        missing = [f"CORTEX_PROFILE={_profile()}"]
    raise FeatureNotInstalled(extra, feature=feature, missing=missing)


def lazy_import(module: str, *, extra: str, feature: str | None = None):
    """Import ``module`` after asserting ``extra`` is installed."""
    require_extra(extra, feature=feature or module)
    import importlib

    return importlib.import_module(module)
