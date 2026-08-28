"""Execution package.

Base install keeps this package importable. Optional agentic submodules
(DAG / OSR / seeker / routines / commitments) live behind the ``agentic``
extra and are imported by callers that need them.
"""

from __future__ import annotations

from typing import Any

__all__ = ["execute_run_plan"]


def __getattr__(name: str) -> Any:
    if name == "execute_run_plan":
        from CortexOS.packaging import require_extra

        require_extra("agentic", feature="execute_run_plan")
        try:
            from .run_plan import execute_run_plan as _fn
        except ImportError as exc:  # pragma: no cover - submodule may be absent on slim checkouts
            raise ImportError(
                "execute_run_plan requires CortexOS.execution.run_plan "
                "(install netie[agentic] on a full engine tree)."
            ) from exc
        globals()[name] = _fn
        return _fn
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
