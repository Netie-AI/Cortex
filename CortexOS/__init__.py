"""Cortex / Netie engine package.

Engine version tracks G-gates (see ``docs/RELEASING.md``). Independent of
``cortex_contract.CONTRACT_VERSION``.
"""

__version__ = "2.5.0"

from CortexOS.packaging import FeatureNotInstalled, require_extra

__all__ = ["__version__", "FeatureNotInstalled", "require_extra"]
