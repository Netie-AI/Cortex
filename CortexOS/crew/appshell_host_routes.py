"""GET-only AppShell host mount. No from __future__ import annotations.

Engine and Crew both serve ``GET /appshell`` chrome so a private tunnel is
not the only path. Control stays F-0030. OpenAPI stays unpolluted
(``include_in_schema=False``) -- this is not a cortex-contract bump.
"""

from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

UI_DIR = Path(__file__).resolve().parent / "ui"


def _ui_file(name: str) -> Path:
    return UI_DIR / name


def _engine_url() -> str:
    import os

    return (os.environ.get("CREW_ENGINE_URL") or "http://127.0.0.1:8010").rstrip("/")


def mount_appshell_host_api(router: Any) -> None:
    """Crew ``/crew`` router: host law + live prove. GET only."""

    @router.get("/appshell/host")
    async def appshell_host_map() -> dict[str, Any]:
        from CortexOS.crew import appshell_host as host

        html_path = _ui_file("index.html")
        html = html_path.read_text(encoding="utf-8") if html_path.is_file() else ""
        return host.public_map(html)

    @router.get("/appshell/host/live")
    async def appshell_host_live() -> dict[str, Any]:
        from CortexOS.crew import appshell_host as host

        return host.prove_live()

    @router.post("/appshell/host")
    async def appshell_host_post() -> Any:
        raise HTTPException(405, "AppShell host prove is GET only. Control is F-0030.")

    @router.post("/appshell/host/live")
    async def appshell_host_live_post() -> Any:
        raise HTTPException(405, "AppShell host prove is GET only. Control is F-0030.")


def mount_appshell_page(app: Any) -> None:
    """``GET /appshell`` -- documented Netie host path, same chrome as Crew ``/``."""

    @app.get("/appshell", include_in_schema=False)
    async def appshell_page() -> Any:
        index = _ui_file("index.html")
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"ok": False, "detail": "UI file missing (CortexOS/crew/ui/index.html)"},
            503,
        )


def mount_engine_appshell(app: Any) -> None:
    """Buyer host path on the Cortex engine (same host as ``/cortex``).

    Serves AppShell chrome + GET-only catalog/control/audit/host. Does not
    mount Crew converse/spawn. Does not POST run/goal/route/secrets.
    """
    from fastapi import APIRouter

    from CortexOS.crew import appshell as appshell_mod

    mount_appshell_page(app)

    chrome = _ui_file("crew.css")
    manifest = _ui_file("appshell.webmanifest")

    @app.get("/crew.css", include_in_schema=False)
    async def engine_crew_css() -> Any:
        if chrome.is_file():
            return FileResponse(chrome, media_type="text/css")
        return JSONResponse({"ok": False, "detail": "crew.css missing"}, 503)

    @app.get("/appshell.webmanifest", include_in_schema=False)
    async def engine_appshell_manifest() -> Any:
        if manifest.is_file():
            return FileResponse(manifest, media_type="application/manifest+json")
        return JSONResponse({"ok": False, "detail": "appshell.webmanifest missing"}, 503)

    router = APIRouter(prefix="/crew", include_in_schema=False)

    @router.get("/appshell")
    async def engine_appshell_catalog() -> dict[str, Any]:
        return appshell_mod.catalog(engine_url=_engine_url())

    @router.get("/appshell/health")
    async def engine_appshell_health() -> dict[str, Any]:
        return appshell_mod.health(engine_url=_engine_url())

    @router.get("/appshell/control")
    async def engine_appshell_control(path: str | None = None) -> dict[str, Any]:
        body = appshell_mod.control_display(path=path)
        if body.get("refused"):
            raise HTTPException(403, body.get("reason") or "not a Control display GET")
        return body

    @router.get("/appshell/audit")
    async def engine_appshell_audit() -> dict[str, Any]:
        return appshell_mod.audit_payload()

    @router.post("/appshell/control")
    async def engine_appshell_control_post() -> Any:
        raise HTTPException(405, "Control is display-only F-0030. GET only.")

    @router.post("/appshell/spawn")
    async def engine_appshell_spawn_refused() -> dict[str, Any]:
        body = appshell_mod.refuse_control_spawn()
        raise HTTPException(403, body["reason"])

    mount_appshell_host_api(router)
    app.include_router(router)
