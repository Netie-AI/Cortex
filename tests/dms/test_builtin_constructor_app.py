"""Constructor is a hosted Cortex builtin app, not a second process."""

from __future__ import annotations

from CortexOS.execution import app_store


def test_builtin_constructor_is_hosted_and_not_deletable(tmp_path, monkeypatch):
    monkeypatch.setattr(app_store, "DB_PATH", tmp_path / "apps.db")
    monkeypatch.setattr(app_store, "APPS_ROOT", tmp_path / "apps")
    monkeypatch.setenv("CONSTRUCTOR_SKIN_DIR", r"D:\Constructor")
    app_store.init()

    seeded = app_store.ensure_builtin_constructor()
    app = seeded["app"]
    assert app["id"] == app_store.BUILTIN_CONSTRUCTOR_ID
    assert app["status"] == app_store.STATUS_APPROVED
    assert app["run_status"] == "hosted"
    assert app["manifest"]["served_by"] == "cortex"
    assert app["manifest"]["launch_path"] == "/cortex/constructor/"
    assert app["port"] is None

    listed = app_store.list_apps()
    assert any(a["id"] == app_store.BUILTIN_CONSTRUCTOR_ID for a in listed)

    started = app_store.start_app(app_store.BUILTIN_CONSTRUCTOR_ID)
    assert started["ok"] is True
    assert started["hosted"] is True
    assert started["url"] == "/cortex/constructor/"
    assert started["app"]["run_status"] == "hosted"

    assert app_store.delete_app(app_store.BUILTIN_CONSTRUCTOR_ID) is False
    assert app_store.get_app(app_store.BUILTIN_CONSTRUCTOR_ID) is not None
