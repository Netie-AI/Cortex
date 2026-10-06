"""C-LOOP (#291) — analysis loop runner and tool registry. Off unless ``CORTEX_ANALYSIS_LOOP``."""

from __future__ import annotations

import os

ENABLED_ENV = "CORTEX_ANALYSIS_LOOP"


def analysis_loop_enabled() -> bool:
    return (os.environ.get(ENABLED_ENV) or "").strip().lower() in {"1", "true", "on", "yes"}


__all__ = ["ENABLED_ENV", "analysis_loop_enabled"]
