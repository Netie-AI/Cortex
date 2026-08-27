"""HTTP + SSE surface for Crew, and the bundled web UI at /crew.

Stdlib + FastAPI only — no packs imports, no extras required. The UI is a
static single-page app under ``webui/crew`` at the repo root, served by the
engine itself (no second dev-server process).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from CortexOS.crew import fleet, llm, store
from CortexOS.crew.runtime import CrewRuntime
from CortexOS.paths import repo_root


class SpaceBody(BaseModel):
    name: str


class AgentBody(BaseModel):
    space_id: str
    name: str
    emoji: str = "🤖"
    color: str = "#5eead4"
    system_prompt: str = ""
    model: str | None = None
    computer_enabled: bool = False


class AgentPatch(BaseModel):
    name: str | None = None
    emoji: str | None = None
    color: str | None = None
    system_prompt: str | None = None
    model: str | None = None
    computer_enabled: bool | None = None
    paused: bool | None = None
    pinned: bool | None = None
    notes: str | None = None


class MessageBody(BaseModel):
    agent_id: str
    text: str


class ConfigBody(BaseModel):
    base_url: str | None = None
    model: str | None = None
    computer_server: str | None = None


class CompileBody(BaseModel):
    goal: str


STARTER_CREW: list[dict[str, Any]] = fleet.starter()


def register_crew_routes(app: Any) -> None:
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import RedirectResponse, StreamingResponse

    router = APIRouter(prefix="/crew")
    runtime = CrewRuntime()
    app.state.crew_runtime = runtime

    # -- bootstrap + config -------------------------------------------------

    @router.get("/state")
    async def state() -> dict[str, Any]:
        spaces = store.list_spaces()
        return {
            "spaces": spaces,
            "agents": store.list_agents(),
            "config": runtime.config(),
            "usage": store.usage_summary(),
        }

    @router.get("/config")
    async def get_config() -> dict[str, Any]:
        cfg = runtime.config()
        models = llm.list_models(cfg["base_url"])
        return {
            **cfg,
            "reachable": models is not None,
            "models": models or [],
            "mcp": await runtime.bridge.status(),
        }

    @router.post("/config")
    async def set_config(body: ConfigBody) -> dict[str, Any]:
        if body.base_url is not None:
            store.set_setting("llm_base", body.base_url.strip())
        if body.model is not None:
            store.set_setting("llm_model", body.model.strip())
        if body.computer_server is not None:
            store.set_setting("computer_server", body.computer_server.strip())
        return runtime.config()

    @router.get("/fleet")
    async def crew_fleet() -> dict[str, Any]:
        """Agent contract: slug, does, never. Prompts stay on the agents."""
        return {"agents": fleet.public_contract()}

    @router.post("/compile-goal")
    async def compile_goal(body: CompileBody) -> dict[str, Any]:
        """Chat-to-routine: PRD/Ticket hand a goal, Cortex composes a preview."""
        goal = (body.goal or "").strip()
        if not goal:
            raise HTTPException(422, "empty goal")
        from CortexOS.execution.routine_composer import compose

        return compose(goal)

    # -- spaces -------------------------------------------------------------

    @router.post("/spaces")
    async def create_space(body: SpaceBody) -> dict[str, Any]:
        space = store.create_space(body.name)
        runtime.bus.publish({"type": "agents_changed"})
        return space

    @router.patch("/spaces/{space_id}")
    async def rename_space(space_id: str, body: SpaceBody) -> dict[str, Any]:
        if not store.rename_space(space_id, body.name):
            raise HTTPException(404, "unknown space")
        runtime.bus.publish({"type": "agents_changed"})
        return {"ok": True}

    @router.post("/spaces/{space_id}/starter")
    async def starter(space_id: str) -> dict[str, Any]:
        created = [store.create_agent(space_id, d) for d in STARTER_CREW]
        runtime.bus.publish({"type": "agents_changed"})
        return {"agents": created}

    # -- agents -------------------------------------------------------------

    @router.post("/agents")
    async def create_agent(body: AgentBody) -> dict[str, Any]:
        agent = store.create_agent(body.space_id, body.model_dump())
        runtime.bus.publish({"type": "agents_changed"})
        return agent

    @router.patch("/agents/{agent_id}")
    async def patch_agent(agent_id: str, body: AgentPatch) -> dict[str, Any]:
        patch = {k: v for k, v in body.model_dump().items() if v is not None}
        agent = store.update_agent(agent_id, patch)
        if agent is None:
            raise HTTPException(404, "unknown agent")
        runtime.bus.publish({"type": "agents_changed"})
        return agent

    @router.delete("/agents/{agent_id}")
    async def delete_agent(agent_id: str) -> dict[str, Any]:
        if not store.delete_agent(agent_id):
            raise HTTPException(404, "unknown agent")
        runtime.bus.publish({"type": "agents_changed"})
        return {"ok": True}

    @router.get("/agents/{agent_id}/messages")
    async def agent_messages(agent_id: str, limit: int = 200) -> dict[str, Any]:
        return {"messages": store.agent_transcript(agent_id, limit=limit)}

    # -- messages / runs ----------------------------------------------------

    @router.get("/flow")
    async def flow(space_id: str | None = None, limit: int = 400) -> dict[str, Any]:
        return {"messages": store.flow_messages(space_id, limit=limit)}

    @router.post("/messages")
    async def post_message(body: MessageBody) -> dict[str, Any]:
        text = body.text.strip()
        if not text:
            raise HTTPException(422, "empty message")
        try:
            return await runtime.post_user_message(body.agent_id, text)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.post("/runs/{run_id}/stop")
    async def stop_run(run_id: str) -> dict[str, Any]:
        runtime.stop_run(run_id)
        return {"ok": True}

    @router.get("/usage")
    async def usage() -> dict[str, Any]:
        return store.usage_summary()

    # -- live events --------------------------------------------------------

    @router.get("/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(
            runtime.bus.stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.include_router(router)

    @app.get("/crew")
    async def crew_index() -> RedirectResponse:
        return RedirectResponse(url="/crew/ui/")

    ui_dir = repo_root() / "webui" / "crew"
    if ui_dir.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/crew/ui", StaticFiles(directory=str(ui_dir), html=True), name="crew_ui")
