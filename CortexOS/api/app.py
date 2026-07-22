"""FastAPI application shell (DB bootstrap on lifespan)."""

from typing import Any

from netie.config import get_config
from netie.db.lifespan import database_lifespan_factory
from netie.packs.loader import load_pack, resolve_pack_dir


def create_app() -> Any:
    try:
        from fastapi import FastAPI, Request
    except ImportError as exc:  # pragma: no cover
        raise ImportError('Install API extras: pip install "netie[api]"') from exc

    cfg = get_config()
    pack = load_pack(cfg.pack, resolve_pack_dir(cfg.pack_dir))
    app = FastAPI(
        title="Cortex Netie",
        version="0.2.0",
        lifespan=database_lifespan_factory(cfg.database_url),
    )
    app.state.pack = pack

    if pack.name == "dms":
        try:
            from fastapi.middleware.cors import CORSMiddleware

            app.add_middleware(
                CORSMiddleware,
                allow_origins=[
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                    "http://localhost:8765",
                    "http://127.0.0.1:8765",
                ],
                allow_methods=["*"],
                allow_headers=["*"],
            )
        except ImportError:
            pass

        from packs.dms.security.rate_limit import DMSRateLimitMiddleware

        app.add_middleware(DMSRateLimitMiddleware)

    from netie.api.search import register_search_routes
    from netie.api.dag_run import register_dag_run_routes
    from netie.api.engine_routes import register_engine_routes
    from netie.api.memory_routes import register_memory_routes

    register_search_routes(app)
    register_dag_run_routes(app)
    register_engine_routes(app)
    register_memory_routes(app)

    if pack.name == "dms":
        from netie.api.dms_query import register_dms_routes
        from netie.api.chat_routes import register_chat_routes
        from netie.api.brain_routes import register_brain_routes
        from netie.api.task_routes import register_task_routes
        from netie.api.skill_routes import register_skill_routes
        from netie.api.sidecar_routes import register_sidecar_routes
        from netie.api.lakehouse_routes import register_lakehouse_routes
        from netie.api.ingest_routes import register_ingest_routes
        from netie.api.pipeline_routes import register_pipeline_routes
        from netie.api.stream_routes import register_stream_routes
        from netie.api.agent_routes import register_agent_routes
        from netie.api.action_routes import register_action_routes

        register_dms_routes(app)
        register_chat_routes(app)
        register_brain_routes(app)
        register_task_routes(app)
        register_skill_routes(app)
        register_sidecar_routes(app)
        register_lakehouse_routes(app)
        register_ingest_routes(app)
        register_pipeline_routes(app)
        register_stream_routes(app)
        register_agent_routes(app)
        register_action_routes(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "pack": pack.name}

    @app.get("/health/db")
    async def health_db(request: Request) -> dict[str, bool]:
        eng = getattr(request.app.state, "db_engine", None)
        return {"postgres_configured": eng is not None}

    return app


__all__ = ["create_app"]
