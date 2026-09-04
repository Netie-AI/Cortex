"""Crew HTTP surface - standalone server and mountable router.

Standalone: ``python -m CortexOS.crew.server`` (default port 8020; the engine
owns 8010 and 8765 is reserved for AirGPT). To embed the same surface in
another FastAPI host (AirGPT), build a :class:`CrewApp` and mount
:func:`build_router` - the crew was written to be lifted, not forked.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from CortexOS.crew.config import CrewSettings, load_settings, resolve_providers
from CortexOS.crew.engine_bridge import EngineBridge
from CortexOS.crew.events import EventBus
from CortexOS.crew.keys import public_fields
from CortexOS.crew.keys import save as save_keys
from CortexOS.crew.keys import status as keys_status
from CortexOS.crew.mcp_client import MCPManager
from CortexOS.crew.queue import JobQueue
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.shell import CrewShell
from CortexOS.crew.store import CrewStore
from CortexOS.crew.wakes import WakeBoard, conveyor

SSE_KEEPALIVE_S = 25
SSE_BREAK = '\n\n'


def _loss_frame(crew: CrewApp, sub: Any) -> str:
    """A resync frame when this client missed events, otherwise nothing.

    The bus caps a slow client's queue rather than blocking a run. The
    client has to be told, or it renders a transcript with holes in it and
    looks perfectly healthy while doing so.
    """
    lost = sub.take_loss()
    if not lost:
        return ""
    return crew.bus.sse("resync", {"dropped": lost})


class CrewApp:
    """One bundle of crew state, shared by every route."""

    def __init__(
        self,
        settings: CrewSettings | None = None,
        llm_chat: Any = None,
        bridge: EngineBridge | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        from CortexOS.crew.keys import apply_saved

        apply_saved(self.settings.data_dir)
        self.store = CrewStore(self.settings.db_path)
        self.bus = EventBus()
        self.bridge = bridge or EngineBridge(
            self.settings.engine_url, self.settings.engine_session
        )
        self.mcp = MCPManager(
            self.settings.mcp_config_path, self.settings.master_computer_control
        )
        self.runtime = CrewRuntime(
            self.store, self.bus, self.settings, self.mcp, self.bridge, llm_chat=llm_chat
        )
        self.shell = CrewShell(self.settings)
        self.wakes = WakeBoard()
        self.queue = JobQueue()

    def mailbox_nonempty(self) -> bool:
        """True when any A2A mailbox still holds unread envelopes.

        Derived flag only. Does not allocate a second mailbox.
        """
        switch = getattr(self.runtime, "switch", None)
        if switch is None:
            return False
        return bool(switch.any_waiting())

    def belt(self) -> dict[str, Any]:
        return conveyor(
            self.store,
            self.wakes,
            self.queue,
            mailbox_nonempty=self.mailbox_nonempty(),
        )

    async def startup(self) -> None:
        from CortexOS.crew.openvault import (
            disable_seeded_cortex_primary,
            ingest_cursor_from_files,
            push_env_keys,
        )

        ingest_cursor_from_files(self.settings.data_dir.parent.parent)
        push_env_keys()
        disable_seeded_cortex_primary()
        from CortexOS.crew.board import ensure_skill_packs

        ensure_skill_packs(self.settings.data_dir / "skills")
        await self.mcp.start_armed()
        # Armed servers do not stay resident. The reaper suspends one once it
        # goes quiet; the next approved tool call starts it again.
        self.mcp.start_reaper()

    async def shutdown(self) -> None:
        await self.runtime.shutdown()
        await self.mcp.stop_all()
        self.store.close()


class SpacePatch(BaseModel):
    title: str | None = None
    ord: int | None = None
    model: str | None = None


class MessageIn(BaseModel):
    text: str


class ConfirmIn(BaseModel):
    approved: bool = False
    takeover: bool = False


class ArmIn(BaseModel):
    armed: bool


class ComputerIn(BaseModel):
    host: str  # this-pc | off


class RuntimeIn(BaseModel):
    backend: str  # laptop | cloudflare-computer


class SkillIn(BaseModel):
    title: str
    body: str


class ImportIn(BaseModel):
    title: str = "Imported chat"
    text: str = ""


class KeysIn(BaseModel):
    keys: dict[str, str | None]


def build_router(crew: CrewApp) -> APIRouter:
    router = APIRouter(prefix="/crew")

    @router.get("/wakes")
    async def wakes() -> dict[str, Any]:
        """Talk liveness. Control GETs this; Control never POSTs wakes."""
        return crew.wakes.snapshot()

    @router.get("/belt")
    async def crew_belt() -> dict[str, Any]:
        """Conveyor JSON. Fallback when /v1/belt is unreachable."""
        return crew.belt()

    @router.get("/health")
    async def health() -> dict[str, Any]:
        from CortexOS.crew.openvault import healthz

        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        mcp = crew.mcp.status()
        return {
            "ok": True,
            "provider": active.public() if active else None,
            "engine": await crew.bridge.health(),
            "openvault": healthz(),
            "computer_control": crew.settings.master_computer_control,
            "runtime": crew.shell.public(),
            "grok_offloaded": True,
            "grok_autostart": False,
            "mcp": mcp,
        }

    @router.get("/providers")
    async def providers() -> dict[str, Any]:
        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        return {
            "active": active.public() if active else None,
            "chain": [p.public() for p in chain],
        }

    @router.get("/spaces")
    async def list_spaces() -> list[dict[str, Any]]:
        return crew.store.list_spaces()

    @router.post("/spaces")
    async def create_space(body: dict[str, Any] | None = None) -> dict[str, Any]:
        title = str((body or {}).get("title") or "New space")
        space = crew.store.create_space(title)
        crew.runtime.ensure_manager(space["id"])
        return space

    @router.patch("/spaces/{space_id}")
    async def patch_space(space_id: str, body: SpacePatch) -> dict[str, Any]:
        space = crew.store.update_space(
            space_id, **{k: v for k, v in body.model_dump().items() if v is not None}
        )
        if space is None:
            raise HTTPException(404, "unknown space")
        return space

    @router.delete("/spaces/{space_id}")
    async def archive_space(space_id: str) -> dict[str, Any]:
        crew.store.archive_space(space_id)
        return {"ok": True}

    @router.get("/spaces/{space_id}/messages")
    async def messages(space_id: str, after: int = 0) -> list[dict[str, Any]]:
        return crew.store.list_messages(space_id, after=after)

    @router.post("/spaces/{space_id}/messages")
    async def post_message(space_id: str, body: MessageIn) -> dict[str, Any]:
        if crew.store.get_space(space_id) is None:
            raise HTTPException(404, "unknown space")
        return await crew.runtime.on_user_message(space_id, body.text)

    @router.get("/spaces/{space_id}/agents")
    async def agents(space_id: str) -> list[dict[str, Any]]:
        return crew.store.list_agents(space_id)

    @router.get("/spaces/{space_id}/events")
    async def events(space_id: str, after: int = -1) -> StreamingResponse:
        """Live events for one space, resumable by message seq.

        ``after`` is the highest seq the client already holds. On connect the
        stream replays everything newer, so a dropped connection cannot leave a
        hole in the transcript. If the client then falls behind far enough for
        the bus to drop events it is sent ``resync`` and refetches, rather than
        being left with a silently incomplete transcript.
        """

        async def stream() -> AsyncIterator[str]:
            sub = crew.bus.subscribe(space_id)
            # No await between subscribe and the backlog read, so nothing can
            # land in the gap: an event is in the backlog or in the queue.
            backlog = crew.store.list_messages(space_id, after=after) if after >= 0 else []
            try:
                yield ": connected" + SSE_BREAK
                for msg in backlog:
                    yield crew.bus.sse("message", {"message": msg, "replay": True})
                while True:
                    try:
                        event, data = await asyncio.wait_for(
                            sub.queue.get(), timeout=SSE_KEEPALIVE_S
                        )
                    except TimeoutError:
                        yield _loss_frame(crew, sub) + ": ping" + SSE_BREAK
                        continue
                    yield _loss_frame(crew, sub) + crew.bus.sse(event, data)
            finally:
                crew.bus.unsubscribe(space_id, sub)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/confirms")
    async def confirms(space_id: str) -> list[dict[str, Any]]:
        return crew.store.pending_confirms(space_id)

    @router.post("/confirms/{confirm_id}")
    async def decide(confirm_id: str, body: ConfirmIn) -> dict[str, Any]:
        row = crew.runtime.decide_confirm(
            confirm_id, body.approved, takeover=body.takeover
        )
        if row is None:
            raise HTTPException(404, "unknown confirm")
        return {"ok": True, "confirm": row}

    @router.get("/roles")
    async def list_roles() -> list[dict[str, Any]]:
        from CortexOS.crew.roles import catalog

        return catalog()

    @router.get("/detect")
    async def detect_plan(q: str = "") -> dict[str, Any]:
        from CortexOS.crew.detect import plan

        return plan(q).as_dict()

    @router.get("/connectors")
    async def list_connectors() -> list[dict[str, Any]]:
        from CortexOS.crew import connectors as connectors_mod

        mcp = crew.mcp.status()
        uacc = next((row for row in mcp if row.get("name") == "uacc"), {})
        return connectors_mod.catalog(
            uacc_enabled=bool(uacc.get("enabled")),
            uacc_armed=bool(uacc.get("armed")),
        )

    @router.get("/routines")
    async def list_routines() -> list[dict[str, Any]]:
        from CortexOS.crew.routines import catalog as routine_catalog

        return routine_catalog()

    @router.post("/import")
    async def import_chat(body: ImportIn) -> dict[str, Any]:
        from CortexOS.crew.import_chats import ingest

        if not (body.text or "").strip():
            raise HTTPException(400, "paste exported chat text")
        result = ingest(crew.store, body.title, body.text)
        crew.runtime.ensure_manager(result["space"]["id"])
        return result

    @router.post("/import/mail")
    async def import_mail(body: ImportIn) -> dict[str, Any]:
        from CortexOS.crew.import_chats import ingest_mail

        if not (body.text or "").strip():
            raise HTTPException(400, "paste an .eml or the email text")
        result = ingest_mail(crew.store, body.text, body.title)
        crew.runtime.ensure_manager(result["space"]["id"])
        return result

    @router.get("/search")
    async def search(q: str = "") -> list[dict[str, Any]]:
        return crew.store.search(q)

    @router.post("/computer")
    async def computer_host(body: ComputerIn) -> dict[str, Any]:
        host = body.host.strip().lower()
        if host not in {"this-pc", "off"}:
            raise HTTPException(400, "host must be this-pc or off")
        if host == "off":
            if "uacc" in {c.spec.name for c in crew.mcp.clients.values()}:
                try:
                    await crew.mcp.arm("uacc", False)
                except (KeyError, PermissionError) as exc:
                    raise HTTPException(403, str(exc)) from exc
            return {"ok": True, "host": "off", "mcp": crew.mcp.status()}
        try:
            await crew.mcp.arm("uacc", True)
        except KeyError as exc:
            raise HTTPException(404, "uacc not catalogued") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {"ok": True, "host": "this-pc", "mcp": crew.mcp.status()}

    @router.get("/runtime")
    async def runtime_status() -> dict[str, Any]:
        """Dual-path exec plane. Isolate is PREVIEW only; never returns secrets."""
        return crew.shell.public()

    @router.post("/runtime")
    async def runtime_choose(body: RuntimeIn) -> dict[str, Any]:
        try:
            status = crew.shell.set_backend(body.backend)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, **status}

    @router.post("/skills")
    async def save_skill(body: SkillIn) -> dict[str, Any]:
        folder = crew.settings.data_dir / "skills"
        folder.mkdir(parents=True, exist_ok=True)
        slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in body.title.strip())[:60]
        path = folder / f"{slug or 'skill'}.md"
        path.write_text(body.body, encoding="utf-8")
        return {"ok": True, "path": str(path), "title": body.title}

    @router.get("/skills")
    async def get_skills() -> list[dict[str, str]]:
        from CortexOS.crew.board import list_skills

        return list_skills(crew.settings.data_dir / "skills")

    @router.post("/imports/ingest")
    async def ingest_dropped() -> dict[str, Any]:
        from CortexOS.crew.import_chats import ingest_readable

        roots = [
            crew.settings.data_dir / "imports",
            crew.settings.data_dir / "drops",
        ]
        return ingest_readable(
            crew.store, roots, skills_dir=crew.settings.data_dir / "skills"
        )

    @router.get("/tickets")
    async def list_tickets() -> dict[str, Any]:
        from CortexOS.crew.board import snapshot

        return snapshot()

    @router.get("/desk")
    async def desk() -> dict[str, Any]:
        from CortexOS.crew.desk import snapshot as desk_snapshot

        mcp = crew.mcp.status()
        uacc = next((row for row in mcp if row.get("name") == "uacc"), {})
        return desk_snapshot(
            uacc_enabled=bool(uacc.get("enabled")),
            uacc_armed=bool(uacc.get("armed")),
        )

    @router.get("/voice")
    async def voice_status() -> dict[str, Any]:
        return {
            "ok": False,
            "available": False,
            "reason": "No TTS/STT key. Rakazo voice needs a provider credential. Crew will not fake speech.",
        }

    @router.get("/keys")
    async def get_keys() -> dict[str, Any]:
        return {"fields": public_fields(), "status": keys_status()["fields"]}

    @router.post("/keys")
    async def post_keys(body: KeysIn) -> dict[str, Any]:
        saved = save_keys(crew.settings.data_dir, body.keys)
        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        return {
            "ok": True,
            "status": saved["fields"],
            "active": active.public() if active else None,
            "chain": [p.public() for p in chain],
        }

    @router.get("/mcp")
    async def mcp_status() -> list[dict[str, Any]]:
        return crew.mcp.status()

    @router.post("/mcp/{name}/arm")
    async def mcp_arm(name: str, body: ArmIn) -> dict[str, Any]:
        try:
            client = await crew.mcp.arm(name, body.armed)
        except KeyError as exc:
            raise HTTPException(404, f"unknown MCP server '{name}'") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {"ok": True, "status": client.status, "armed": client.spec.armed}

    @router.post("/runs/{run_id}/cancel")
    async def cancel(run_id: str) -> dict[str, Any]:
        if not crew.runtime.cancel_run(run_id):
            raise HTTPException(404, "unknown or finished run")
        return {"ok": True}

    return router


def create_app(
    settings: CrewSettings | None = None,
    llm_chat: Any = None,
    bridge: EngineBridge | None = None,
) -> FastAPI:
    crew = CrewApp(settings, llm_chat=llm_chat, bridge=bridge)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await crew.startup()
        try:
            yield
        finally:
            await crew.shutdown()

    app = FastAPI(title="Cortex Crew", lifespan=lifespan)
    app.state.crew = crew
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(build_router(crew))

    @app.get("/v1/belt")
    async def v1_belt() -> dict[str, Any]:
        """Preferred Control probe. Display JSON only. No Cortex HTTP ping."""
        return crew.belt()

    index = crew.settings.ui_dir / "index.html"
    chrome = crew.settings.ui_dir / "crew.css"

    @app.get("/")
    async def root() -> Any:
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"ok": False, "detail": "UI file missing (CortexOS/crew/ui/index.html)"}, 503
        )

    @app.get("/crew.css")
    async def crew_css() -> Any:
        if chrome.is_file():
            return FileResponse(chrome, media_type="text/css")
        return JSONResponse({"ok": False, "detail": "crew.css missing"}, 503)

    @app.get("/stolen.css")
    async def stolen_css() -> Any:
        """Retired clone stylesheet. Original chrome is GET /crew.css."""
        return JSONResponse(
            {"ok": False, "detail": "stolen.css retired; use /crew.css"},
            410,
        )

    return app


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Cortex Crew server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = load_settings()
    port = args.port or settings.port
    uvicorn.run(create_app(settings), host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
