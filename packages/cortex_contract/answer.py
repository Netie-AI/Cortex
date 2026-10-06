from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)


class Badge(str, Enum):
    CERTIFIED = "certified"
    GOVERNED_METRIC = "governed_metric"
    QUERY_SKILL = "query_skill"
    SESSION = "session"
    ABSTAIN = "abstain"
    BLOCKED = "blocked"


class AbstainReason(str, Enum):
    NO_TRUSTWORTHY_PATH = "no_trustworthy_path"
    POLICY_BLOCK = "policy_block"
    NEEDS_CLARIFICATION = "needs_clarification"


class Provenance(BaseModel):
    layer: str
    badge: Badge
    metric_id: str | None = None
    query_source: str | None = None
    assumptions: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = "demo"
    space_id: str | None = None
    # 1.4.0: set on every ask of a scored round. Solution memory stays empty and
    # nothing is written to memory while it is set.
    scored_pack_id: str | None = None


class MemoryRead(BaseModel):
    """1.4.0: provenance of one per-Space memory entry an answer read."""

    id: str
    kind: str
    space_id: str
    source: str
    version: int
    written_at: str
    served_at: str


_NAMED_ABSTAIN = re.compile(r"^[a-z][a-z0-9_]*: \S")


class LoopStep(BaseModel):
    """1.5.0 C-LOOP: one stamped step of the analysis loop, in run order."""

    seq: int
    served_step: str
    served_status: Literal["ok", "skipped", "failed", "refused", "abstain"]
    served_by: str
    served_at: str
    served_reason: str = ""
    served_tool: str | None = None
    served_memory_ids: list[str] = Field(default_factory=list)
    served_sql: list[str] = Field(default_factory=list)
    served_row_count: int | None = None


class AnalysisLoop(BaseModel):
    """1.5.0 C-LOOP: the stamped steps of one analysis-loop ask, in run order.

    The last step is ``answer`` (ok) or ``abstain``; an abstain step's
    ``served_reason`` is ``"<named_code>: <detail>"``.
    """

    outcome: Literal["answer", "abstain"]
    steps: list[LoopStep] = Field(default_factory=list)

    @model_validator(mode="after")
    def _abstain_is_named(self) -> AnalysisLoop:
        last = self.steps[-1] if self.steps else None
        if self.outcome == "abstain":
            if last is None or (last.served_step, last.served_status) != ("abstain", "abstain"):
                raise ValueError("an analysis-loop abstain must end with an abstain step")
            if not _NAMED_ABSTAIN.match(last.served_reason):
                raise ValueError("an analysis-loop abstain must name its reason as '<code>: <detail>'")
        elif last is None or (last.served_step, last.served_status) != ("answer", "ok"):
            raise ValueError("an analysis-loop answer must end with an ok answer step")
        if any(s.served_status == "abstain" for s in self.steps[:-1]):
            raise ValueError("only the last analysis-loop step may abstain")
        return self


class ContributingSource(BaseModel):
    """One source card for the Sources panel (architecture §4.7 / §4.8)."""

    ref_id: str
    container: str | None = None
    member: str | None = None
    kind: str | None = None
    row_count: int | None = None
    contribution: float | None = None


class Answer(BaseModel):
    answer: str
    sql_used: str | None = None
    audit_id: str
    route: str
    row_count: int | None = None
    rows: list[dict[str, Any]] | None = None
    provenance: Provenance
    suggestions: list[str] = Field(default_factory=list)
    # T7 / contract 1.2.0 additive — older clients ignore these.
    answer_id: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    contributing_sources: list[ContributingSource] = Field(default_factory=list)
    drillthrough_token: str | None = None
    # 1.3.0: actual FreeRoute response identity for model-served L2 answers.
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False
    served_reason: str | None = None
    # 1.4.0 C-MEM: per-Space memory this answer read. [] when memory is off or
    # nothing was read. ``reused`` marks a stored solution re-run on current
    # data; it is never a validation by itself.
    memory_ids_read: list[str] = Field(default_factory=list)
    memory_reads: list[MemoryRead] = Field(default_factory=list)
    reused: bool = False
    # 1.5.0 C-LOOP: present only when the analysis loop ran. Left off the wire
    # when absent, so a loop-off answer is byte-for-byte a 1.4.0 answer.
    analysis_loop: AnalysisLoop | None = None

    # No return annotation: one would replace Answer's published response schema.
    @model_serializer(mode="wrap")
    def _omit_absent_loop(self, handler: SerializerFunctionWrapHandler):
        data = handler(self)
        if self.analysis_loop is None and isinstance(data, dict):
            data.pop("analysis_loop", None)
        return data


class DrillthroughRequest(BaseModel):
    token: str = Field(min_length=1)


class DrillthroughResponse(BaseModel):
    answer_id: str
    session_id: str
    sql_used: str
    approximate: bool = False
    row_count: int
    total_count: int | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
