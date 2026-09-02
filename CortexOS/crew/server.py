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
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from CortexOS.crew.config import CrewSettings, load_settings, resolve_providers
from CortexOS.crew.engine_bridge import EngineBridge
from CortexOS.crew.events import EventBus
from CortexOS.crew.keys import public_fields
from CortexOS.crew.keys import save as save_keys
from CortexOS.crew.keys import status as keys_status
from CortexOS.crew.mcp_client import MCPManager
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.crew.store import CrewStore

PEER_HEALTH_WAIT_S = 1.0

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
        from CortexOS.crew.wakes import WakeStore

        self.wakes = WakeStore(self.settings.data_dir / "wakes.json")
        self.runtime.wakes = self.wakes
        self._wake_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        from CortexOS.crew.openvault import (
            disable_seeded_cortex_primary,
            disable_unusable_custom_keys,
            ingest_cursor_from_files,
            push_env_keys,
        )

        ingest_cursor_from_files(self.settings.data_dir.parent.parent)
        push_env_keys()
        disable_seeded_cortex_primary()
        disable_unusable_custom_keys()
        from CortexOS.crew.board import ensure_skill_packs

        ensure_skill_packs(self.settings.data_dir / "skills")
        await self.mcp.start_armed()
        self.runtime.recover_stranded()
        # Armed servers do not stay resident. The reaper suspends one once it
        # goes quiet; the next approved tool call starts it again.
        self.mcp.start_reaper()
        self._wake_task = asyncio.create_task(self._wake_loop())

    async def _wake_loop(self) -> None:
        """Fire due timer wakes as transcript turns. HITL floors still apply."""
        while True:
            await asyncio.sleep(5)
            try:
                await self.fire_due_wakes()
                self.fire_stalls()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def fire_due_wakes(self) -> list[dict[str, Any]]:
        fired: list[dict[str, Any]] = []
        for wake in self.wakes.due():
            row = self.wakes.mark_fired(wake.id)
            if row is None:
                continue
            fired.append(row.as_dict())
            if self.store.get_space(wake.space_id) is None:
                continue
            note = "[wake] " + (wake.note or wake.kind)
            await self.runtime.on_user_message(wake.space_id, note)
        return fired

    def fire_stalls(self) -> list[dict[str, Any]]:
        """Seq-age stalls on teammates only. Idle mail is a wake, not a kill."""
        from CortexOS.crew.stall import detect_stalls

        cut: list[dict[str, Any]] = []
        for hit in detect_stalls(self.store, after_s=self.settings.llm_timeout_s):
            if self.runtime.cut_stalled_teammate(hit):
                cut.append(hit)
                self.wakes.fire_event("stall")
                space_id = hit.get("space_id")
                if space_id:
                    self.wakes.fire_event("stall:" + str(space_id))
        for space in self.store.list_spaces():
            run_id = self.runtime._space_run.get(space["id"])
            ctx = self.runtime._runs.get(run_id) if run_id else None
            if ctx is None:
                continue
            for agent in self.store.list_agents(space["id"]):
                if agent.get("name") == "Manager":
                    continue
                if agent.get("status") != "idle":
                    continue
                if self.runtime.switch.mailbox(agent["id"]).empty():
                    continue
                self.runtime._wake_if_idle(ctx, agent)
        return cut

    def belt_payload(self, cortex: dict[str, Any]) -> dict[str, Any]:
        """Read-only conveyor. Control may GET this. Control must not POST converse."""
        from CortexOS.crew.belt import snapshot as belt_snapshot

        spaces = self.store.list_spaces()
        agents: list[dict[str, Any]] = []
        confirms: list[dict[str, Any]] = []
        for space in spaces:
            agents.extend(self.store.list_agents(space["id"]))
            confirms.extend(self.store.pending_confirms(space["id"]))
        return belt_snapshot(
            cortex=cortex,
            crew={
                "spaces": [{"id": s["id"], "title": s["title"]} for s in spaces],
                "agents": [
                    {
                        "id": a["id"],
                        "name": a["name"],
                        "space_id": a["space_id"],
                        "status": a["status"],
                    }
                    for a in agents
                ],
                "wakes": [w.as_dict() for w in self.wakes.list()],
                "confirms": confirms,
                "queue": self.runtime.work_queue.counts(),
            },
        )

    async def shutdown(self) -> None:
        if self._wake_task is not None:
            self._wake_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._wake_task
            self._wake_task = None
        await self.runtime.shutdown()
        await self.mcp.stop_all()
        self.store.close()


async def _engine_health(crew: CrewApp, timeout: float = PEER_HEALTH_WAIT_S) -> dict[str, Any]:
    """Fail-closed. Control's belt probe is 1.5s; this must return sooner."""
    try:
        engine = await asyncio.wait_for(crew.bridge.health(), timeout=timeout)
        if isinstance(engine, dict):
            return engine
    except Exception as exc:
        return {
            "ok": False,
            "url": getattr(crew.bridge, "base_url", ""),
            "detail": type(exc).__name__,
        }
    return {"ok": False, "detail": "unread"}


def _belt_now(crew: CrewApp) -> dict[str, Any]:
    """Local conveyor. Control already probes /crew/health for engine_ok."""
    return crew.belt_payload({"ok": False, "detail": "not probed"})


def _drop_roots(crew: CrewApp) -> list:
    return [
        crew.settings.data_dir / "imports",
        crew.settings.data_dir / "drops",
    ]


def _import_status(crew: CrewApp) -> dict[str, Any]:
    """Drop-zone contract. Idle zero files is none, not unread."""
    from CortexOS.crew.import_chats import discover_exports

    waiting = discover_exports(_drop_roots(crew))
    n = len(waiting)
    return {
        "ok": True,
        "drop": "ready",
        "files_waiting": n,
        "mail": "POST /crew/import/mail",
        "ingest": "POST /crew/imports/ingest",
        "detail": "import none." if n == 0 else f"{n} waiting",
        "law": "Drop chats or .eml. No import button.",
    }


def _computer_now(crew: CrewApp) -> dict[str, Any]:
    """Host panel JSON. Failed GET is unread in the UI; this body is live."""
    rows = crew.mcp.status()
    if not rows:
        return {
            "ok": True,
            "host": "off",
            "master": False,
            "armed": False,
            "detail": "computer none. MCP catalog empty.",
        }
    master = any(bool(r.get("enabled")) for r in rows)
    uacc = next((r for r in rows if r.get("name") == "uacc"), {})
    armed = bool(uacc.get("armed")) and master
    if not master:
        detail = (
            "computer disarmed. Master off until CORTEX_COMPUTER_CONTROL=1 restart."
        )
        host = "off"
    elif armed:
        detail = "computer armed. UACC on. Mutating clicks still wait for Approve."
        host = "this-pc"
    else:
        detail = "computer master on. UACC disarmed."
        host = "off"
    return {
        "ok": True,
        "host": host,
        "master": master,
        "armed": armed,
        "detail": detail,
    }


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


class SkillIn(BaseModel):
    title: str
    body: str
    labels: list[str] = []
    source: str = ""


class ImportIn(BaseModel):
    title: str = "Imported chat"
    text: str = ""


class KeysIn(BaseModel):
    keys: dict[str, str | None]


class WakeIn(BaseModel):
    space_id: str
    fire_at: str
    note: str = ""


class EventWakeIn(BaseModel):
    space_id: str
    event_key: str
    note: str = ""


class FireEventIn(BaseModel):
    event_key: str


def build_router(crew: CrewApp) -> APIRouter:
    router = APIRouter(prefix="/crew")

    @router.get("/health")
    async def health() -> dict[str, Any]:
        from CortexOS.crew.openvault import healthz

        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        mcp = crew.mcp.status()
        engine_r, ov_r = await asyncio.gather(
            _engine_health(crew),
            asyncio.to_thread(healthz, PEER_HEALTH_WAIT_S),
            return_exceptions=True,
        )
        engine = (
            engine_r
            if isinstance(engine_r, dict)
            else {"ok": False, "detail": type(engine_r).__name__}
        )
        ov = (
            ov_r
            if isinstance(ov_r, dict)
            else {"ok": False, "detail": type(ov_r).__name__}
        )
        return {
            "ok": True,
            "provider": active.public() if active else None,
            "engine": engine,
            "openvault": ov,
            "computer_control": crew.settings.master_computer_control,
            "grok_offloaded": True,
            "grok_autostart": False,
            "mcp": mcp,
            "queue": crew.runtime.work_queue.counts(),
        }

    @router.get("/belt")
    async def belt() -> dict[str, Any]:
        """Same snapshot as GET /v1/belt. Display only. No POST converse.

        Do not ping Cortex here. Control already GETs /crew/health for
        engine_ok. A hung engine must not make belt miss the 1.5s probe.
        """
        return _belt_now(crew)

    @router.get("/wakes")
    async def list_wakes() -> dict[str, Any]:
        return {"ok": True, "wakes": [w.as_dict() for w in crew.wakes.list(include_fired=True)]}

    @router.post("/wakes")
    async def create_wake(body: WakeIn) -> dict[str, Any]:
        from datetime import datetime, timezone

        if crew.store.get_space(body.space_id) is None:
            raise HTTPException(404, "unknown space")
        try:
            fire = datetime.fromisoformat(body.fire_at)
        except ValueError as exc:
            raise HTTPException(400, "fire_at must be ISO-8601") from exc
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=timezone.utc)
        wake = crew.wakes.add_timer(body.space_id, fire, note=body.note)
        return {"ok": True, "wake": wake.as_dict()}

    @router.post("/wakes/event")
    async def create_event_wake(body: EventWakeIn) -> dict[str, Any]:
        if crew.store.get_space(body.space_id) is None:
            raise HTTPException(404, "unknown space")
        key = body.event_key.strip()
        if not key:
            raise HTTPException(400, "event_key required")
        wake = crew.wakes.add_event(body.space_id, key, note=body.note)
        return {"ok": True, "wake": wake.as_dict()}

    @router.post("/wakes/fire")
    async def fire_event_wake(body: FireEventIn) -> dict[str, Any]:
        key = body.event_key.strip()
        if not key:
            raise HTTPException(400, "event_key required")
        crew.wakes.fire_event(key)
        fired = await crew.fire_due_wakes()
        return {"ok": True, "fired": fired}

    @router.get("/providers")
    async def providers() -> dict[str, Any]:
        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        from CortexOS.crew.openvault import preferred_ov_model, register_wizard

        return {
            "active": active.public() if active else None,
            "chain": [p.public() for p in chain],
            "engine_url": crew.settings.engine_url,
            "prefer": preferred_ov_model(probe_vault=False),
            "register": register_wizard(probe_vault=False),
            "vault_listing": "skipped",
        }

    @router.get("/spaces")
    async def list_spaces() -> list[dict[str, Any]]:
        return crew.store.list_spaces()

    @router.post("/spaces")
    async def create_space(body: dict[str, Any] | None = None) -> dict[str, Any]:
        title = str((body or {}).get("title") or "New space")
        space = crew.store.create_space(title)
        crew.runtime.ensure_roster(space["id"])
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

    @router.get("/spaces/{space_id}/todos")
    async def todos(space_id: str) -> list[dict[str, Any]]:
        if crew.store.get_space(space_id) is None:
            raise HTTPException(404, "unknown space")
        return crew.store.list_todos(space_id)

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

    @router.get("/import")
    @router.get("/import/mail")
    async def import_status() -> dict[str, Any]:
        return _import_status(crew)

    @router.get("/imports")
    async def import_list() -> dict[str, Any]:
        from CortexOS.crew.import_chats import discover_exports

        files = discover_exports(_drop_roots(crew))
        return {
            "ok": True,
            "files": files,
            "count": len(files),
            "detail": "import none." if not files else f"{len(files)} waiting",
        }

    @router.post("/import")
    async def import_chat(body: ImportIn) -> dict[str, Any]:
        from CortexOS.crew.import_chats import ingest

        if not (body.text or "").strip():
            raise HTTPException(400, "paste exported chat text")
        result = ingest(crew.store, body.title, body.text)
        crew.runtime.ensure_roster(result["space"]["id"])
        return result

    @router.post("/import/mail")
    async def import_mail(body: ImportIn) -> dict[str, Any]:
        from CortexOS.crew.import_chats import ingest_mail

        if not (body.text or "").strip():
            raise HTTPException(400, "paste an .eml or the email text")
        result = ingest_mail(crew.store, body.text, body.title)
        crew.runtime.ensure_roster(result["space"]["id"])
        return result

    @router.get("/search")
    async def search(q: str = "") -> list[dict[str, Any]]:
        return crew.store.search(q)

    @router.get("/computer")
    async def computer_status() -> dict[str, Any]:
        return _computer_now(crew)

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

    @router.post("/skills")
    async def save_skill(body: SkillIn) -> dict[str, Any]:
        from CortexOS.crew.board import save_skill as write_skill

        folder = crew.settings.data_dir / "skills"
        saved = write_skill(
            folder,
            body.title,
            body.body,
            labels=body.labels,
            source=body.source,
        )
        return {
            "ok": True,
            "path": saved["path"],
            "title": saved["title"],
            "labels": saved.get("labels") or "",
            "source": saved.get("source") or "",
        }

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

    index = crew.settings.ui_dir / "index.html"
    crew_css = crew.settings.ui_dir / "crew.css"

    @app.get("/v1/belt")
    async def v1_belt() -> dict[str, Any]:
        return _belt_now(crew)

    @app.get("/")
    async def root() -> Any:
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            {"ok": False, "detail": "UI file missing (CortexOS/crew/ui/index.html)"}, 503
        )

    @app.get("/crew.css")
    async def crew_sheet() -> Any:
        if crew_css.is_file():
            return FileResponse(crew_css, media_type="text/css")
        return JSONResponse({"ok": False, "detail": "crew.css missing"}, 503)

    @app.get("/favicon.ico")
    async def favicon() -> Any:
        return Response(status_code=204)

    @app.get("/stolen.css")
    async def stolen_css() -> Any:
        return JSONResponse(
            {
                "ok": False,
                "detail": "replaced by /crew.css. Guaca leftover removed.",
            },
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
