from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer


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


class FollowUp(BaseModel):
    """1.5.0 SUGGEST (#308): one follow-up question grounded in an answered result.

    ``tables`` are granted tables in the schema. ``columns`` are ``table.column``
    schema columns of those tables, or bare column names the result returned.
    ``values`` are cells the result returned. Nothing else may be named.
    """

    question: str
    kind: str
    tables: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    served_by: str
    served_at: str
    served_reason: str
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False


# 1.5.0 fields that stay off the wire unless the engine set them, so an answer
# with follow-ups switched off serialises to the same bytes as 1.4.0.
_SET_ONLY_FIELDS = ("followups", "followups_reason")


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
    # 1.5.0 SUGGEST (#308): follow-ups grounded in this result; never on an
    # abstain or a clarify. Absent when follow-ups are off. ``followups_reason``
    # says why the list is empty when they are on.
    followups: list[FollowUp] = Field(default_factory=list)
    followups_reason: str | None = None

    # No return annotation: pydantic would publish it as the serialization schema.
    @model_serializer(mode="wrap")
    def _omit_unset_followups(self, handler: SerializerFunctionWrapHandler):
        out = handler(self)
        if isinstance(out, dict):
            for name in _SET_ONLY_FIELDS:
                if name not in self.model_fields_set:
                    out.pop(name, None)
        return out


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
