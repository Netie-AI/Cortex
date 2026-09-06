"""Slim Cortex Constructor surface when full CortexOS.api.main cannot start."""

from __future__ import annotations

from typing import Any


def create_constructor_app() -> Any:
    from fastapi import FastAPI

    from packs.dms.constructor_routes import register_constructor_routes
    from packs.dms.ontology_routes import register_ontology_routes

    app = FastAPI(title="Cortex Constructor", version="1.0.0")
    register_constructor_routes(app)
    register_ontology_routes(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "pack": "dms", "surface": "constructor"}

    return app


app = create_constructor_app()
