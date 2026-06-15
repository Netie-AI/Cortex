"""FastAPI application shell (DB bootstrap on lifespan)."""

from __future__ import annotations

from typing import Any

from netie.config import get_config
from netie.db.lifespan import database_lifespan_factory


def create_app() -> Any:
    try:
        from fastapi import FastAPI, Request
    except ImportError as exc:  # pragma: no cover
        raise ImportError('Install API extras: pip install "netie[api]"') from exc

    cfg = get_config()
    app = FastAPI(
        title="Cortex Netie",
        version="0.2.0",
        lifespan=database_lifespan_factory(cfg.database_url),
    )

    from netie.api.search import register_search_routes
    from netie.api.dag_run import register_dag_run_routes

    register_search_routes(app)
    register_dag_run_routes(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/db")
    async def health_db(request: Request) -> dict[str, bool]:
        eng = getattr(request.app.state, "db_engine", None)
        return {"postgres_configured": eng is not None}

    return app


__all__ = ["create_app"]
