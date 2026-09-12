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

from CortexOS.crew.approvals import ApprovalBook
from CortexOS.crew.belt import TicketLeaseLedger
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
        self.approvals = ApprovalBook(self.settings.data_dir / "approvals.json")
        self.runtime = CrewRuntime(
            self.store,
            self.bus,
            self.settings,
            self.mcp,
            self.bridge,
            llm_chat=llm_chat,
            approvals=self.approvals,
        )
        self.shell = CrewShell(self.settings)
        self.wakes = WakeBoard()
        self.queue = JobQueue()
        self.leases = TicketLeaseLedger()
        self._fetch_warm: asyncio.Task[None] | None = None

    def mailbox_nonempty(self) -> bool:
        """True when any A2A mailbox still holds unread envelopes.

        Derived flag only. Does not allocate a second mailbox.
        """
        switch = getattr(self.runtime, "switch", None)
        if switch is None:
            return False
        return bool(switch.any_waiting())

    def threads(self, space_id: str) -> dict[str, Any] | None:
        """HUD view over the Switchboard. Same bus as ``/spaces/messages``."""
        from CortexOS.crew import a2a

        if self.store.get_space(space_id) is None:
            return None
        names = {a["id"]: a["name"] for a in self.store.list_agents(space_id)}
        pending: list[dict[str, Any]] = []
        switch = getattr(self.runtime, "switch", None)
        if switch is not None:
            for row in switch.pending_asks():
                pending.append(
                    {
                        **row,
                        "from": names.get(row["asker_id"], row["asker_id"]),
                        "to": names.get(row["target_id"], row["target_id"]),
                    }
                )
        return a2a.hud(self.store.list_messages(space_id), pending)

    def belt(self) -> dict[str, Any]:
        from CortexOS.crew.assign import public as assignment_public

        return conveyor(
            self.store,
            self.wakes,
            self.queue,
            mailbox_nonempty=self.mailbox_nonempty(),
            assignments=assignment_public(self.settings.data_dir),
            data_dir=self.settings.data_dir,
            leases=self.leases,
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
        from CortexOS.crew import github as github_mod

        # Off the GET /v1/belt path so Control's 1.5s proxy does not wait on gh.
        self._fetch_warm = asyncio.create_task(
            asyncio.to_thread(github_mod.warm_fetched, self.settings.data_dir)
        )

    async def shutdown(self) -> None:
        if self._fetch_warm is not None:
            self._fetch_warm.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._fetch_warm
        await self.runtime.shutdown()
        await self.mcp.stop_all()
        self.store.close()


class SpacePatch(BaseModel):
    title: str | None = None
    ord: int | None = None
    model: str | None = None


class MessageIn(BaseModel):
    text: str
    provider: str | None = None
    model: str | None = None


class ProviderPin(BaseModel):
    provider: str | None = None
    model: str | None = None


class ConfirmIn(BaseModel):
    approved: bool = False
    takeover: bool = False


class ApprovalIn(BaseModel):
    mode: str | None = None


class ApprovalRevokeIn(BaseModel):
    tool: str = ""


class MemoryIn(BaseModel):
    name: str
    description: str
    body: str = ""
    scope: str = "space"
    owner: str = ""


class ArmIn(BaseModel):
    armed: bool


class ComputerIn(BaseModel):
    host: str  # this-pc | off


class RuntimeIn(BaseModel):
    backend: str  # laptop | cloudflare-computer


class InsightsIn(BaseModel):
    intent: str
    ask: bool = True


class SkillIn(BaseModel):
    title: str
    body: str


class AgentSpawnIn(BaseModel):
    name: str
    brief: str = ""
    role: str = ""
    capability: str = ""
    mode: str = "active"
    goal: str = ""


class AgentModeIn(BaseModel):
    mode: str
    goal: str | None = None


class AgentKillIn(BaseModel):
    reason: str = "operator killed"


class AgentAcceptIn(BaseModel):
    brief: str = ""


class ImportIn(BaseModel):
    title: str = "Imported chat"
    text: str = ""


class KeysIn(BaseModel):
    keys: dict[str, str | None]


class TicketAssignIn(BaseModel):
    spec: str
    space_id: str
    name: str = "Ticket"


class TicketLeaseIn(BaseModel):
    worker: str = ""
    name: str = ""
    ttl_s: int | None = None
    space_id: str = ""


class TaskAssignIn(BaseModel):
    destination: str
    space_id: str = ""
    brief: str = ""
    name: str = "Assign"
    spec: str = ""
    execute: bool = False
    lane: str = ""
    live_ssh: bool = False


class TicketBuildIn(BaseModel):
    spec: str
    space_id: str
    name: str = ""
    verify: str = ""


class TicketScaleIn(BaseModel):
    space_id: str
    limit: int = 3
    names: str = ""


_LEASE_LAW = (
    "Crew lease. Control display-only. Did not write CLAIMS.json. "
    "Did not set a GitHub assignee."
)


def _lease_worker(body: TicketLeaseIn) -> str:
    return (body.worker or body.name or "").strip()


def _refuse_seated(spec: str) -> None:
    from CortexOS.crew import github as github_mod

    seated = github_mod.seated_claim(spec)
    if seated is None:
        return
    raise HTTPException(
        409,
        (
            f"DENIED: {seated.get('ticket')} is SEATED "
            f"({seated.get('owner_pr')}). Ticket Runner owns the seat."
        ),
    )


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
        from CortexOS.crew.llm import chosen_public, usage_view

        choice = chosen_public()
        return {
            "ok": True,
            "provider": active.public() if active else None,
            "chosen": choice["chosen"],
            "refused": choice["refused"],
            "engine": await crew.bridge.health(),
            "openvault": healthz(),
            "computer_control": crew.settings.master_computer_control,
            "runtime": crew.shell.public(),
            "grok_offloaded": True,
            "grok_autostart": False,
            "mcp": mcp,
            "usage": usage_view(crew.store.usage_totals()),
        }

    @router.get("/providers")
    async def providers() -> dict[str, Any]:
        from CortexOS.crew.llm import chosen_public

        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        choice = chosen_public()
        return {
            "active": active.public() if active else None,
            "chain": [p.public() for p in chain],
            "chosen": choice["chosen"],
            "refused": choice["refused"],
        }

    @router.post("/providers")
    async def pin_provider(body: ProviderPin) -> dict[str, Any]:
        from CortexOS.crew.keys import pin_provider as save_pin
        from CortexOS.crew.llm import chosen_public

        save_pin(crew.settings.data_dir, body.provider, body.model)
        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        choice = chosen_public(provider=body.provider, model=body.model)
        return {
            "ok": choice["refused"] is None
            or not ((body.provider or "").strip() or (body.model or "").strip()),
            "active": active.public() if active else None,
            "chain": [p.public() for p in chain],
            "chosen": choice["chosen"],
            "refused": choice["refused"],
        }

    @router.get("/usage")
    async def usage() -> dict[str, Any]:
        from CortexOS.crew.llm import usage_view

        return usage_view(crew.store.usage_totals())

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

    @router.get("/spaces/{space_id}/threads")
    async def threads(space_id: str) -> dict[str, Any]:
        """Correlated ask/reply HUD. Reads the message bus + live pending asks.

        Not a second inbox. CMD ``@`` stamps on ``POST /spaces/messages``
        already carry ``meta.a2a``; this view groups those rows.
        """
        body = crew.threads(space_id)
        if body is None:
            raise HTTPException(404, "unknown space")
        return body

    @router.post("/spaces/{space_id}/messages")
    async def post_message(space_id: str, body: MessageIn) -> dict[str, Any]:
        if crew.store.get_space(space_id) is None:
            raise HTTPException(404, "unknown space")
        return await crew.runtime.on_user_message(
            space_id,
            body.text,
            provider=body.provider,
            model=body.model,
        )

    @router.get("/spaces/{space_id}/agents")
    async def agents(space_id: str) -> list[dict[str, Any]]:
        return crew.store.list_agents(space_id)

    @router.post("/spaces/{space_id}/agents")
    async def spawn_agent(space_id: str, body: AgentSpawnIn) -> dict[str, Any]:
        if crew.store.get_space(space_id) is None:
            raise HTTPException(404, "unknown space")
        result = await crew.runtime.operator_spawn(
            space_id,
            {
                "name": body.name,
                "brief": body.brief,
                "role": body.role,
                "capability": body.capability,
                "mode": body.mode,
                "goal": body.goal,
            },
        )
        if result.get("error"):
            raise HTTPException(400, str(result["error"]))
        return result

    @router.post("/spaces/{space_id}/agents/{agent_id}/stop")
    async def stop_agent(space_id: str, agent_id: str) -> dict[str, Any]:
        row = crew.store.get_agent(agent_id)
        if row is None or row.get("space_id") != space_id:
            raise HTTPException(404, "unknown agent")
        stopped = crew.runtime.stop_agent(agent_id)
        if isinstance(stopped, dict) and stopped.get("error"):
            raise HTTPException(400, str(stopped["error"]))
        return {"ok": True, "agent": stopped}

    @router.post("/spaces/{space_id}/agents/{agent_id}/idle")
    async def idle_agent(space_id: str, agent_id: str) -> dict[str, Any]:
        row = crew.store.get_agent(agent_id)
        if row is None or row.get("space_id") != space_id:
            raise HTTPException(404, "unknown agent")
        idled = crew.runtime.idle_agent(agent_id)
        if isinstance(idled, dict) and idled.get("error"):
            raise HTTPException(400, str(idled["error"]))
        return {"ok": True, "agent": idled}

    @router.post("/spaces/{space_id}/agents/{agent_id}/wait")
    async def wait_agent(space_id: str, agent_id: str) -> dict[str, Any]:
        row = crew.store.get_agent(agent_id)
        if row is None or row.get("space_id") != space_id:
            raise HTTPException(404, "unknown agent")
        waiting = crew.runtime.wait_agent(agent_id)
        if isinstance(waiting, dict) and waiting.get("error"):
            raise HTTPException(400, str(waiting["error"]))
        return {"ok": True, "agent": waiting}

    @router.post("/spaces/{space_id}/agents/{agent_id}/kill")
    async def kill_agent(space_id: str, agent_id: str, body: AgentKillIn | None = None) -> dict[str, Any]:
        row = crew.store.get_agent(agent_id)
        if row is None or row.get("space_id") != space_id:
            raise HTTPException(404, "unknown agent")
        killed = crew.runtime.kill_agent(
            agent_id, reason=(body.reason if body is not None else "operator killed")
        )
        if isinstance(killed, dict) and killed.get("error"):
            raise HTTPException(400, str(killed["error"]))
        return {"ok": True, "agent": killed}

    @router.post("/spaces/{space_id}/agents/{agent_id}/mode")
    async def set_agent_mode(space_id: str, agent_id: str, body: AgentModeIn) -> dict[str, Any]:
        row = crew.store.get_agent(agent_id)
        if row is None or row.get("space_id") != space_id:
            raise HTTPException(404, "unknown agent")
        updated = crew.runtime.set_agent_mode(agent_id, body.mode, goal_text=body.goal)
        if updated is None:
            raise HTTPException(404, "unknown agent")
        return {"ok": True, "agent": updated}

    @router.post("/spaces/{space_id}/agents/{agent_id}/accept")
    async def accept_task(space_id: str, agent_id: str, body: AgentAcceptIn | None = None) -> dict[str, Any]:
        row = crew.store.get_agent(agent_id)
        if row is None or row.get("space_id") != space_id:
            raise HTTPException(404, "unknown agent")
        accepted = crew.runtime.accept_task(
            agent_id, brief=(body.brief if body is not None else "")
        )
        if isinstance(accepted, dict) and accepted.get("error"):
            raise HTTPException(400, str(accepted["error"]))
        return {"ok": True, "agent": accepted}

    def _space_mem(space_id: str, scope: str = "space", owner: str = "") -> Any:
        from CortexOS.crew.memory import CrewMemoryError

        if crew.store.get_space(space_id) is None:
            raise HTTPException(404, "unknown space")
        try:
            return crew.runtime._mem(space_id, scope=scope, owner=owner)
        except CrewMemoryError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/spaces/{space_id}/memory")
    async def list_memory(
        space_id: str, q: str = "", scope: str = "space", owner: str = ""
    ) -> dict[str, Any]:
        mem = _space_mem(space_id, scope, owner)
        query = q.strip()
        facts = mem.search(query) if query else mem.list_facts()
        return mem.public_payload(facts)

    @router.get("/spaces/{space_id}/memory/search")
    async def search_memory(
        space_id: str, q: str = "", scope: str = "space", owner: str = ""
    ) -> dict[str, Any]:
        mem = _space_mem(space_id, scope, owner)
        payload = mem.public_payload(mem.search(q))
        payload["query"] = q
        return payload

    @router.post("/spaces/{space_id}/memory")
    async def save_memory(space_id: str, body: MemoryIn) -> dict[str, Any]:
        from CortexOS.crew.memory import CrewMemoryError

        mem = _space_mem(space_id, body.scope, body.owner)
        try:
            detail = mem.remember(body.name, body.description, body.body)
        except CrewMemoryError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "detail": detail,
            "facts": mem.public_facts(),
            "scope": mem.scope,
            "owner": mem.owner,
            "file": "facts.md",
        }

    @router.get("/spaces/{space_id}/memory/export")
    async def export_memory(
        space_id: str, scope: str = "space", owner: str = ""
    ) -> dict[str, Any]:
        mem = _space_mem(space_id, scope, owner)
        return {
            "filename": mem.export_filename(),
            "markdown": mem.export_markdown(),
            "scope": mem.scope,
            "owner": mem.owner,
        }

    @router.delete("/spaces/{space_id}/memory/{name}")
    async def forget_memory(
        space_id: str, name: str, scope: str = "space", owner: str = ""
    ) -> dict[str, Any]:
        from CortexOS.crew.memory import CrewMemoryError

        mem = _space_mem(space_id, scope, owner)
        try:
            detail = mem.forget(name)
        except CrewMemoryError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "ok": True,
            "detail": detail,
            "facts": mem.public_facts(),
            "scope": mem.scope,
            "owner": mem.owner,
        }

    @router.post("/spaces/{space_id}/clear")
    async def clear_chat(space_id: str) -> dict[str, Any]:
        if crew.store.get_space(space_id) is None:
            raise HTTPException(404, "unknown space")
        return crew.runtime.clear_chat(space_id)

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
        return {"ok": True, "confirm": row, "approvals": crew.approvals.snapshot()}

    @router.get("/approvals")
    async def get_approvals() -> dict[str, Any]:
        """Standing / session / one-off ladder. Server SoT, not localStorage."""
        return crew.approvals.snapshot()

    @router.put("/approvals")
    async def put_approvals(body: ApprovalIn) -> dict[str, Any]:
        if body.mode is None:
            return crew.approvals.snapshot()
        try:
            return crew.approvals.set_mode(body.mode)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/approvals/revoke")
    async def revoke_approvals(body: ApprovalRevokeIn | None = None) -> dict[str, Any]:
        tool = (body.tool if body is not None else "") or ""
        return crew.approvals.revoke(tool.strip() or None)

    @router.get("/roles")
    async def list_roles() -> list[dict[str, Any]]:
        from CortexOS.crew.roles import catalog

        return catalog()

    @router.get("/commands")
    async def list_commands(q: str = "", space_id: str = "") -> dict[str, Any]:
        """Slash catalog plus @ targets. Autocomplete source for the composer."""
        from CortexOS.crew.commands import (
            catalog,
            filter_commands,
            filter_mentions,
            mention_targets,
        )

        skills_dir = crew.settings.data_dir / "skills"
        cmds = catalog(skills_dir)
        agents = crew.store.list_agents(space_id) if space_id else []
        mentions = mention_targets(agents)
        needle = (q or "").strip()
        if needle.startswith("@"):
            mentions = filter_mentions(mentions, needle)
            cmds = filter_commands(cmds, "")
        elif needle:
            cmds = filter_commands(cmds, needle)
        return {"commands": cmds, "mentions": mentions}

    @router.get("/appshell")
    async def appshell_catalog() -> dict[str, Any]:
        """Nav + Apps launcher + Control/Audit deep-links. GET catalog only."""
        from CortexOS.crew import appshell as appshell_mod

        return appshell_mod.catalog(engine_url=crew.settings.engine_url)

    @router.get("/appshell/health")
    async def appshell_health() -> dict[str, Any]:
        from CortexOS.crew import appshell as appshell_mod

        return appshell_mod.health(engine_url=crew.settings.engine_url)

    @router.get("/appshell/control")
    async def appshell_control(path: str | None = None) -> dict[str, Any]:
        """Control :8040 display GET. Never POST run/goal/route/secrets."""
        from CortexOS.crew import appshell as appshell_mod

        body = appshell_mod.control_display(path=path)
        if body.get("refused"):
            raise HTTPException(403, body.get("reason") or "not a Control display GET")
        return body

    @router.get("/appshell/audit")
    async def appshell_audit() -> dict[str, Any]:
        """Honest RSF Audit/Operate payload. CERTIFIED|ABSTAIN|REFUSE only."""
        from CortexOS.crew import appshell as appshell_mod

        return appshell_mod.audit_payload()

    @router.post("/appshell/control")
    async def appshell_control_post() -> Any:
        raise HTTPException(405, "Control is display-only F-0030. GET only.")

    @router.post("/appshell/spawn")
    async def appshell_spawn_refused() -> dict[str, Any]:
        from CortexOS.crew import appshell as appshell_mod

        body = appshell_mod.refuse_control_spawn()
        raise HTTPException(403, body["reason"])

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

    @router.post("/connectors/{slug}/arm")
    async def arm_connector(slug: str, body: ArmIn) -> dict[str, Any]:
        from CortexOS.crew import connectors as connectors_mod

        try:
            result = connectors_mod.arm(slug, body.armed)
        except connectors_mod.ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc
        mcp = crew.mcp.status()
        uacc = next((row for row in mcp if row.get("name") == "uacc"), {})
        return {
            **result,
            "catalog": connectors_mod.catalog(
                uacc_enabled=bool(uacc.get("enabled")),
                uacc_armed=bool(uacc.get("armed")),
            ),
        }

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

    @router.get("/assign")
    async def assign_catalog() -> dict[str, Any]:
        """Destination coordinate map. Control may GET-display. Does not execute."""
        from CortexOS.crew.assign_router import catalog as assign_catalog

        return assign_catalog(live=True)

    @router.post("/assign")
    async def assign_task(body: TaskAssignIn) -> Any:
        """Execute through Crew adapters. Control must not POST this."""
        from CortexOS.crew.assign_router import dispatch as assign_dispatch

        result = await assign_dispatch(
            crew.runtime,
            {
                "destination": body.destination,
                "space_id": body.space_id,
                "brief": body.brief,
                "name": body.name,
                "spec": body.spec,
                "execute": body.execute,
                "lane": body.lane,
                "live_ssh": body.live_ssh,
            },
        )
        if not result.get("ok"):
            code = int(result.get("status_code") or 409)
            return JSONResponse(result, status_code=code)
        return result

    @router.get("/insights")
    async def insights_law() -> dict[str, Any]:
        """Ask+ontology map. No numbers. Control may GET-display."""
        from CortexOS.crew import insights as insights_mod

        return insights_mod.public_law(shell_public=crew.shell.public())

    @router.get("/insights/ontology")
    async def insights_ontology(q: str = "") -> dict[str, Any]:
        """Where + importance ranking only. Does not ask DMS."""
        from CortexOS.crew import insights as insights_mod

        intent = (q or "").strip()
        if not intent:
            raise HTTPException(400, "q is required")
        ranking = insights_mod.retrieve_ontology(intent)
        return {
            "ok": bool(ranking.get("ok")),
            "phase": "ontology",
            "intent": intent,
            "ontology": ranking,
            "law": insights_mod.LAW,
            "export_runtime": insights_mod.export_runtime_hint(crew.shell.public()),
        }

    @router.post("/insights")
    async def insights_ask(body: InsightsIn) -> Any:
        """Ontology first, then constrained DMS ask. CERTIFIED|ABSTAIN|REFUSE."""
        from CortexOS.crew import insights as insights_mod

        intent = (body.intent or "").strip()
        if not intent:
            raise HTTPException(400, "intent is required")
        result = await insights_mod.run_insights(
            intent,
            bridge=crew.bridge,
            ask=body.ask,
            shell_public=crew.shell.public(),
        )
        return result

    @router.get("/tickets")
    async def list_tickets() -> dict[str, Any]:
        from CortexOS.crew import github as github_mod
        from CortexOS.crew.assign import public as assignment_public
        from CortexOS.crew.board import overlay_leases, snapshot

        body = snapshot()
        body["assignments"] = assignment_public(crew.settings.data_dir)
        claimed = {
            str(row.get("ticket") or "")
            for row in (body.get("tickets") or [])
            if isinstance(row, dict)
        }
        claimed.update(
            str(row.get("owner_pr") or "")
            for row in (body.get("tickets") or [])
            if isinstance(row, dict)
        )
        fetched = github_mod.list_open_issues()
        github_mod.remember_fetched(crew.settings.data_dir, fetched)
        issues = []
        for row in fetched.get("issues") or []:
            if not isinstance(row, dict):
                continue
            spec = str(row.get("spec") or "")
            if spec and spec in claimed:
                continue
            issues.append(row)
        leases = crew.leases.public()
        body["leases"] = leases
        body["tickets"] = overlay_leases(body.get("tickets") or [], leases)
        body["issues"] = overlay_leases(issues, leases)
        body["issues_detail"] = fetched.get("detail") or ""
        body["issues_ok"] = bool(fetched.get("ok"))
        body["lease_owner"] = (
            "Crew POST /crew/tickets/{id}/claim. Control does not lease."
        )
        return body

    @router.post("/tickets/claim")
    async def claim_ticket(body: TicketAssignIn) -> dict[str, Any]:
        """Local /assign bind. Does not write CLAIMS.json. Does not seat Ticket Runner."""
        if crew.store.get_space(body.space_id) is None:
            raise HTTPException(404, "unknown space")
        rest = f"{body.spec.strip()} | {body.name.strip() or 'Ticket'}"
        text, args = await crew.runtime._assign_issue_slash(body.space_id, rest)
        if text.startswith("DENIED"):
            code = 409 if "SEATED" in text else 400
            raise HTTPException(code, text)
        return {
            "ok": True,
            "detail": text,
            "args": args,
            "run_id": args.get("run_id"),
            "law": "Local bind. Did not write CLAIMS.json. Did not set a GitHub assignee.",
        }

    @router.post("/tickets/release")
    async def release_ticket(body: TicketAssignIn) -> dict[str, Any]:
        """Drop a Crew-local bind. Never edits CLAIMS.json."""
        from CortexOS.crew import github as github_mod
        from CortexOS.crew.assign import release as release_bind

        seated = github_mod.seated_claim(body.spec)
        if seated is not None:
            raise HTTPException(
                409,
                (
                    f"DENIED: {seated.get('ticket')} is SEATED "
                    f"({seated.get('owner_pr')}). Ticket Runner owns the seat."
                ),
            )
        release_bind(crew.settings.data_dir, body.spec)
        return {
            "ok": True,
            "spec": github_mod.canonical_spec(body.spec),
            "law": "Released local bind. Did not edit CLAIMS.json.",
        }

    @router.post("/tickets/build")
    async def build_ticket(body: TicketBuildIn) -> dict[str, Any]:
        """Operator /build over HTTP: skill build + verifier named test. 409 SEATED."""
        from CortexOS.crew import scale as scale_mod

        if crew.store.get_space(body.space_id) is None:
            raise HTTPException(404, "unknown space")
        rest = f"{body.spec.strip()} | {body.name.strip()} | {body.verify.strip()}"
        text, args = await crew.runtime._build_issue_slash(body.space_id, rest)
        if text.startswith("DENIED"):
            code = 409 if "SEATED" in text else 400
            raise HTTPException(code, text)
        return {
            "ok": True,
            "detail": text,
            "args": args,
            "run_id": args.get("run_id"),
            "verify": args.get("verify"),
            "law": scale_mod.LAW_BUILD,
        }

    @router.post("/tickets/scale")
    async def scale_tickets(body: TicketScaleIn) -> dict[str, Any]:
        """Operator /scale over HTTP: seat existing writers, spawn only to cap."""
        from CortexOS.crew import scale as scale_mod

        if crew.store.get_space(body.space_id) is None:
            raise HTTPException(404, "unknown space")
        limit = max(1, min(scale_mod.MAX_SCALE_LIMIT, int(body.limit)))
        rest = str(limit)
        if body.names.strip():
            rest = f"{rest} | {body.names.strip()}"
        text, args = await crew.runtime._scale_slash(body.space_id, rest)
        if text.startswith("DENIED"):
            raise HTTPException(409, text)
        return {
            "ok": True,
            "detail": text,
            "seated": args.get("seated") or [],
            "queued": args.get("queued") or [],
            "skipped": args.get("skipped") or [],
            "run_id": args.get("run_id"),
            "law": scale_mod.LAW_SCALE,
        }

    @router.post("/tickets/{ticket_id:path}/claim")
    async def claim_ticket_lease(ticket_id: str, body: TicketLeaseIn) -> dict[str, Any]:
        """Claim a ticket lease. 409 if held elsewhere or SEATED. Control never POSTs."""
        _refuse_seated(ticket_id)
        result = crew.leases.claim(ticket_id, _lease_worker(body), body.ttl_s)
        if not result["ok"]:
            code = 409 if result["reason"] == "conflict" else 400
            raise HTTPException(code, result["detail"])
        lease = result["lease"]
        return {
            "ok": True,
            "id": lease["id"],
            "worker": lease["worker"],
            "ttl_s": lease["ttl_s"],
            "until": lease["until"],
            "detail": result["detail"],
            "law": _LEASE_LAW,
        }

    @router.post("/tickets/{ticket_id:path}/release")
    async def release_ticket_lease(ticket_id: str, body: TicketLeaseIn) -> dict[str, Any]:
        """Release only the holder. 403 other worker. 409 if not held or SEATED."""
        _refuse_seated(ticket_id)
        result = crew.leases.release(ticket_id, _lease_worker(body))
        if not result["ok"]:
            if result["reason"] == "forbidden":
                code = 403
            elif result["reason"] == "missing":
                code = 409
            else:
                code = 400
            raise HTTPException(code, result["detail"])
        lease = result["lease"]
        return {
            "ok": True,
            "id": lease["id"],
            "detail": result["detail"],
            "law": "Released Crew lease. Did not edit CLAIMS.json.",
        }

    @router.get("/desk")
    async def desk() -> dict[str, Any]:
        from CortexOS.crew.desk import snapshot as desk_snapshot

        mcp = crew.mcp.status()
        uacc = next((row for row in mcp if row.get("name") == "uacc"), {})
        return desk_snapshot(
            uacc_enabled=bool(uacc.get("enabled")),
            uacc_armed=bool(uacc.get("armed")),
            usage=crew.store.usage_totals(),
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
        from CortexOS.crew.llm import chosen_public

        saved = save_keys(crew.settings.data_dir, body.keys)
        chain = resolve_providers()
        active = next((p for p in chain if p.active), None)
        choice = chosen_public()
        return {
            "ok": True,
            "status": saved["fields"],
            "active": active.public() if active else None,
            "chain": [p.public() for p in chain],
            "chosen": choice["chosen"],
            "refused": choice["refused"],
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
    manifest = crew.settings.ui_dir / "appshell.webmanifest"

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

    @app.get("/appshell.webmanifest")
    async def appshell_manifest() -> Any:
        if manifest.is_file():
            return FileResponse(manifest, media_type="application/manifest+json")
        return JSONResponse({"ok": False, "detail": "appshell.webmanifest missing"}, 503)

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
