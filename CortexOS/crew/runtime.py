"""Crew orchestration - a Manager agent that answers, delegates, and merges.

Guaca-shaped: every space has a persistent Manager; it answers simple things
itself, calls the governed engine for data questions, and for bigger work
spawns teammates that run concurrently and talk over an in-process A2A inbox.
The user gets ONE final answer; the crew's traffic stays visible in the
transcript as agent-to-agent wire messages.

Honesty rules enforced here rather than hoped for:
- the active provider/model is stamped into every assistant message meta;
- an LLM or tool failure is persisted into the transcript, not swallowed;
- run budgets (llm calls, steps, agents) end a run with a visible system
  message instead of an endless spin.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from CortexOS.crew import a2a, detect, policy, roles
from CortexOS.crew import llm as llm_mod
from CortexOS.crew.config import CrewSettings, active_provider
from CortexOS.crew.engine_bridge import EngineBridge
from CortexOS.crew.events import EventBus
from CortexOS.crew.llm import LLMError, LLMResult, ToolCall
from CortexOS.crew.mcp_client import MCPManager
from CortexOS.crew.store import CrewStore

# Colors follow the Constructor desk roster (CortexOS/connectors/agents.py).
AGENT_COLORS = ["#388bfd", "#f778ba", "#39d353", "#f85149", "#d29922", "#58a6ff", "#8b949e"]
MANAGER_COLOR = "#4e6b16"

MANAGER_CHARTER = """You are the Manager of this crew space in Cortex Crew, a local agentic \
workspace over the Cortex engine.

How to work:
- Answer simple questions directly and concisely yourself.
- For questions about governed company data or metrics, use cortex_ask. Report its answer \
together with its badge and audit id. If the engine abstains or is offline, say so plainly. \
Never invent numbers the engine did not return.
- For multi-part or specialist work, spawn teammates with spawn_agent. Name \
them for THIS job (not a fixed roster). Copy a capability template when one \
fits; its default skills are copied into the teammate automatically. Restrict \
shared tools with allow_tools / deny_tools. Rename with \
rename_agent. For quality-critical work set verify=true with explicit \
verify_criteria; skip verify rather than rubber-stamp. \
Crew A2A is the graph. Do not start LangGraph. Engine gen_cfsm stays on the \
answer plane. OpenVault holds keys.
- Talk to the crew with send_to_agent (hand something over, keep working), ask_agent \
(ask ONE named teammate and wait for that teammate's answer), broadcast (tell everyone \
at once), and wait_for_replies (collect whatever has arrived). Prefer ask_agent when you \
need a specific answer - the reply is correlated for you, so another teammate's report \
cannot be mistaken for it. If a teammate stops without answering, you are told so \
plainly: report that instead of inventing what they would have said.
- Computer-control tools may be denied or may wait for operator approval; if a call is denied, \
tell the user what was denied and why instead of pretending it ran.
- The human operator is money and decision authority. Do not auto-pay, auto-send, or auto-merge.
- Do not spawn infinite Cursor cloud chats. One issue per human-opened chat. Ticket Runner seats \
existing writers.
- Models go through OpenVault FreeRoute. Cursor chats use grok-4.6 (high), never grok-fast.
- To check PRs, mail, connectors, the Cursor key, or the GitHub org estate, call desk_status or estate_status. Before shipping, call ship_gate (repo=slug or repo=all). Do not ask the operator to click import or PR buttons. Dropped files already become spaces.
- Your final plain-text reply is the only thing the user reads. Keep it direct. Plain ASCII only.

""" + roles.charter_block()

TEAMMATE_CHARTER = """You are {name}, a teammate in a Cortex Crew space. Your role: {role}

Work the brief you were given. You may use cortex_ask for governed data, send_to_agent to talk \
to other teammates or the Manager, and any computer-control tools you are offered (they may \
require operator approval). Use ask_agent when you need one named teammate to answer you \
before you can continue, and broadcast to tell everyone at once. When another agent asks \
you a question, answer it with send_to_agent back to whoever asked - they are blocked \
waiting for you. If you are restarted, the messages above are your own real history; \
continue from them rather than starting over. When you are done, reply with your findings \
as one compact final message (or call finish). Your reply goes to the Manager, not the \
user. Plain ASCII only."""""


class _AgentFinished(Exception):
    def __init__(self, summary: str) -> None:
        super().__init__(summary)
        self.summary = summary


@dataclass
class AgentHandle:
    """Per-agent bookkeeping. The mailbox lives on the Switchboard, not here,
    so a restarted agent keeps whatever was queued for it."""

    row: dict[str, Any]
    task: asyncio.Task[None] | None = None


@dataclass
class RunContext:
    id: str
    space_id: str
    #: set once the run has reached a terminal status; a straggler task must
    #: not then emit another 'running' event over the top of it
    closed: bool = False
    stats: dict[str, Any] = field(
        default_factory=lambda: {
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0.0,
        }
    )
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64]


class CrewRuntime:
    def __init__(
        self,
        store: CrewStore,
        bus: EventBus,
        settings: CrewSettings,
        mcp: MCPManager,
        bridge: EngineBridge,
        llm_chat: Any = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.settings = settings
        self.mcp = mcp
        self.bridge = bridge
        self._llm = llm_chat or llm_mod.chat
        self._handles: dict[str, AgentHandle] = {}
        self.switch = a2a.Switchboard()
        # agent_id -> {asker_id: question message id}. Lets the runtime stamp
        # reply_to automatically, so an answer is matched to its question
        # rather than merely to its sender.
        self._open_questions: dict[str, dict[str, str]] = {}
        self._runs: dict[str, RunContext] = {}
        self._space_run: dict[str, str] = {}
        self._confirms: dict[str, tuple[asyncio.Event, dict[str, Any]]] = {}
        self._mcp_names: dict[str, tuple[str, str]] = {}

    # -- public entry points ----------------------------------------------

    def ensure_manager(self, space_id: str) -> dict[str, Any]:
        return self.store.upsert_agent(
            space_id,
            "Manager",
            role_prompt="Coordinate the crew and answer the user.",
            icon="M",
            color=MANAGER_COLOR,
        )

    async def on_user_message(self, space_id: str, text: str) -> dict[str, Any]:
        msg = self.store.add_message(space_id, "user", text)
        self.bus.emit(space_id, "message", {"message": msg})

        space = self.store.get_space(space_id)
        if space is None:
            return {"error": "unknown space"}
        if active_provider() is None and not (space.get("model") or "").strip():
            note = (
                "No model provider is configured. Start OpenVault on :5000 (keys stay there),"
                " paste a key in Providers, or set CREW_MODEL / an *_API_KEY, then retry."
            )
            sysmsg = self.store.add_message(space_id, "system", note)
            self.bus.emit(space_id, "message", {"message": sysmsg})
            return {"error": note}

        active_run = self._space_run.get(space_id)
        if active_run:
            manager = self.ensure_manager(space_id)
            self.switch.mailbox(manager["id"]).put(
                a2a.Envelope(
                    id=msg["id"],
                    kind=a2a.USER,
                    from_id="operator",
                    from_name="operator",
                    to_id=manager["id"],
                    to_name="Manager",
                    text=text,
                    seq=int(msg["seq"]),
                )
            )
            return {"run_id": active_run, "queued": True}

        manager = self.ensure_manager(space_id)
        return {"run_id": self._start_run(space_id, manager)}

    def _start_run(self, space_id: str, manager: dict[str, Any]) -> str:
        run = self.store.create_run(space_id)
        ctx = RunContext(id=run["id"], space_id=space_id)
        self._runs[run["id"]] = ctx
        self._space_run[space_id] = run["id"]
        task = asyncio.create_task(self._manager_run(ctx, manager))
        ctx.tasks.add(task)
        task.add_done_callback(ctx.tasks.discard)
        return run["id"]

    def decide_confirm(
        self, confirm_id: str, approved: bool, *, takeover: bool = False
    ) -> dict[str, Any] | None:
        row = self.store.decide_confirm(confirm_id, approved, takeover=takeover)
        pair = self._confirms.get(confirm_id)
        if pair is not None:
            event, holder = pair
            if takeover:
                holder["verdict"] = "takeover"
                holder["approved"] = False
            else:
                holder["verdict"] = "approved" if approved else "denied"
                holder["approved"] = approved
            event.set()
        if row is not None:
            self.bus.emit(row["space_id"], "confirm", {"confirm": row})
        return row

    def cancel_run(self, run_id: str) -> bool:
        ctx = self._runs.get(run_id)
        if ctx is None:
            return False
        for task in list(ctx.tasks):
            task.cancel()
        return True

    async def shutdown(self) -> None:
        for ctx in list(self._runs.values()):
            for task in list(ctx.tasks):
                task.cancel()

    # -- run lifecycle -----------------------------------------------------

    async def _manager_run(self, ctx: RunContext, manager: dict[str, Any]) -> None:
        space_id = ctx.space_id
        status = "done"
        try:
            await self._agent_loop(ctx, manager, is_manager=True)
        except asyncio.CancelledError:
            status = "cancelled"
        except LLMError as exc:
            status = "failed"
            msg = self.store.add_message(space_id, "system", f"Run failed: {exc}")
            self.bus.emit(space_id, "message", {"message": msg})
        except Exception as exc:  # noqa: BLE001 - a run must never die silently
            status = "failed"
            msg = self.store.add_message(
                space_id, "system", f"Run crashed: {type(exc).__name__}: {exc}"
            )
            self.bus.emit(space_id, "message", {"message": msg})
        finally:
            leftovers = [
                t for t in list(ctx.tasks) if t is not asyncio.current_task() and not t.done()
            ]
            if leftovers:
                if status in {"cancelled", "failed"}:
                    for task in leftovers:
                        task.cancel()
                    await asyncio.gather(*leftovers, return_exceptions=True)
                else:
                    # The Manager already wrote the user-facing answer; teammates
                    # get a beat to land their report in the transcript. Anything
                    # still running after that is cancelled and named. Letting it
                    # run on produced an orphan that kept writing to a finished
                    # run's stats, could no longer be cancelled, and survived
                    # shutdown - all of it invisible to the operator.
                    _done, straggling = await asyncio.wait(
                        leftovers, timeout=min(30, self.settings.llm_timeout_s)
                    )
                    if straggling:
                        stuck = self._busy_names(space_id, manager["id"])
                        for task in straggling:
                            task.cancel()
                        await asyncio.gather(*straggling, return_exceptions=True)
                        note = (
                            "Cut off after the grace window: "
                            + (", ".join(stuck) if stuck else f"{len(straggling)} teammate task(s)")
                            + ". Their work is not in the answer above."
                        )
                        cut = self.store.add_message(space_id, "system", note)
                        self.bus.emit(space_id, "message", {"message": cut})
            ctx.closed = True
            self._space_run.pop(space_id, None)
            run = self.store.update_run(ctx.id, status=status, stats=ctx.stats)
            self._set_status(manager["id"], "idle")
            if run is not None:
                self.bus.emit(space_id, "run", {"run_id": ctx.id, "status": status, "stats": ctx.stats})
            # Popped last: while the context is still registered, cancel_run and
            # shutdown can reach every task this run created.
            self._runs.pop(ctx.id, None)
            if status == "done" and self.switch.mailbox(manager["id"]).has_kind(a2a.USER):
                # An operator message that landed while this run was finishing
                # was acknowledged as queued. Without this, no run would ever
                # read it and the acknowledgement would have been a lie.
                self._start_run(space_id, manager)

    async def _teammate_run(self, ctx: RunContext, row: dict[str, Any], brief: str) -> None:
        name = row["name"]
        try:
            await self._agent_loop(ctx, row, is_manager=False, brief=brief)
        except asyncio.CancelledError:
            self._set_status(row["id"], "idle")
        except LLMError as exc:
            self._set_status(row["id"], "failed")
            self._deliver_a2a(ctx, row, "Manager", f"I failed: {exc}", kind=a2a.REPORT)
        except Exception as exc:  # noqa: BLE001
            self._set_status(row["id"], "failed")
            self._deliver_a2a(
                ctx, row, "Manager", f"I crashed: {type(exc).__name__}: {exc}", kind=a2a.REPORT
            )
        finally:
            # Anyone blocked in ask_agent on this teammate is told it stopped,
            # instead of burning the whole timeout on a task that will never
            # answer (KB R-0011: a silent stall is a lie).
            for asker_id in self.switch.abandon(row["id"], f"{name} stopped running"):
                self._note_abandoned(ctx, asker_id, name)
            # Backstop for anything delivered after the loop's last drain: a
            # queued message with nobody left to read it is silently lost work.
            if not self.switch.mailbox(row["id"]).empty():
                self._handle(row).task = None
                self._wake_if_idle(ctx, self.store.get_agent(row["id"]) or row)

    # -- the agent loop ----------------------------------------------------

    async def _agent_loop(
        self,
        ctx: RunContext,
        row: dict[str, Any],
        *,
        is_manager: bool,
        brief: str | None = None,
    ) -> None:
        space = self.store.get_space(ctx.space_id) or {}
        model, api_base, label = self._provider(space, row)
        self._set_status(row["id"], "thinking")
        last_user = ""
        for m in reversed(self.store.list_messages(ctx.space_id, limit=10_000)):
            if m["role"] == "user":
                last_user = m["content"]
                break
        detected = detect.plan(last_user) if is_manager else None
        if detected is not None:
            self.bus.emit(ctx.space_id, "detect", {"plan": detected.as_dict()})

        if is_manager:
            # Stable first block so OpenRouter/Anthropic prefix-cache hits.
            # Roster + detect change every turn and stay in the second system message.
            messages: list[dict[str, Any]] = [
                {
                    "role": "system",
                    "content": MANAGER_CHARTER,
                    "cache_control": {"type": "ephemeral"},
                },
                {
                    "role": "system",
                    "content": (
                        self._roster_note(ctx.space_id).lstrip()
                        + "\n\n"
                        + detect.render(detected or detect.plan(""))
                    ),
                },
            ]
            messages.extend(self._manager_history(ctx.space_id))
        else:
            system = TEAMMATE_CHARTER.format(name=row["name"], role=row["role_prompt"] or "helper")
            tone = self._tone_block()
            if tone:
                system = system + "\n\n" + tone
            messages = [
                {
                    "role": "system",
                    "content": system,
                    "cache_control": {"type": "ephemeral"},
                },
            ]
            messages.extend(self._teammate_history(ctx.space_id, row, brief))

        # Everything at or below this seq is already inside `messages`. An
        # envelope queued before the rebuild would otherwise be read twice.
        floor = self.store.max_seq(ctx.space_id)
        mailbox = self.switch.mailbox(row["id"])

        final_text: str | None = None
        upstream = model
        for _step in range(self.settings.max_steps_per_agent):
            if ctx.stats["llm_calls"] >= self.settings.max_llm_calls_per_run:
                note = (
                    f"Run budget exhausted ({ctx.stats['llm_calls']} model calls);"
                    " stopping here."
                )
                msg = self.store.add_message(ctx.space_id, "system", note)
                self.bus.emit(ctx.space_id, "message", {"message": msg})
                break

            for env in self._note_questions(row["id"], mailbox.drain(floor)):
                floor = max(floor, env.seq)
                messages.append({"role": "user", "content": env.render()})

            stream_cb = None
            # litellm cannot parse ollama NDJSON chunks; streaming that path
            # surfaces as APIConnectionError and kills the run (Night 2026-08-23).
            if is_manager and not str(model).startswith("ollama/"):
                agent_id = row["id"]

                async def _cb(text: str, _agent_id: str = agent_id) -> None:
                    self.bus.emit(ctx.space_id, "delta", {"agent_id": _agent_id, "text": text})

                stream_cb = _cb

            result: LLMResult = await self._llm(
                model,
                messages,
                tools=self._toolspecs(is_manager, row),
                api_base=api_base,
                timeout=self.settings.llm_timeout_s,
                stream_cb=stream_cb,
            )
            if result.model:
                upstream = result.model
            self._bump_stats(ctx, result)

            if result.tool_calls:
                self._set_status(row["id"], "acting")
                messages.append(_assistant_tool_msg(result))
                finished: str | None = None
                for tc in result.tool_calls:
                    try:
                        outcome = await self._execute_tool(ctx, row, tc, is_manager=is_manager)
                    except _AgentFinished as done:
                        finished = done.summary
                        outcome = "finished"
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id, "content": outcome[:8000]}
                    )
                if finished is not None:
                    final_text = finished
                    break
                self._set_status(row["id"], "thinking")
                continue

            text = result.text.strip()
            # Something may have arrived while this agent was producing its
            # answer. Finishing now would leave it unread in the mailbox, so
            # take it and keep going; the step budget still bounds the loop,
            # and `text` is retained as the fallback answer if it runs out.
            pending = self._note_questions(row["id"], mailbox.drain(floor))
            if pending:
                messages.append({"role": "assistant", "content": text})
                for env in pending:
                    floor = max(floor, env.seq)
                    messages.append({"role": "user", "content": env.render()})
                final_text = text
                continue
            final_text = text
            break

        if final_text is None:
            final_text = "(stopped without a final answer - step budget reached)"

        if is_manager:
            msg = self.store.add_message(
                ctx.space_id,
                "assistant",
                final_text,
                agent_id=row["id"],
                meta={
                    "model": upstream,
                    "route": model,
                    "provider": label,
                    "run_id": ctx.id,
                    "cost_usd": ctx.stats.get("cost_usd"),
                },
            )
            self.bus.emit(ctx.space_id, "message", {"message": msg})
            self._set_status(row["id"], "idle")
        else:
            from CortexOS.execution.subagent_contract import sanitize_subagent_final

            final_text = sanitize_subagent_final(final_text)["content"]
            self._deliver_a2a(ctx, row, "Manager", final_text, kind=a2a.REPORT)
            self._set_status(row["id"], "done")
            if int(row.get("verify") or 0) and str(row.get("verify_criteria") or "").strip():
                await self._spawn_verifier(ctx, row, final_text)

    # -- tools -------------------------------------------------------------

    def _toolspecs(self, is_manager: bool, row: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        def spec(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            }

        specs = [
            spec(
                "netie_board",
                "Read the live Netie CLAIMS+RUNTIME ticket board. Report seated vs unseated."
                " Do not implement HELD/SEATED tickets from another writer. Do not spawn"
                " one Cursor cloud agent per issue. Human is money and decision authority.",
                {},
                [],
            ),
            spec(
                "desk_status",
                "Live PRs (gh), Gmail IMAP headers or drop-file status, connector map, and "
                "whether the Cursor API key is set (never the secret). Use this when the "
                "user asks to check PRs, mail, MCP, login, or keys. Do not auto-merge or send.",
                {},
                [],
            ),
            spec(
                "estate_status",
                "List github.com/Netie-AI repos with adaptive surfaces and which production "
                "domains apply (Security/Reliability/Infra/Architecture/Observability/Surface). "
                "Static catalog plus optional live gh. Do not clone every repo. Do not auto-merge.",
                {},
                [],
            ),
            spec(
                "ship_gate",
                "Run the deterministic production ship-gate. repo is a Netie-AI slug, "
                "full_name, or 'all'. Fail closed on missing evidence. Skip is not pass. "
                "File presence is not SOC2/HIPAA/GDPR. Spawn domain teammates only for FAIL "
                "items. Do not auto-merge.",
                {"repo": {"type": "string", "description": "Cortex, Netie-AI/AIM, or all"}},
                ["repo"],
            ),
            spec(
                "cortex_ask",
                "Ask the governed Cortex engine a data question. Returns the answer with its"
                " badge, sources and audit id. The engine may abstain; report that honestly.",
                {"question": {"type": "string"}},
                ["question"],
            ),
            spec(
                "send_to_agent",
                "Send a message to another agent in this space by name and keep working."
                " Use this to hand information over. It does NOT wait for an answer - use"
                " ask_agent when you need one. Name '*' broadcasts to everyone.",
                {"name": {"type": "string"}, "message": {"type": "string"}},
                ["name", "message"],
            ),
            spec(
                "ask_agent",
                "Ask one named agent a question and WAIT for that agent's answer. The"
                " reply is correlated for you, so another teammate's report cannot be"
                " mistaken for the answer. Returns their answer, or says plainly that"
                " they did not answer and why.",
                {
                    "name": {"type": "string"},
                    "question": {"type": "string"},
                    "timeout_seconds": {"type": "integer", "minimum": 5, "maximum": 300},
                },
                ["name", "question"],
            ),
            spec(
                "broadcast",
                "Send one message to every other agent in this space at once.",
                {"message": {"type": "string"}},
                ["message"],
            ),
            spec(
                "wait_for_replies",
                "Wait for anything addressed to you (briefs, questions, reports). Each"
                " item comes back labelled with who sent it. Returns immediately when no"
                " teammate is still working, so you never idle out a timeout for nothing.",
                {"timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120}},
                [],
            ),
        ]
        if is_manager:
            specs.append(
                spec(
                    "spawn_agent",
                    "Spawn a teammate that starts working its brief immediately and"
                    " reports back to you. Name them for THIS job. Optional capability"
                    " copies a prompt template (Ticket, PRD, Epic, Gate, ...)."
                    " Optional allow_tools / deny_tools restrict the shared connector pool."
                    " Optional verify=true needs verify_criteria (explicit list).",
                    {
                        "name": {"type": "string", "description": "job-specific unique name"},
                        "role": {
                            "type": "string",
                            "description": "custom system role; overrides capability",
                        },
                        "capability": {
                            "type": "string",
                            "description": "optional template name to copy the role prompt from",
                        },
                        "brief": {"type": "string", "description": "the concrete task"},
                        "allow_tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "if set, only these MCP/internal tool names are offered",
                        },
                        "deny_tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "never offer or run these tool names",
                        },
                        "verify": {
                            "type": "boolean",
                            "description": "after they finish, spawn a verifier teammate",
                        },
                        "verify_criteria": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "explicit pass/fail checks; required when verify is true",
                        },
                        "skills": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "skill file names under data/crew/skills to copy into the role",
                        },
                        "model": {
                            "type": "string",
                            "description": "optional OpenVault/FreeRoute model for this teammate; grok-fast is rewritten to grok-4.6",
                        },
                    },
                    ["name", "brief"],
                )
            )
            specs.append(
                spec(
                    "rename_agent",
                    "Rename a teammate in this space. Manager cannot be renamed.",
                    {"old_name": {"type": "string"}, "new_name": {"type": "string"}},
                    ["old_name", "new_name"],
                )
            )
        else:
            specs.append(
                spec(
                    "finish",
                    "Finish your task and deliver this summary to the Manager.",
                    {"summary": {"type": "string"}},
                    ["summary"],
                )
            )

        allowed, denied = _grants(row)
        self._mcp_names = {}
        for server, tool in self.mcp.tool_catalog():
            tool_name = str(tool.get("name", ""))
            public = _sanitize(f"mcp_{server}_{tool_name}")
            names = {tool_name, public, f"{server}.{tool_name}"}
            self._mcp_names[public] = (server, tool_name)
            if denied and names & denied:
                continue
            if allowed and not (names & allowed):
                continue
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": public,
                        "description": f"[{server}] " + (tool.get("description") or "")[:500],
                        "parameters": tool.get("inputSchema")
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return specs

    async def _execute_tool(
        self, ctx: RunContext, row: dict[str, Any], tc: ToolCall, *, is_manager: bool
    ) -> str:
        name, args = tc.name, tc.args
        allowed, denied = _grants(row)
        if name in self._mcp_names:
            server, real_tool = self._mcp_names[name]
            client = self.mcp.clients.get(server)
            armed = bool(client and client.spec.armed)
            decision, reason = policy.decide(
                real_tool,
                server=server,
                armed=armed,
                master_on=self.mcp.master_on,
                allowed=allowed,
                denied=denied,
            )
            if decision == policy.CONFIRM:
                verdict = await self._await_confirm(ctx, row, f"{server}.{real_tool}", args)
                if verdict == "takeover":
                    msg = (
                        "OPERATOR TOOK OVER AUTH. Do not type passwords, OTP, or 2FA. "
                        "Wait until the page is already logged in, then continue with that session."
                    )
                    self._persist_tool(ctx, row, f"{server}.{real_tool}", args, msg)
                    return msg
                if verdict != "approved":
                    decision, reason = policy.DENY, "operator denied (or approval timed out)"
                else:
                    decision, reason = policy.ALLOW, "operator approved"
            if decision == policy.DENY:
                self._persist_tool(ctx, row, f"{server}.{real_tool}", args, f"denied: {reason}")
                return f"DENIED: {reason}"
            assert client is not None
            # The server may be armed but suspended for idleness. Wake it only
            # now that the call is approved: a denied call must never be the
            # reason a desktop-automation process starts.
            await self.mcp.ensure_ready(server)
            try:
                outcome = await client.call(real_tool, args)
            except (RuntimeError, TimeoutError, OSError) as exc:
                outcome = f"TOOL ERROR: {exc}"
            self._persist_tool(ctx, row, f"{server}.{real_tool}", args, outcome)
            return outcome

        decision, reason = policy.decide(
            name,
            server=None,
            armed=False,
            master_on=False,
            allowed=allowed,
            denied=denied,
        )
        if decision != policy.ALLOW:
            return f"DENIED: {reason}"

        if name == "netie_board":
            agent = row
            from CortexOS.crew.board import snapshot

            board = snapshot()
            lines = [
                f"{len(board['tickets'])} tickets, seated={board['seated']} unseated={board['unseated']}. {board['law']}"
            ]
            for item in board["tickets"][:30]:
                lines.append(
                    f"- {item.get('ticket')} {item.get('role')} write={item.get('may_write')} pr={item.get('owner_pr')}"
                )
            text = "\n".join(lines)
            msg = self.store.add_message(
                ctx.space_id,
                "tool",
                text,
                agent_id=agent["id"],
                meta={"tool": "netie_board"},
            )
            self.bus.emit(ctx.space_id, "message", {"message": msg})
            return text

        if name == "desk_status":
            from CortexOS.crew.desk import render, snapshot

            mcp_rows = self.mcp.status()
            uacc = next((row for row in mcp_rows if row.get("name") == "uacc"), {})
            snap = snapshot(
                uacc_enabled=bool(uacc.get("enabled")),
                uacc_armed=bool(uacc.get("armed")),
            )
            text = render(snap)
            self._persist_tool(ctx, row, "desk_status", {}, text)
            return text

        if name == "estate_status":
            from CortexOS.crew.estate import render as estate_render
            from CortexOS.crew.estate import snapshot as estate_snapshot

            text = estate_render(estate_snapshot())
            self._persist_tool(ctx, row, "estate_status", {}, text)
            return text

        if name == "ship_gate":
            from CortexOS.crew.ship_gate import render_slug

            repo = str(args.get("repo", "")).strip() or "all"
            text = render_slug(repo)
            self._persist_tool(ctx, row, "ship_gate", {"repo": repo}, text)
            return text

        if name == "cortex_ask":
            question = str(args.get("question", "")).strip()
            envelope = await self.bridge.ask(question)
            msg = self.store.add_message(
                ctx.space_id,
                "tool",
                envelope.get("answer", ""),
                agent_id=row["id"],
                meta={"tool": "cortex_ask", "args": {"question": question}, "envelope": envelope},
            )
            self.bus.emit(ctx.space_id, "message", {"message": msg})
            return (
                f"answer: {envelope.get('answer', '')}\n"
                f"badge: {envelope.get('badge')} route: {envelope.get('route')}"
                f" audit_id: {envelope.get('audit_id')} rows: {envelope.get('row_count')}"
            )

        if name == "spawn_agent":
            return await self._spawn(ctx, row, args)

        if name == "rename_agent":
            old = str(args.get("old_name", "")).strip()
            new = _sanitize(str(args.get("new_name", "")).strip())
            if old.lower() == "manager" or new.lower() == "manager":
                return "DENIED: Manager cannot be renamed"
            renamed = self.store.rename_agent(ctx.space_id, old, new)
            if renamed is None:
                return f"DENIED: could not rename '{old}' to '{new}'"
            self.bus.emit(ctx.space_id, "agent", {"agent": renamed})
            return f"renamed {old} -> {renamed['name']}"

        if name == "send_to_agent":
            target_name = str(args.get("name", "")).strip()
            message = str(args.get("message", ""))
            if target_name == a2a.BROADCAST_TARGET:
                return self._broadcast(ctx, row, message)
            target = self.store.get_agent_by_name(ctx.space_id, target_name)
            if target is None:
                known = ", ".join(a["name"] for a in self.store.list_agents(ctx.space_id))
                return f"no agent named '{target_name}' here (known: {known})"
            self._deliver_a2a(ctx, row, target_name, message, kind=a2a.TELL)
            fresh = self.store.get_agent(target["id"]) or target
            return f"delivered to {target_name} (they are {fresh.get('status')})"

        if name == "broadcast":
            return self._broadcast(ctx, row, str(args.get("message", "")))

        if name == "ask_agent":
            return await self._ask_agent(ctx, row, args)

        if name == "wait_for_replies":
            timeout = min(int(args.get("timeout_seconds") or 60), 120)
            mailbox = self.switch.mailbox(row["id"])
            envs = mailbox.drain()
            if not envs:
                busy = self._busy_names(ctx.space_id, row["id"])
                if not busy:
                    return (
                        "nothing is waiting for you and no teammate is still working."
                        " Do not wait again - act on what you already have."
                    )
                env = await mailbox.get(timeout)
                if env is None:
                    return (
                        f"nothing arrived within {timeout}s; still working:"
                        f" {', '.join(busy)}"
                    )
                envs = [env, *mailbox.drain()]
            return a2a.render_all(envs)

        if name == "finish":
            raise _AgentFinished(str(args.get("summary", "")))

        return f"DENIED: unknown tool '{name}'"

    async def _spawn(self, ctx: RunContext, row: dict[str, Any], args: dict[str, Any]) -> str:
        agents = self.store.list_agents(ctx.space_id)
        if len(agents) >= self.settings.max_agents_per_space:
            return f"DENIED: agent cap reached ({self.settings.max_agents_per_space})"
        name = _sanitize(str(args.get("name", "")).strip() or f"agent-{len(agents)}")
        role = str(args.get("role", "")).strip()
        brief = str(args.get("brief", "")).strip()
        cap_name = str(args.get("capability", "")).strip()
        preset = roles.by_name(cap_name) or roles.by_name(name)
        if preset is not None and not role:
            role = preset.role
        verify = bool(args.get("verify"))
        criteria = _str_list(args.get("verify_criteria"))
        if verify and not criteria:
            verify = False
        skill_names = _str_list(args.get("skills"))
        if preset is not None:
            skill_names = list(dict.fromkeys([*preset.skills, *skill_names]))
        if skill_names:
            from CortexOS.crew.board import read_skill

            bits = []
            for title in skill_names:
                body = read_skill(self.settings.data_dir / "skills", title).strip()
                if body:
                    bits.append(f"Skill {title}:\n{body[:3500]}")
            if bits:
                role = (role + "\n\n" if role else "") + "\n\n".join(bits)
        model = str(args.get("model") or "").strip()
        color = AGENT_COLORS[len(agents) % len(AGENT_COLORS)]
        teammate = self.store.upsert_agent(
            ctx.space_id,
            name,
            role_prompt=role,
            icon=(preset.icon if preset is not None else (name[:1] or "A").upper()),
            color=color,
            spawned_by=row["id"],
            allow_tools=_str_list(args.get("allow_tools")),
            deny_tools=_str_list(args.get("deny_tools")),
            capability=(preset.name if preset is not None else cap_name),
            verify=verify,
            verify_criteria=criteria,
            model=model,
            skills=skill_names,
        )
        self.bus.emit(ctx.space_id, "agent", {"agent": teammate})
        self._deliver_a2a(ctx, row, name, brief, kind=a2a.BRIEF, wake=False)
        task = asyncio.create_task(self._teammate_run(ctx, teammate, brief))
        # Register before yielding. A task that has been created but has not
        # run yet still reads as status 'idle' in the store, so without this
        # a send_to_agent on the very next step would start a second copy of
        # the same teammate.
        self._handle(teammate).task = task
        ctx.tasks.add(task)
        task.add_done_callback(ctx.tasks.discard)
        extra = ""
        if verify:
            extra = "; verifier will run after they finish"
        elif bool(args.get("verify")) and not criteria:
            extra = "; verify skipped (no explicit criteria; refusing rubber-stamp)"
        return f"spawned {name}; they are working the brief and will report back{extra}"

    async def _spawn_verifier(
        self, ctx: RunContext, worker: dict[str, Any], output: str
    ) -> None:
        if str(worker.get("name") or "").endswith("-verify"):
            return
        agents = self.store.list_agents(ctx.space_id)
        if len(agents) >= self.settings.max_agents_per_space:
            return
        gate = roles.by_name("Gate")
        criteria = _str_list(worker.get("verify_criteria"))
        name = _sanitize(f"{worker['name']}-verify")
        role = (gate.role if gate is not None else "You are the verifier.")
        role += (
            "\nExplicit criteria (fail closed if any miss):\n"
            + "\n".join(f"- {c}" for c in criteria)
            + "\nDo not rubber-stamp. Return passed: true/false and feedback."
        )
        brief = (
            f"Verify this output from {worker['name']} against the criteria.\n\n{output[:6000]}"
        )
        verifier = self.store.upsert_agent(
            ctx.space_id,
            name,
            role_prompt=role,
            icon="V",
            color=AGENT_COLORS[len(agents) % len(AGENT_COLORS)],
            spawned_by=worker.get("spawned_by"),
            deny_tools=["spawn_agent"],
            capability="Gate",
            verify=False,
        )
        self.bus.emit(ctx.space_id, "agent", {"agent": verifier})
        self._deliver_a2a(ctx, worker, name, brief, kind=a2a.BRIEF, wake=False)
        task = asyncio.create_task(self._teammate_run(ctx, verifier, brief))
        self._handle(verifier).task = task
        ctx.tasks.add(task)
        task.add_done_callback(ctx.tasks.discard)

    # -- confirm gate ------------------------------------------------------

    async def _await_confirm(
        self, ctx: RunContext, row: dict[str, Any], tool: str, args: dict[str, Any]
    ) -> str:
        confirm = self.store.create_confirm(
            ctx.space_id, run_id=ctx.id, agent_id=row["id"], tool=tool, args=args
        )
        event = asyncio.Event()
        holder: dict[str, Any] = {"approved": False, "verdict": "denied"}
        self._confirms[confirm["id"]] = (event, holder)
        self.bus.emit(ctx.space_id, "confirm", {"confirm": confirm})
        try:
            await asyncio.wait_for(event.wait(), timeout=self.settings.confirm_timeout_s)
        except TimeoutError:
            self.store.decide_confirm(confirm["id"], False)
            refreshed = self.store.get_confirm(confirm["id"])
            if refreshed is not None:
                self.bus.emit(ctx.space_id, "confirm", {"confirm": refreshed})
            return "denied"
        finally:
            self._confirms.pop(confirm["id"], None)
        return str(holder.get("verdict") or "denied")

    # -- helpers -----------------------------------------------------------

    def _provider(
        self, space: dict[str, Any], row: dict[str, Any] | None = None
    ) -> tuple[str, str | None, str]:
        agent_model = str((row or {}).get("model") or "").strip()
        if agent_model:
            if "grok" in agent_model.lower() and "fast" in agent_model.lower():
                agent_model = "openai/grok-4.6"
            return agent_model, None, "agent-override"
        override = (space.get("model") or "").strip()
        if override:
            return override, None, "space-override"
        provider = active_provider()
        if provider is None:
            raise LLMError(
                "no model provider configured (set ANTHROPIC_API_KEY, OPENROUTER_API_KEY,"
                " DEEPSEEK_API_KEY, OPENAI_API_KEY, or run Ollama)"
            )
        return provider.model, provider.api_base, provider.label

    def _handle(self, row: dict[str, Any]) -> AgentHandle:
        handle = self._handles.get(row["id"])
        if handle is None:
            handle = AgentHandle(row=row)
            self._handles[row["id"]] = handle
        return handle

    def _set_status(self, agent_id: str, status: str) -> None:
        row = self.store.set_agent_status(agent_id, status)
        if row is not None:
            self.bus.emit(row["space_id"], "agent", {"agent": row})

    def _note_questions(self, agent_id: str, envs: list[a2a.Envelope]) -> list[a2a.Envelope]:
        """Record which questions this agent now owes an answer to.

        The next message it sends back to that asker is stamped with the
        question id, so correlation is exact instead of "whatever this agent
        said next". Returns the envelopes unchanged so it can wrap a drain.
        """
        for env in envs:
            if env.kind == a2a.ASK:
                self._open_questions.setdefault(agent_id, {})[env.from_id] = env.id
        return envs

    def _busy_names(self, space_id: str, me: str) -> list[str]:
        """Teammates that are still expected to say something.

        Stored status alone is not enough. A teammate whose task has been
        created but has not had its first tick yet still reads as 'idle', so
        judging by status would tell a Manager that just spawned someone that
        nobody is working - and wait_for_replies would return before the
        teammate had even started.
        """
        out: list[str] = []
        for a in self.store.list_agents(space_id):
            if a["id"] == me:
                continue
            handle = self._handles.get(a["id"])
            running = handle is not None and handle.task is not None and not handle.task.done()
            if running or a["status"] in {"thinking", "acting"}:
                out.append(a["name"])
        return out

    def _broadcast(self, ctx: RunContext, row: dict[str, Any], message: str) -> str:
        """Tell everyone at once, without summoning anyone.

        wake=False on purpose: a broadcast that restarted every teammate that
        had already finished would turn one FYI into a stampede of reruns. It
        lands in the transcript and in each mailbox, and is read by whoever is
        working or is next woken for a real reason.
        """
        sent: list[str] = []
        for other in self.store.list_agents(ctx.space_id):
            if other["id"] == row["id"]:
                continue
            self._deliver_a2a(
                ctx, row, other["name"], message, kind=a2a.BROADCAST, wake=False
            )
            sent.append(other["name"])
        if not sent:
            return "nobody else is in this space yet; nothing was sent"
        return "broadcast to " + ", ".join(sent)

    async def _ask_agent(
        self, ctx: RunContext, row: dict[str, Any], args: dict[str, Any]
    ) -> str:
        target_name = str(args.get("name", "")).strip()
        question = str(args.get("question", ""))
        timeout = min(max(int(args.get("timeout_seconds") or 90), 5), 300)
        target = self.store.get_agent_by_name(ctx.space_id, target_name)
        if target is None:
            known = ", ".join(a["name"] for a in self.store.list_agents(ctx.space_id))
            return f"no agent named '{target_name}' here (known: {known})"
        if target["id"] == row["id"]:
            return "DENIED: you cannot ask yourself"
        if self.switch.would_deadlock(row["id"], target["id"]):
            # Arming the other half of the cycle guarantees both sides wait out
            # their timeouts and neither ever speaks.
            return (
                f"DENIED: {target_name} is already waiting on an answer from you."
                " Answer them with send_to_agent first; asking back would leave"
                " you both waiting."
            )
        future = self.switch.open_ask(row["id"], target["id"], "")
        msg = self._deliver_a2a(ctx, row, target_name, question, kind=a2a.ASK)
        if msg is not None:
            # The stored message id IS the question id, so the answer can be
            # matched to this question rather than merely to its sender.
            self.switch.rekey_ask(row["id"], target["id"], str(msg["id"]))
        try:
            kind, answer = await asyncio.wait_for(future, timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError):
            self.switch.close_ask(row["id"], target["id"])
            fresh = self.store.get_agent(target["id"]) or target
            return (
                f"{target_name} did not answer within {timeout}s"
                f" (they are {fresh.get('status')}). Proceed without them, or ask again."
            )
        if kind == a2a.NO_ANSWER:
            return f"{target_name} {answer}"
        # REPORT is an answering kind on the switchboard (keyed by asker+target,
        # so a third agent's report cannot steal this). Calling it anything
        # else made ask_agent look unanswered while the finding sat in the
        # transcript.
        return f"{target_name} answered: {answer}"

    def _note_abandoned(self, ctx: RunContext, asker_id: str, dead_name: str) -> None:
        """Say in the transcript that a wait ended because the target stopped."""
        asker = self.store.get_agent(asker_id)
        if asker is None:
            return
        msg = self.store.add_message(
            ctx.space_id,
            "system",
            f"{asker['name']} was waiting on {dead_name}, which stopped running.",
        )
        self.bus.emit(ctx.space_id, "message", {"message": msg})

    def _deliver_a2a(
        self,
        ctx: RunContext,
        from_row: dict[str, Any],
        to_name: str,
        text: str,
        *,
        kind: str = a2a.TELL,
        reply_to: str | None = None,
        wake: bool = True,
    ) -> dict[str, Any] | None:
        space_id = ctx.space_id
        target = self.store.get_agent_by_name(space_id, to_name)
        if target is None and to_name == "Manager":
            target = self.ensure_manager(space_id)
        if reply_to is None and target is not None and kind in {
            a2a.TELL,
            a2a.REPLY,
            a2a.REPORT,
        }:
            # This agent owed that agent an answer: stamp the question id so the
            # switchboard matches the reply to the question, not to the sender.
            owed = self._open_questions.get(from_row["id"])
            if owed:
                reply_to = owed.pop(target["id"], None)
                if reply_to:
                    kind = a2a.REPLY
        msg = self.store.add_message(
            space_id,
            "agent",
            text,
            agent_id=from_row["id"],
            to_agent_id=target["id"] if target else None,
            meta={
                "a2a": {
                    "kind": kind,
                    "from": from_row["name"],
                    "to": target["name"] if target else to_name,
                    "reply_to": reply_to,
                }
            },
        )
        self.bus.emit(space_id, "message", {"message": msg})
        if target is None:
            return msg
        answered = self.switch.deliver(
            a2a.Envelope(
                id=msg["id"],
                kind=kind,
                from_id=from_row["id"],
                from_name=from_row["name"],
                to_id=target["id"],
                to_name=target["name"],
                text=text,
                seq=int(msg["seq"]),
                reply_to=reply_to,
            )
        )
        if wake and not answered:
            self._wake_if_idle(ctx, target)
        return msg

    def _wake_if_idle(self, ctx: RunContext, target: dict[str, Any]) -> None:
        """Restart a stopped teammate so a message to it is not left unread.

        The teammate rebuilds its context from the transcript, so it comes back
        knowing its brief and everything it already said. It is not re-briefed
        from scratch with only the newest line, which is what used to happen.
        """
        if target["name"] == "Manager":
            return  # the manager is driven by the run loop, never restarted here
        handle = self._handle(target)
        if handle.task is not None and not handle.task.done():
            return
        fresh = self.store.get_agent(target["id"]) or target
        if fresh["status"] in {"thinking", "acting"}:
            return
        task = asyncio.create_task(self._teammate_run(ctx, fresh, ""))
        handle.task = task
        ctx.tasks.add(task)
        task.add_done_callback(ctx.tasks.discard)

    def _persist_tool(
        self,
        ctx: RunContext,
        row: dict[str, Any],
        tool: str,
        args: dict[str, Any],
        outcome: str,
    ) -> None:
        msg = self.store.add_message(
            ctx.space_id,
            "tool",
            outcome[:4000],
            agent_id=row["id"],
            meta={"tool": tool, "args": args},
        )
        self.bus.emit(ctx.space_id, "message", {"message": msg})

    def _bump_stats(self, ctx: RunContext, result: LLMResult) -> None:
        if ctx.closed:
            return  # the run already reported a terminal status; do not undo it
        ctx.stats["llm_calls"] += 1
        ctx.stats["prompt_tokens"] += result.prompt_tokens or 0
        ctx.stats["completion_tokens"] += result.completion_tokens or 0
        ctx.stats["cost_usd"] = round(ctx.stats["cost_usd"] + (result.cost_usd or 0.0), 6)
        self.store.update_run(ctx.id, stats=ctx.stats)
        self.bus.emit(
            ctx.space_id, "run", {"run_id": ctx.id, "status": "running", "stats": ctx.stats}
        )

    def _roster_note(self, space_id: str) -> str:
        agents = self.store.list_agents(space_id)
        mcp_tools = len(self.mcp.tool_catalog())
        names = ", ".join(f"{a['name']} ({a['status']})" for a in agents) or "just you"
        from CortexOS.crew.board import list_skills

        skills = list_skills(self.settings.data_dir / "skills")
        skill_bit = (
            " Skills: " + ", ".join(s["title"] for s in skills) + "."
            if skills
            else " Skills: none yet (Teach saves markdown under data/crew/skills)."
        )
        tone = self._tone_block()
        tone_bit = " Tone skill: on." if tone else " Tone skill: none (save tone.md via Teach)."
        return (
            f"\n\nCurrent crew: {names}."
            f" Computer control: off ({mcp_tools} MCP tools registered, none armed)."
            f" Engine: {self.bridge.base_url} (cortex_ask). Models: OpenVault FreeRoute."
            f"{skill_bit}{tone_bit}"
        )

    def _tone_block(self) -> str:
        from CortexOS.crew.board import read_skill

        body = read_skill(self.settings.data_dir / "skills", "tone").strip()
        if not body:
            return ""
        return "Tone (from skills/tone.md):\n" + body[:4000]

    def _manager_history(self, space_id: str, limit: int = 40) -> list[dict[str, Any]]:
        rows = self.store.list_messages(space_id, limit=10_000)
        agents = {a["id"]: a["name"] for a in self.store.list_agents(space_id)}
        out: list[dict[str, Any]] = []
        for m in rows:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                out.append({"role": "assistant", "content": m["content"]})
            elif m["role"] == "agent":
                frm = agents.get(m["agent_id"], "agent")
                to = agents.get(m["to_agent_id"], "space")
                kind = ((m.get("meta") or {}).get("a2a") or {}).get("kind") or "message"
                out.append(
                    {"role": "user", "content": f"[a2a {kind}] {frm} -> {to}: {m['content']}"}
                )
        return _openable(out[-limit:])

    def _teammate_history(
        self, space_id: str, row: dict[str, Any], brief: str | None, limit: int = 40
    ) -> list[dict[str, Any]]:
        """Rebuild one teammate's context from the transcript.

        A teammate that had finished and was then sent another message used to
        be restarted with nothing but that message: it had forgotten its own
        brief, its own findings, and who it had already talked to, and it
        answered as if the conversation had never happened. The transcript is
        the durable record of a space, so read the teammate's side of it back
        rather than keeping a second copy in memory that dies with the task.
        """
        me = row["id"]
        agents = {a["id"]: a["name"] for a in self.store.list_agents(space_id)}
        out: list[dict[str, Any]] = []
        owed: dict[str, str] = {}
        for m in self.store.list_messages(space_id, limit=10_000):
            meta = m.get("meta") or {}
            if m["role"] == "agent":
                if m["to_agent_id"] == me:
                    kind = (meta.get("a2a") or {}).get("kind") or a2a.TELL
                    frm = agents.get(m["agent_id"], "someone")
                    lead = a2a.lead_for(kind)
                    if kind == a2a.ASK:
                        owed[m["agent_id"]] = str(m["id"])
                    out.append({"role": "user", "content": f"[{lead} from {frm}] {m['content']}"})
                elif m["agent_id"] == me:
                    owed.pop(m["to_agent_id"], None)
                    to = agents.get(m["to_agent_id"], "the space")
                    out.append({"role": "assistant", "content": f"[to {to}] {m['content']}"})
            elif m["role"] == "tool" and m["agent_id"] == me:
                tool = meta.get("tool") or "tool"
                body = str(m["content"])[:2000]
                out.append({"role": "user", "content": f"[{tool} returned] {body}"})
        if owed:
            self._open_questions.setdefault(me, {}).update(owed)
        tail = _openable(out[-limit:])
        if not tail:
            tail = [{"role": "user", "content": brief or "Report in."}]
        return tail


def _openable(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop leading assistant turns from a rebuilt history.

    A tail slice can begin on an assistant turn as soon as an agent has said
    more than it has been told, and the Anthropic API rejects a turn list that
    opens that way. Trimming here stops a context rebuild from surfacing as a
    provider error the operator would read as a crashed agent.
    """
    out = list(turns)
    while out and out[0].get("role") == "assistant":
        out.pop(0)
    return out


def _str_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        text = val.strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        return [p.strip() for p in text.split(",") if p.strip()]
    return []


def _grants(row: dict[str, Any] | None) -> tuple[frozenset[str] | None, frozenset[str] | None]:
    if not row:
        return None, None
    allow = frozenset(_str_list(row.get("allow_tools")))
    deny = frozenset(_str_list(row.get("deny_tools")))
    return (allow or None, deny or None)


def _assistant_tool_msg(result: LLMResult) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": result.text or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": json.dumps(tc.args)},
            }
            for tc in result.tool_calls
        ],
    }
