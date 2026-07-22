import pytest

pytest.importorskip("fastapi")


def test_create_app_exposes_health_routes():
    from netie.api.app import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/health" in paths
    assert "/health/db" in paths
