"""FastAPI lifespan helpers (Postgres bootstrap for CostLedger and services)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from netie.db.bootstrap import dispose_engine, init_database_engine
from netie.execution.model_router import ModelRouter
from netie.routing.cost_ledger import CostLedger


def database_lifespan_factory(
    database_url: str | None,
    *,
    state_attribute: str = "db_engine",
):
    """
    Returns an async lifespan handler suitable for::

        FastAPI(lifespan=database_lifespan_factory(settings.database_url))
    """

    @asynccontextmanager
    async def _lifespan(app: Any):
        engine = await init_database_engine(database_url)
        setattr(app.state, state_attribute, engine)
        app.state.ledger = CostLedger(engine=engine)
        app.state.model_router = ModelRouter()
        try:
            yield
        finally:
            await dispose_engine(engine)

    return _lifespan
