"""Uvicorn entrypoint: ``PACK=ruma uvicorn netie.api.main:app``."""

from netie.api.app import create_app

app = create_app()

__all__ = ["app"]
