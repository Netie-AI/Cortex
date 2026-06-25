"""Uvicorn entrypoint: ``PACK=dms uvicorn CortexOS.api.main:app``."""

from CortexOS.api.app import create_app

app = create_app()

__all__ = ["app"]
