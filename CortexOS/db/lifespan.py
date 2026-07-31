"""FastAPI lifespan helpers (Postgres bootstrap for CostLedger and services)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from netie.db.bootstrap import dispose_engine, init_database_engine
from netie.execution.model_router import ModelRouter
from netie.routing.cost_ledger import CostLedger

_LOG = logging.getLogger("cortex.lifespan")


def _refresh_jwks_at_startup() -> None:
    """Cold-path JWKS refresh so the first live mint can verify.

    Failure is not fatal: an on-disk cache may still be warm. Hot-path verify
    never calls the network. Uses the shared submit verifier cache so the
    in-memory set matches what session_bind will read.
    """
    try:
        from CortexOS.execution.submit import get_verifier

        cache = get_verifier().cache
        ok = cache.refresh(timeout=2.0)
        if ok:
            _LOG.info("JWKS refreshed from OpenVault (%d keys)", len(cache.known_kids))
        else:
            # Ensure disk cache is loaded even when the network fetch fails.
            n = len(cache.known_kids)
            _LOG.warning(
                "JWKS refresh skipped or empty; using cached keys if any (%d)",
                n,
            )
    except Exception:  # noqa: BLE001 — never block API boot on OpenVault
        _LOG.exception("JWKS startup refresh failed; continuing with cache if present")


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
        _refresh_jwks_at_startup()
        try:
            yield
        finally:
            await dispose_engine(engine)

    return _lifespan
