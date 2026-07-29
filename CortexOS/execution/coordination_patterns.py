"""Multi-agent coordination patterns — Anthropic catalog mapped onto Cortex.

distill: skill_distill/captures/2026-07-27_anthropic_multi-agent-coordination.md

This module is a *decision surface*, not a third orchestrator. It recommends a
pattern and points at existing Cortex runners (DAG templates, AGENT_TASK,
A2A stubs, memory). Execution still goes through dag_runner / workflow_runner.

Sources:
- https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
- Anthropic "Five approaches" coordination-patterns post (2026-04-10)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

PatternId = Literal[
    "single_agent",
    "generator_verifier",
    "orchestrator_subagent",
    "agent_teams",
    "message_bus",
    "shared_state",
]

CortexStatus = Literal["strong", "partial", "none", "n/a"]


@dataclass(frozen=True, slots=True)
class CoordinationPattern:
    id: PatternId
    name: str
    blurb: str
    use_when: tuple[str, ...]
    struggles_when: tuple[str, ...]
    cortex_status: CortexStatus
    cortex_path: str
    evolve_from: tuple[PatternId, ...] = ()
    evolve_to: tuple[PatternId, ...] = ()
    default_for: bool = False
    parked: bool = False
    parking_ref: str = ""


PATTERNS: tuple[CoordinationPattern, ...] = (
    CoordinationPattern(
        id="single_agent",
        name="Single agent",
        blurb=(
            "One context, one tool loop. Prefer this unless context pollution, "
            "true parallelism, or specialization clearly justify multi-agent cost "
            "(typically 3–10× tokens)."
        ),
        use_when=(
            "Task fits one coherent context",
            "Tool count is manageable (<~15–20) or deferred/lazy-loaded",
            "No independent parallel facets",
        ),
        struggles_when=(
            "Context pollution across unrelated subtasks",
            "Search space too large for one window",
            "Conflicting personas / tool domains in one prompt",
        ),
        cortex_status="strong",
        cortex_path="architecture_presets=minimal|AGENT_TASK max_steps loop",
        evolve_to=("generator_verifier", "orchestrator_subagent"),
        default_for=True,
    ),
    CoordinationPattern(
        id="generator_verifier",
        name="Generator–verifier",
        blurb=(
            "Generate → verify against explicit criteria → revise until accept "
            "or max iterations. Verifier must not rubber-stamp (early victory)."
        ),
        use_when=(
            "Output quality is critical",
            "Evaluation criteria can be made explicit / black-box testable",
            "Incorrect output costs more than another generation cycle",
        ),
        struggles_when=(
            "Vague 'is it good?' criteria (rubber-stamp)",
            "Evaluation as hard as generation (creative inseparability)",
            "Oscillation without max_attempts + fallback",
        ),
        cortex_status="partial",
        cortex_path=(
            "workflow verify phases + execution/generator_verifier.py revise loop; "
            "prompts research.verify / audit.verify"
        ),
        evolve_from=("single_agent",),
        evolve_to=("orchestrator_subagent",),
    ),
    CoordinationPattern(
        id="orchestrator_subagent",
        name="Orchestrator–subagent",
        blurb=(
            "Lead plans, dispatches bounded subagents (fresh context, distilled "
            "return), synthesizes. Default multi-agent starting point."
        ),
        use_when=(
            "Clear task decomposition with minimal interdependence",
            "Subtasks are short, focused, produce clear outputs",
            "Need context isolation or parallel facet coverage",
        ),
        struggles_when=(
            "Orchestrator becomes information bottleneck across subagents",
            "Sequential dispatch without parallel phases",
            "Workers need sustained multi-step domain memory across many tasks",
        ),
        cortex_status="strong",
        cortex_path=(
            "workflow_templates → compile_template → dag_runner + AGENT_TASK; "
            "subagent_contract sanitize + spawn depth"
        ),
        evolve_from=("single_agent", "generator_verifier"),
        evolve_to=("agent_teams", "message_bus", "shared_state"),
        default_for=True,
    ),
    CoordinationPattern(
        id="agent_teams",
        name="Agent teams",
        blurb=(
            "Persistent workers claim from a shared queue; accumulate domain "
            "context across many long-running independent assignments."
        ),
        use_when=(
            "Parallel independent long-running subtasks",
            "Workers benefit from sustained multi-step familiarity",
            "Partitions do not need intermediate cross-talk",
        ),
        struggles_when=(
            "Hidden interdependence / conflicting edits on shared resources",
            "Hard completion detection across variable durations",
            "Need real-time sharing of intermediate findings",
        ),
        cortex_status="none",
        cortex_path="not built — park under P19 (upstream experimental)",
        evolve_from=("orchestrator_subagent",),
        evolve_to=("shared_state",),
        parked=True,
        parking_ref="PARKING_LOT.md#P19",
    ),
    CoordinationPattern(
        id="message_bus",
        name="Message bus",
        blurb=(
            "Publish/subscribe event routing. Workflow emerges from events; "
            "new agent types join without rewiring."
        ),
        use_when=(
            "Event-driven pipelines (alerts → triage → investigate → respond)",
            "Growing agent ecosystem / unpredictable branching",
            "Conditional orchestrator logic is becoming unmaintainable",
        ),
        struggles_when=(
            "Hard to trace cascades without correlation ids",
            "Silent drops / misroutes",
            "Findings shared as events rather than accumulating knowledge",
        ),
        cortex_status="partial",
        cortex_path="a2a/protocol + InProcessA2ATransport (demo handlers only)",
        evolve_from=("orchestrator_subagent",),
        evolve_to=("shared_state",),
        parked=True,
        parking_ref="PARKING_LOT.md#P19",
    ),
    CoordinationPattern(
        id="shared_state",
        name="Shared state",
        blurb=(
            "Agents read/write a shared store directly; no central coordinator. "
            "Requires first-class termination (budget, convergence, adjudicator)."
        ),
        use_when=(
            "Collaborative research where findings must flow in real time",
            "No single point of failure desired",
            "Knowledge accumulates rather than event pipelines",
        ),
        struggles_when=(
            "Duplicate work / contradictory approaches",
            "Reactive token-burning loops without termination",
            "Concurrent write conflicts without versioning/partitioning",
        ),
        cortex_status="partial",
        cortex_path=(
            "memory/* + RAG + step_journal as stores; no multi-writer "
            "termination protocol yet"
        ),
        evolve_from=("agent_teams", "message_bus", "orchestrator_subagent"),
        parked=True,
        parking_ref="PARKING_LOT.md#P19",
    ),
)

_BY_ID: dict[str, CoordinationPattern] = {p.id: p for p in PATTERNS}


def catalog() -> list[dict[str, Any]]:
    return [asdict(p) for p in PATTERNS]


def get(pattern_id: str) -> CoordinationPattern | None:
    return _BY_ID.get((pattern_id or "").strip().lower().replace("-", "_"))


@dataclass(slots=True)
class PatternSignals:
    """Structural signals used to recommend a pattern (context-centric)."""

    quality_critical: bool = False
    explicit_criteria: bool = False
    clear_decomposition: bool = False
    bounded_subtasks: bool = True
    parallel_independent: bool = False
    long_running_workers: bool = False
    event_driven: bool = False
    growing_ecosystem: bool = False
    collaborative_findings: bool = False
    no_spof_required: bool = False
    tool_count: int = 0
    context_pollution_risk: bool = False
    specialization_needed: bool = False
    prefer_cheapest: bool = True


@dataclass(frozen=True, slots=True)
class PatternRecommendation:
    pattern: PatternId
    confidence: float
    why: str
    cortex_path: str
    alternatives: tuple[PatternId, ...] = ()
    warnings: tuple[str, ...] = ()
    multi_agent_justified: bool = False

    def as_dict(self) -> dict[str, Any]:
        p = get(self.pattern)
        return {
            "pattern": self.pattern,
            "name": p.name if p else self.pattern,
            "confidence": round(self.confidence, 3),
            "why": self.why,
            "cortex_path": self.cortex_path,
            "cortex_status": p.cortex_status if p else "none",
            "parked": bool(p.parked) if p else False,
            "alternatives": list(self.alternatives),
            "warnings": list(self.warnings),
            "multi_agent_justified": self.multi_agent_justified,
        }


def _signals_from_mapping(raw: Mapping[str, Any] | None) -> PatternSignals:
    raw = raw or {}
    return PatternSignals(
        quality_critical=bool(raw.get("quality_critical")),
        explicit_criteria=bool(raw.get("explicit_criteria")),
        clear_decomposition=bool(raw.get("clear_decomposition", True)),
        bounded_subtasks=bool(raw.get("bounded_subtasks", True)),
        parallel_independent=bool(raw.get("parallel_independent")),
        long_running_workers=bool(raw.get("long_running_workers")),
        event_driven=bool(raw.get("event_driven")),
        growing_ecosystem=bool(raw.get("growing_ecosystem")),
        collaborative_findings=bool(raw.get("collaborative_findings")),
        no_spof_required=bool(raw.get("no_spof_required")),
        tool_count=int(raw.get("tool_count") or 0),
        context_pollution_risk=bool(raw.get("context_pollution_risk")),
        specialization_needed=bool(raw.get("specialization_needed")),
        prefer_cheapest=bool(raw.get("prefer_cheapest", True)),
    )


def recommend(signals: PatternSignals | Mapping[str, Any] | None = None) -> PatternRecommendation:
    """Recommend the simplest pattern that fits; escalate only on clear signals.

    Anthropic rule: start single-agent / orchestrator-subagent; evolve when
    observed friction matches a specific failure mode.
    """
    s = signals if isinstance(signals, PatternSignals) else _signals_from_mapping(signals)
    warnings: list[str] = []

    multi_justified = bool(
        s.context_pollution_risk
        or s.parallel_independent
        or s.specialization_needed
        or s.quality_critical
        or s.event_driven
        or s.collaborative_findings
        or s.tool_count >= 20
    )
    if s.tool_count >= 15 and s.tool_count < 20:
        warnings.append(
            "Tool count approaching specialization threshold — prefer deferred/lazy "
            "tool loading before splitting agents."
        )
    if multi_justified:
        warnings.append(
            "Multi-agent typically costs 3–10× tokens vs single-agent; confirm a "
            "real constraint (context, parallelism, specialization)."
        )

    # Shared state / SPOF
    if s.collaborative_findings or s.no_spof_required:
        p = get("shared_state")
        assert p is not None
        return PatternRecommendation(
            pattern="shared_state",
            confidence=0.72 if s.collaborative_findings else 0.65,
            why=(
                "Agents need each other's intermediate findings in real time"
                if s.collaborative_findings
                else "No single point of failure required — decentralized store"
            ),
            cortex_path=p.cortex_path,
            alternatives=("orchestrator_subagent", "message_bus"),
            warnings=tuple(warnings + ["parked: termination protocol not shipped"]),
            multi_agent_justified=True,
        )

    # Message bus
    if s.event_driven or s.growing_ecosystem:
        p = get("message_bus")
        assert p is not None
        return PatternRecommendation(
            pattern="message_bus",
            confidence=0.7,
            why="Event-driven / growing agent ecosystem — pub/sub over fixed DAG",
            cortex_path=p.cortex_path,
            alternatives=("orchestrator_subagent",),
            warnings=tuple(warnings + ["parked: A2A is demo-only; harden before prod"]),
            multi_agent_justified=True,
        )

    # Agent teams
    if s.parallel_independent and s.long_running_workers and not s.bounded_subtasks:
        p = get("agent_teams")
        assert p is not None
        return PatternRecommendation(
            pattern="agent_teams",
            confidence=0.68,
            why="Independent long-running partitions need persistent workers",
            cortex_path=p.cortex_path,
            alternatives=("orchestrator_subagent",),
            warnings=tuple(warnings + ["parked: agent teams not built in Cortex yet"]),
            multi_agent_justified=True,
        )

    # Generator-verifier
    if s.quality_critical and s.explicit_criteria:
        p = get("generator_verifier")
        assert p is not None
        return PatternRecommendation(
            pattern="generator_verifier",
            confidence=0.85,
            why="Quality-critical output with explicit evaluation criteria",
            cortex_path=p.cortex_path,
            alternatives=("orchestrator_subagent", "single_agent"),
            warnings=tuple(warnings),
            multi_agent_justified=True,
        )

    # Orchestrator-subagent (widest multi-agent default)
    if multi_justified and (s.clear_decomposition or s.parallel_independent or s.specialization_needed):
        p = get("orchestrator_subagent")
        assert p is not None
        return PatternRecommendation(
            pattern="orchestrator_subagent",
            confidence=0.8,
            why=(
                "Clear bounded decomposition / parallel facets — start with "
                "orchestrator-subagent (Cortex workflow DAG)"
            ),
            cortex_path=p.cortex_path,
            alternatives=("generator_verifier", "single_agent"),
            warnings=tuple(warnings),
            multi_agent_justified=True,
        )

    # Single agent default
    p = get("single_agent")
    assert p is not None
    return PatternRecommendation(
        pattern="single_agent",
        confidence=0.9 if s.prefer_cheapest else 0.75,
        why="No multi-agent constraint detected — stay single-agent",
        cortex_path=p.cortex_path,
        alternatives=("generator_verifier", "orchestrator_subagent"),
        warnings=tuple(warnings),
        multi_agent_justified=False,
    )


def recommend_from_prompt(prompt: str, *, extras: Mapping[str, Any] | None = None) -> PatternRecommendation:
    """Lightweight keyword → signals for chat / engine recommend endpoints."""
    text = (prompt or "").lower()
    signals = PatternSignals(
        quality_critical=any(
            k in text
            for k in (
                "must be correct",
                "compliance",
                "verify",
                "fact-check",
                "fact check",
                "grade",
                "rubric",
                "adversarial",
            )
        ),
        explicit_criteria=any(
            k in text
            for k in (
                "criteria",
                "rubric",
                "test suite",
                "acceptance",
                "checklist",
                "must pass",
            )
        ),
        parallel_independent=any(
            k in text
            for k in (
                "in parallel",
                "fan out",
                "fan-out",
                "multiple angles",
                "several agents",
                "research",
                "investigate",
            )
        ),
        long_running_workers=any(
            k in text for k in ("migrate", "migration", "long-running", "overnight", "batch of services")
        ),
        event_driven=any(
            k in text for k in ("alert", "webhook", "event-driven", "pipeline of events", "triage")
        ),
        growing_ecosystem=any(k in text for k in ("new agent types", "plugin agents", "growing ecosystem")),
        collaborative_findings=any(
            k in text
            for k in (
                "build on each",
                "shared findings",
                "collaborative research",
                "knowledge base together",
            )
        ),
        no_spof_required=any(k in text for k in ("no single point", "no spof", "decentralized")),
        context_pollution_risk=any(
            k in text for k in ("large codebase", "many documents", "huge context", "pollute")
        ),
        specialization_needed=any(
            k in text for k in ("specialist", "specialized agent", "domain expert", "separate tools")
        ),
        clear_decomposition=True,
        bounded_subtasks=not any(k in text for k in ("long-running", "overnight", "persist worker")),
        tool_count=int((extras or {}).get("tool_count") or 0),
        prefer_cheapest=True,
    )
    # Merge explicit overrides from extras
    if extras:
        for key in (
            "quality_critical",
            "explicit_criteria",
            "parallel_independent",
            "long_running_workers",
            "event_driven",
            "growing_ecosystem",
            "collaborative_findings",
            "no_spof_required",
            "context_pollution_risk",
            "specialization_needed",
            "clear_decomposition",
            "bounded_subtasks",
            "prefer_cheapest",
        ):
            if key in extras:
                setattr(signals, key, bool(extras[key]))
    return recommend(signals)


def gap_matrix() -> list[dict[str, Any]]:
    """Compact status board for STATUS / distill reviews."""
    return [
        {
            "pattern": p.id,
            "status": p.cortex_status,
            "path": p.cortex_path,
            "parked": p.parked,
            "parking_ref": p.parking_ref,
        }
        for p in PATTERNS
    ]
