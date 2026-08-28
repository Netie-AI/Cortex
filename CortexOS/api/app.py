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
        version="2.5.0",
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

    import importlib

    from netie.api.context_routes import register_context_routes
    from netie.api.dag_run import register_dag_run_routes
    from netie.api.engine_routes import register_engine_routes
    from netie.api.memory_routes import register_memory_routes
    from netie.api.search import register_search_routes

    from CortexOS import __version__ as engine_version
    from CortexOS.api.connector_routes import register_connector_routes
    from CortexOS.api.contract_routes import register_contract_routes
    from CortexOS.api.feature_stubs import (
        AGENTIC_STUB_ROUTES,
        register_feature_stubs,
    )
    from CortexOS.packaging import extra_available

    register_contract_routes(app)
    register_connector_routes(app)
    register_search_routes(app)
    register_dag_run_routes(app)
    register_engine_routes(app)
    register_memory_routes(app)
    register_context_routes(app)

    # Optional route modules — always present in the route table. Import failure
    # or a core profile registers the same paths as HTTP 501 stubs.
    for _mod, _reg, _extra, _feature, _stubs in (
        ("CortexOS.api.workflow_routes", "register_workflow_routes", "agentic", "workflows", []),
        ("CortexOS.api.telemetry_routes", "register_telemetry_routes", "agentic", "telemetry", []),
        ("CortexOS.api.mcp_routes", "register_mcp_routes", "agentic", "mcp", []),
        ("CortexOS.api.discovery_routes", "register_discovery_routes", "agentic", "discovery", []),
    ):
        try:
            getattr(importlib.import_module(_mod), _reg)(app)
        except ImportError:
            if _stubs:
                register_feature_stubs(app, extra=_extra, feature=_feature, routes=_stubs)

    for _mod, _reg, _key in (
        ("CortexOS.api.race_routes", "register_race_routes", "race_routes"),
        ("CortexOS.api.routine_routes", "register_routine_routes", "routine_routes"),
        ("CortexOS.api.app_routes", "register_app_routes", "app_routes"),
        ("CortexOS.api.activity_routes", "register_activity_routes", "activity_routes"),
        ("CortexOS.api.goal_routes", "register_goal_routes", "goal_routes"),
    ):
        stubs = AGENTIC_STUB_ROUTES[_key]
        if extra_available("agentic"):
            try:
                getattr(importlib.import_module(_mod), _reg)(app)
                continue
            except ImportError:
                pass
        register_feature_stubs(app, extra="agentic", feature=_key, routes=stubs)

    if pack.name == "dms":
        from netie.api.action_routes import register_action_routes
        from netie.api.agent_routes import register_agent_routes
        from netie.api.brain_routes import register_brain_routes
        from netie.api.dms_query import register_dms_routes
        from netie.api.ingest_routes import register_ingest_routes
        from netie.api.lakehouse_routes import register_lakehouse_routes
        from netie.api.pipeline_routes import register_pipeline_routes
        from netie.api.sidecar_routes import register_sidecar_routes
        from netie.api.skill_routes import register_skill_routes
        from netie.api.stream_routes import register_stream_routes
        from netie.api.task_routes import register_task_routes

        register_dms_routes(app)
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
        from packs.dms.constructor_routes import register_constructor_routes

        register_constructor_routes(app)
        try:
            from CortexOS.api.a2a_routes import register_a2a_routes

            register_a2a_routes(app)
        except ImportError:
            register_feature_stubs(
                app,
                extra="agentic",
                feature="a2a",
                routes=[("POST", "/a2a/messages")],
            )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "pack": pack.name}

    @app.get("/health/db")
    async def health_db(request: Request) -> dict[str, bool]:
        eng = getattr(request.app.state, "db_engine", None)
        return {"postgres_configured": eng is not None}

    @app.get("/health/features")
    async def health_features(request: Request) -> dict[str, Any]:
        """Installed extras + engine version — used to prove -core ≠ -full images."""
        from netie.config import get_config

        cfg = get_config()
        dense_retriever = getattr(request.app.state, "dense_retriever", None)
        return {
            "engine_version": engine_version,
            "profile": __import__("os").environ.get("CORTEX_PROFILE", "") or "default",
            "extras": {
                "agentic": extra_available("agentic"),
                "rag": extra_available("rag"),
                "full": extra_available("full"),
            },
            "rag": {
                "dense_available": dense_retriever is not None,
                "qdrant_configured": bool(getattr(cfg, "qdrant_url", None)),
            },
        }

    return app


__all__ = ["create_app"]
