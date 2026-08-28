"""OpenTelemetry-shaped run traces. Identifiers and verdicts, not inbox prose."""

from __future__ import annotations

from typing import Any


def span(*, run_id: str, invocation_id: str, node: str, status: str) -> dict[str, Any]:
    return {
        "trace": "night_shift",
        "run_id": run_id,
        "invocation_id": invocation_id,
        "node": node,
        "status": status,
    }
