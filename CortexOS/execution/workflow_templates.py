"""Predetermined multi-phase agent workflows — Cortex's plan catalog.

A template is a *plan*, not a runtime: it names phases, the subagents inside
each phase, which preset prompt each one gets, and which tools it may touch.
``compile_template`` turns it into an ordinary ``AgenticDSLProgram`` so the plan
executes on the existing ``dag_runner`` — this file adds no second orchestrator
(PRODUCT_ROLES lock 5).

Fan-out inside a phase is expressed as sibling nodes with the same inputs, so
the DAG compiler already puts them in one layer; ``run_dag`` in parallel mode
runs that layer concurrently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from CortexOS.execution import prompt_library
from CortexOS.fabrication.dsl_parser import AgenticDSLProgram, DSLNode, NodeType

# Tool bundles a subagent may be granted. Named so a template reads as intent
# ("this one researches") rather than as a tool list to keep in sync by hand.
TOOLSETS: dict[str, tuple[str, ...]] = {
    "research": ("web_search", "web_fetch", "memory_search", "find_skills"),
    "code": ("fs_read", "fs_search", "fs_glob", "git_diff", "find_skills"),
    "code_and_web": (
        "fs_read",
        "fs_search",
        "fs_glob",
        "git_diff",
        "web_search",
        "web_fetch",
        "find_skills",
        "find_mcp",
    ),
    "memory": ("memory_search", "vault_read", "rag_search"),
    "discovery": ("find_skills", "find_mcp", "find_subagents", "web_search"),
    "none": (),
}


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One subagent inside a phase."""

    id: str
    purpose: str  # shown in the UI as "invoked for what"
    prompt_id: str
    toolset: str = "none"
    effort: str = "medium"  # low | medium | high
    max_tokens: int = 1400
    #: Static values merged into the prompt's ``{{vars}}`` at compile time.
    vars: Mapping[str, Any] = field(default_factory=dict)
    #: ``fan_over`` names a key on the phase's input whose list expands this
    #: spec into one agent per item (each item's fields become prompt vars).
    fan_over: str | None = None
    #: Cap on expansion so a template can never fan out unbounded.
    fan_max: int = 8

    @property
    def tools(self) -> tuple[str, ...]:
        return TOOLSETS.get(self.toolset, ())


@dataclass(frozen=True, slots=True)
class PhaseSpec:
    id: str
    title: str
    detail: str
    agents: tuple[AgentSpec, ...]
    #: Phase ids this phase consumes. Empty means it reads the run seed.
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowTemplate:
    id: str
    name: str
    description: str
    #: Lowercase phrases that suggest this template; the recognizer scores them.
    triggers: tuple[str, ...]
    phases: tuple[PhaseSpec, ...]
    #: Prompt variables the caller is expected to supply (beyond ``topic``).
    inputs: tuple[str, ...] = ("topic",)
    default_toolset: str = "none"

    @property
    def agent_count(self) -> int:
        return sum(len(p.agents) for p in self.phases)

    def phase(self, phase_id: str) -> PhaseSpec | None:
        return next((p for p in self.phases if p.id == phase_id), None)


# -- catalog -----------------------------------------------------------------

DEEP_RESEARCH = WorkflowTemplate(
    id="deep_research",
    name="Deep research",
    description=(
        "Split a topic into distinct angles, search each one with web access, "
        "adversarially verify what comes back, then synthesize with citations."
    ),
    triggers=(
        "research", "look into", "find out about", "investigate", "dig into",
        "gather knowledge", "what do we know about", "survey", "literature",
        "invoke subagent to research", "spin off", "sources",
    ),
    default_toolset="research",
    phases=(
        PhaseSpec(
            id="plan",
            title="Plan",
            detail="split the topic into distinct research angles",
            agents=(
                AgentSpec(
                    id="plan-angles",
                    purpose="Decide which angles to research so the searchers do not overlap",
                    prompt_id="research.plan",
                    toolset="none",
                    effort="high",
                    max_tokens=900,
                    vars={"fanout": 4},
                ),
            ),
        ),
        PhaseSpec(
            id="search",
            title="Search",
            detail="one web-searching subagent per angle",
            depends_on=("plan",),
            agents=(
                AgentSpec(
                    id="search",
                    purpose="Search and read sources for one angle",
                    prompt_id="research.search",
                    toolset="research",
                    effort="medium",
                    max_tokens=2000,
                    fan_over="angles",
                    fan_max=6,
                ),
            ),
        ),
        PhaseSpec(
            id="verify",
            title="Verify",
            detail="try to refute each claim before it survives",
            depends_on=("search",),
            agents=(
                AgentSpec(
                    id="verify",
                    purpose="Attempt to refute one claim",
                    prompt_id="research.verify",
                    toolset="research",
                    effort="medium",
                    max_tokens=700,
                    fan_over="findings",
                    fan_max=8,
                ),
            ),
        ),
        PhaseSpec(
            id="synthesize",
            title="Synthesize",
            detail="write the cited answer",
            depends_on=("verify",),
            agents=(
                AgentSpec(
                    id="synthesize",
                    purpose="Write the final cited answer from verified findings",
                    prompt_id="research.synthesize",
                    toolset="none",
                    effort="high",
                    max_tokens=2600,
                ),
            ),
        ),
    ),
)

DOCUMENT_QA = WorkflowTemplate(
    id="document_qa",
    name="Document Q&A",
    description=(
        "Answer a question over pre-materialized document pages (Space/PdfPig + OCR). "
        "Plan relevant pages, fan out extract agents, then a final deduce/synthesize."
    ),
    triggers=(
        "document", "pdf", "this report", "this file", "pages", "whole report",
        "according to the document", "in the pdf", "workbook", "across pages",
    ),
    default_toolset="memory",
    inputs=("question", "pages"),
    phases=(
        PhaseSpec(
            id="plan",
            title="Plan pages",
            detail="pick page batches that answer the question",
            agents=(
                AgentSpec(
                    id="plan-pages",
                    purpose="Select which pages to extract",
                    prompt_id="document.plan",
                    toolset="none",
                    effort="medium",
                    max_tokens=700,
                    vars={"fanout": 4},
                ),
            ),
        ),
        PhaseSpec(
            id="extract",
            title="Extract",
            detail="one extract subagent per page batch (CPU-capped fan_max)",
            depends_on=("plan",),
            agents=(
                AgentSpec(
                    id="extract",
                    purpose="Distill one page/batch with citations",
                    prompt_id="document.extract",
                    toolset="none",
                    effort="medium",
                    max_tokens=1200,
                    fan_over="pages",
                    fan_max=4,
                ),
            ),
        ),
        PhaseSpec(
            id="synthesize",
            title="Deduce",
            detail="final markdown answer with page cites",
            depends_on=("extract",),
            agents=(
                AgentSpec(
                    id="synthesize",
                    purpose="Final deduce from page extracts",
                    prompt_id="document.synthesize",
                    toolset="none",
                    effort="high",
                    max_tokens=1800,
                ),
            ),
        ),
    ),
)

SMOOTHNESS_AUDIT = WorkflowTemplate(
    id="smoothness_audit",
    name="Smoothness audit",
    description=(
        "Audit a UI for latency, jank and animation smoothness across four "
        "independent lenses, verify each finding against the real code, then "
        "report ordered by severity over cost."
    ),
    triggers=(
        "smoothness", "smoother", "jank", "janky", "laggy", "latency",
        "animation", "animations", "morphing", "stutter", "frame rate", "fps",
        "performance audit", "feels slow", "improve smoothness", "responsiveness",
    ),
    default_toolset="code",
    phases=(
        PhaseSpec(
            id="audit",
            title="Audit",
            detail="four independent lenses over the target",
            agents=(
                AgentSpec(
                    id="animation",
                    purpose="Find animation and transition work that cannot hit the compositor",
                    prompt_id="audit.animation",
                    toolset="code",
                    effort="high",
                    max_tokens=1800,
                ),
                AgentSpec(
                    id="latency",
                    purpose="Trace input-to-paint latency and find what serializes it",
                    prompt_id="audit.latency",
                    toolset="code",
                    effort="high",
                    max_tokens=1800,
                ),
                AgentSpec(
                    id="render",
                    purpose="Find render and DOM cost that repeats per frame",
                    prompt_id="audit.render",
                    toolset="code",
                    effort="high",
                    max_tokens=1800,
                ),
                AgentSpec(
                    id="load-path",
                    purpose="Trace startup work done before the first usable frame",
                    prompt_id="audit.load_path",
                    toolset="code",
                    effort="medium",
                    max_tokens=1500,
                ),
            ),
        ),
        PhaseSpec(
            id="verify",
            title="Verify",
            detail="read the cited code and confirm or kill each finding",
            depends_on=("audit",),
            agents=(
                AgentSpec(
                    id="verify",
                    purpose="Adversarially verify one finding against the real code",
                    prompt_id="audit.verify",
                    toolset="code",
                    effort="medium",
                    max_tokens=700,
                    fan_over="findings",
                    fan_max=8,
                ),
            ),
        ),
        PhaseSpec(
            id="report",
            title="Report",
            detail="order by severity over cost and say what was not covered",
            depends_on=("verify",),
            agents=(
                AgentSpec(
                    id="report",
                    purpose="Write the actionable report from confirmed findings",
                    prompt_id="audit.report",
                    toolset="none",
                    effort="high",
                    max_tokens=2400,
                ),
            ),
        ),
    ),
    inputs=("target",),
)

_REVIEW_DIMENSIONS = (
    ("correctness", "Logic that produces a wrong result: off-by-one, inverted conditions, wrong operator, mishandled null/empty, broken invariants."),
    ("error-handling", "Failures that are swallowed, retried wrongly, or leave partial state behind. Resources not released on the error path."),
    ("concurrency", "Shared state touched without ordering guarantees, races between check and use, reentrancy, deadlock, lost updates."),
    ("security", "Untrusted input reaching a sink: injection, path traversal, secrets in logs or URLs, missing authz on a state change."),
)

CODE_REVIEW = WorkflowTemplate(
    id="code_review",
    name="Code review",
    description=(
        "Review a diff along four independent dimensions in parallel, then "
        "adversarially verify each finding before reporting."
    ),
    triggers=(
        "review", "code review", "check my code", "look over this diff",
        "pr review", "critique", "any bugs in", "audit the code",
    ),
    default_toolset="code",
    phases=(
        PhaseSpec(
            id="review",
            title="Review",
            detail="one reviewer per dimension",
            agents=tuple(
                AgentSpec(
                    id=f"review-{dim}",
                    purpose=f"Review for {dim} defects only",
                    prompt_id="review.dimension",
                    toolset="code",
                    effort="high",
                    max_tokens=1800,
                    vars={"dimension": dim, "dimension_detail": detail},
                )
                for dim, detail in _REVIEW_DIMENSIONS
            ),
        ),
        PhaseSpec(
            id="verify",
            title="Verify",
            detail="refute anything that does not survive a second read",
            depends_on=("review",),
            agents=(
                AgentSpec(
                    id="verify",
                    purpose="Adversarially verify one review finding",
                    prompt_id="audit.verify",
                    toolset="code",
                    effort="medium",
                    max_tokens=700,
                    fan_over="findings",
                    fan_max=10,
                ),
            ),
        ),
        PhaseSpec(
            id="report",
            title="Report",
            detail="rank the confirmed findings",
            depends_on=("verify",),
            agents=(
                AgentSpec(
                    id="report",
                    purpose="Rank and write up confirmed findings",
                    prompt_id="audit.report",
                    toolset="none",
                    effort="high",
                    max_tokens=2000,
                ),
            ),
        ),
    ),
    inputs=("target",),
)

BUG_HUNT = WorkflowTemplate(
    id="bug_hunt",
    name="Bug hunt",
    description="Several finders sweep for defects from different starting points, then each candidate is verified.",
    triggers=("find bugs", "bug hunt", "what's broken", "hunt for bugs", "defects", "edge cases"),
    default_toolset="code",
    phases=(
        PhaseSpec(
            id="find",
            title="Find",
            detail="parallel finders with different entry points",
            agents=tuple(
                AgentSpec(
                    id=f"find-{n}",
                    purpose=f"Sweep {n} for defects",
                    prompt_id="bug.find",
                    toolset="code",
                    effort="high",
                    max_tokens=1600,
                    vars={"round": 1, "seen": "(nothing yet)", "entry": n},
                )
                for n in ("error-paths", "boundaries", "state-transitions")
            ),
        ),
        PhaseSpec(
            id="verify",
            title="Verify",
            detail="confirm each candidate against the code",
            depends_on=("find",),
            agents=(
                AgentSpec(
                    id="verify",
                    purpose="Verify one candidate bug",
                    prompt_id="audit.verify",
                    toolset="code",
                    effort="medium",
                    max_tokens=700,
                    fan_over="bugs",
                    fan_max=10,
                ),
            ),
        ),
    ),
    inputs=("target",),
)

TEMPLATES: tuple[WorkflowTemplate, ...] = (
    DEEP_RESEARCH,
    DOCUMENT_QA,
    SMOOTHNESS_AUDIT,
    CODE_REVIEW,
    BUG_HUNT,
)

_BY_ID: dict[str, WorkflowTemplate] = {t.id: t for t in TEMPLATES}


def get(template_id: str) -> WorkflowTemplate | None:
    return _BY_ID.get((template_id or "").strip().lower())


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "inputs": list(t.inputs),
            "agent_count": t.agent_count,
            "phases": [
                {
                    "id": p.id,
                    "title": p.title,
                    "detail": p.detail,
                    "depends_on": list(p.depends_on),
                    "agents": [
                        {
                            "id": a.id,
                            "purpose": a.purpose,
                            "prompt_id": a.prompt_id,
                            "tools": list(a.tools),
                            "effort": a.effort,
                            "fans_out": a.fan_over is not None,
                        }
                        for a in p.agents
                    ],
                }
                for p in t.phases
            ],
        }
        for t in TEMPLATES
    ]


# -- compilation -------------------------------------------------------------


def _expand(
    phase: PhaseSpec,
    template: WorkflowTemplate,
    variables: Mapping[str, Any],
    fan_items: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[tuple[str, AgentSpec, dict[str, Any]]]:
    """(node_id, spec, prompt vars) for every concrete agent in this phase.

    A ``fan_over`` spec with no items yet still yields one node: at compile time
    the upstream phase has not run, so the fan width is unknown. The runner
    re-expands that node once the real list arrives.
    """
    out: list[tuple[str, AgentSpec, dict[str, Any]]] = []
    for spec in phase.agents:
        base = {**variables, **dict(spec.vars)}
        if spec.fan_over:
            items = list(fan_items.get(spec.fan_over) or [])[: spec.fan_max]
            if not items:
                out.append((f"{phase.id}__{spec.id}", spec, base))
                continue
            for idx, item in enumerate(items):
                merged = {**base, **{k: v for k, v in dict(item).items()}}
                out.append((f"{phase.id}__{spec.id}__{idx}", spec, merged))
        else:
            out.append((f"{phase.id}__{spec.id}", spec, base))
    return out


def _agent_node(
    node_id: str,
    spec: AgentSpec,
    prompt_vars: Mapping[str, Any],
    upstream: Sequence[str],
    template: WorkflowTemplate,
    phase: PhaseSpec,
) -> DSLNode:
    return DSLNode(
        id=node_id,
        kind=NodeType.AGENT_TASK,
        inputs=list(upstream),
        prompt=prompt_library.render(spec.prompt_id, prompt_vars),
        max_tokens=spec.max_tokens,
        annotations={
            "workflow": template.id,
            "phase": phase.id,
            "phase_title": phase.title,
            "agent": spec.id,
            "label": f"{phase.id}:{spec.id}",
            "purpose": spec.purpose,
            "prompt_id": spec.prompt_id,
            "tools": list(spec.tools),
            "effort": spec.effort,
            "vars": dict(prompt_vars),
        },
    )


def build_phase_agents(
    template: WorkflowTemplate,
    phase: PhaseSpec,
    variables: Mapping[str, Any],
    upstream_ids: Sequence[str],
    *,
    fan_items: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[DSLNode]:
    """Concrete AGENT_TASK nodes for one phase, fanning out over ``fan_items``.

    The runner calls this once the upstream phase has produced its lists, so a
    ``fan_over`` spec expands to the real number of items rather than the single
    placeholder ``compile_template`` emits when the width is still unknown.
    """
    upstream = list(upstream_ids) or ["prompt"]
    nodes: list[DSLNode] = []
    for node_id, spec, prompt_vars in _expand(phase, template, variables, dict(fan_items or {})):
        nodes.append(_agent_node(node_id, spec, prompt_vars, upstream, template, phase))
    return nodes


def compile_template(
    template: WorkflowTemplate,
    variables: Mapping[str, Any] | None = None,
    *,
    fan_items: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    intent_hash: str = "workflow",
) -> AgenticDSLProgram:
    """Template + variables → a DAG the existing ``run_dag`` can execute."""
    variables = dict(variables or {})
    fan_items = dict(fan_items or {})

    seed = DSLNode(id="prompt", kind=NodeType.DOCUMENT_REF, context_key="prompt")
    nodes: list[DSLNode] = [seed]
    phase_outputs: dict[str, list[str]] = {}

    for phase in template.phases:
        upstream: list[str] = []
        for dep in phase.depends_on:
            upstream.extend(phase_outputs.get(dep, []))
        if not upstream:
            upstream = ["prompt"]

        phase_nodes = build_phase_agents(template, phase, variables, upstream, fan_items=fan_items)
        nodes.extend(phase_nodes)
        agent_ids = [n.id for n in phase_nodes]

        join_id = f"{phase.id}__join"
        nodes.append(
            DSLNode(
                id=join_id,
                kind=NodeType.EMIT,
                inputs=agent_ids,
                annotations={"workflow": template.id, "phase": phase.id, "join": True},
            )
        )
        phase_outputs[phase.id] = [join_id]

    return AgenticDSLProgram(
        nodes=nodes,
        entry_node_id="prompt",
        output_node_id=f"{template.phases[-1].id}__join",
        intent_hash=intent_hash,
    )
