"""Register AppShell buyer host path on the engine (same host as /cortex).

Public URL (existing host, not a new name): https://app.netie.ai/appshell
Local: http://127.0.0.1:8010/appshell

CortexOS/api must not import CortexOS.crew (FreeRoute core bound). The pack
registers the Crew chrome mount into the engine -- packs -> CortexOS is the
allowed direction. Control stays GET F-0030.
"""

from typing import Any


def register_appshell_host_routes(app: Any) -> None:
    from CortexOS.crew.appshell_host_routes import mount_engine_appshell

    mount_engine_appshell(app)
