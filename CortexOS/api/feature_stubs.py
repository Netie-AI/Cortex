"""Register identical HTTP paths that return 501 when an optional extra is missing.

Core and full images must expose the same route table. Behaviour differs;
presence of the path does not.
"""

from __future__ import annotations

from typing import Any

from CortexOS.packaging import FeatureNotInstalled


def feature_not_installed_detail(exc: FeatureNotInstalled) -> dict[str, Any]:
    """JSON body for HTTP 501 — names the extra the operator must install."""
    body: dict[str, Any] = {
        "error": "FeatureNotInstalled",
        "extra": exc.extra,
        "message": str(exc),
    }
    if exc.feature:
        body["feature"] = exc.feature
    if exc.missing:
        body["missing"] = list(exc.missing)
    return body


def register_feature_stubs(
    app: Any,
    *,
    extra: str,
    feature: str,
    routes: list[tuple[str, str]],
) -> None:
    """Register ``(METHOD, path)`` handlers that always respond 501.

    ``routes`` paths must match the real register_* modules so OpenAPI and
    clients see one table across install profiles.
    """
    from fastapi import HTTPException

    detail = feature_not_installed_detail(
        FeatureNotInstalled(extra, feature=feature, missing=())
    )

    async def _stub() -> None:
        raise HTTPException(status_code=501, detail=detail)

    for method, path in routes:
        app.add_api_route(
            path,
            _stub,
            methods=[method.upper()],
            name=f"stub_{extra}_{feature}_{method}_{path}".replace("/", "_"),
            tags=[f"stub:{extra}"],
        )


# Path tables mirrored from the real register_* modules. Keep in sync when
# those modules add or rename routes — tests/packaging asserts key paths exist
# under both CORTEX_PROFILE=core and full.
AGENTIC_STUB_ROUTES: dict[str, list[tuple[str, str]]] = {
    "race_routes": [
        ("POST", "/api/engine/auto"),
        ("GET", "/api/engine/scoreboard"),
        ("GET", "/api/engine/scoreboard/{family}"),
    ],
    "routine_routes": [
        ("GET", "/api/routines"),
        ("POST", "/api/routines/draft"),
        ("POST", "/api/routines"),
        ("POST", "/api/routines/tick"),
        ("POST", "/api/routines/pause-all"),
        ("POST", "/api/routines/resume-all"),
        ("GET", "/api/routines/{rid}"),
        ("PATCH", "/api/routines/{rid}"),
        ("DELETE", "/api/routines/{rid}"),
        ("POST", "/api/routines/{rid}/pause"),
        ("POST", "/api/routines/{rid}/resume"),
        ("POST", "/api/routines/{rid}/run"),
        ("POST", "/api/routines/{rid}/fire"),
        ("GET", "/api/routines/{rid}/runs"),
    ],
    "app_routes": [
        ("POST", "/api/apps/import"),
        ("POST", "/api/apps/import-folder"),
        ("GET", "/api/apps"),
        ("GET", "/api/apps/{app_id}"),
        ("POST", "/api/apps/{app_id}/approve"),
        ("POST", "/api/apps/{app_id}/reject"),
        ("POST", "/api/apps/{app_id}/rescan"),
        ("POST", "/api/apps/{app_id}/start"),
        ("POST", "/api/apps/{app_id}/stop"),
        ("POST", "/api/apps/{app_id}/dockerize"),
        ("DELETE", "/api/apps/{app_id}"),
    ],
    "activity_routes": [
        ("GET", "/api/engine/activity"),
    ],
    "goal_routes": [
        ("GET", "/api/goals"),
        ("POST", "/api/goals"),
        ("GET", "/api/goals/{goal_id}"),
        ("PATCH", "/api/goals/{goal_id}"),
        ("DELETE", "/api/goals/{goal_id}"),
        ("GET", "/api/goals/{goal_id}/seeks"),
        ("POST", "/api/goals/{goal_id}/outcome"),
        ("GET", "/api/commitments"),
        ("POST", "/api/commitments/scan"),
        ("POST", "/api/commitments/{cid}/close"),
        ("POST", "/api/commitments/{cid}/dismiss"),
        ("GET", "/api/engine/telemetry"),
        ("POST", "/api/engine/telemetry/compact"),
        ("GET", "/api/goals/{goal_id}/values"),
        ("POST", "/api/engine/osr"),
        ("POST", "/api/engine/seek"),
    ],
}

RAG_STUB_ROUTES: list[tuple[str, str]] = [
    ("POST", "/search"),
]

AGENTIC_DAG_STUB_ROUTES: list[tuple[str, str]] = [
    ("POST", "/run"),
    ("GET", "/api/engine/runs/{run_id}/cost"),
]
