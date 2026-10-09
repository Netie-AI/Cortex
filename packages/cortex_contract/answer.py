from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Badge(str, Enum):
    CERTIFIED = "certified"
    GOVERNED_METRIC = "governed_metric"
    QUERY_SKILL = "query_skill"
    SESSION = "session"
    ABSTAIN = "abstain"
    BLOCKED = "blocked"
    # 1.5.0: the plan+SQL loop could not ground an answer and is asking the user.
    # Not an abstain. Older clients ignore the value.
    RECONFIRM = "reconfirm"


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


class AskPayloadColumn(BaseModel):
    """1.5.0: one column of a table the consumer selected for this ask."""

    name: str = Field(min_length=1)
    type: str | None = None
    description: str | None = None


class AskPayloadTable(BaseModel):
    """1.5.0: one selected table and its schema."""

    name: str = Field(min_length=1)
    columns: list[AskPayloadColumn] = Field(default_factory=list)
    description: str | None = None


class AskPayloadJoin(BaseModel):
    """1.5.0: one ontology join between two selected tables."""

    left_table: str = Field(min_length=1)
    left_column: str = Field(min_length=1)
    right_table: str = Field(min_length=1)
    right_column: str = Field(min_length=1)
    relation: str | None = None


class PlanSqlReconfirm(BaseModel):
    """1.5.0: why the plan+SQL loop would abstain, and the closest question it can run.

    Present only when the loop could not ground an answer. Yes
    (``AskRequest.plan_sql_confirm`` = ``yes``, with ``question`` set to
    ``closest_question``) runs that question. No answers
    ``not found in the database``. Older clients ignore the object.
    """

    why: str = Field(min_length=1)
    closest_question: str = Field(min_length=1)


class AskPayload(BaseModel):
    """1.5.0: selected tables, their schema and ontology joins for one question.

    The payload can only narrow what the signed grant allows: a table or join
    outside the grant is refused before any model call.
    """

    tables: list[AskPayloadTable] = Field(min_length=1)
    joins: list[AskPayloadJoin] = Field(default_factory=list)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = "demo"
    space_id: str | None = None
    # 1.4.0: set on every ask of a scored round. Solution memory stays empty and
    # nothing is written to memory while it is set.
    scored_pack_id: str | None = None
    # 1.5.0: ignored unless the engine runs with its plan+SQL path switched on.
    dms_payload: AskPayload | None = None
    # 1.5.0: decision on a prior plan+SQL reconfirm. Absent on a normal ask.
    # ``yes`` runs ``question`` (the closest question). ``no`` answers
    # ``not found in the database`` and does not call a model.
    plan_sql_confirm: Literal["yes", "no"] | None = None


class MemoryRead(BaseModel):
    """1.4.0: provenance of one per-Space memory entry an answer read."""

    id: str
    kind: str
    space_id: str
    source: str
    version: int
    written_at: str
    served_at: str


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
    # 1.5.0: set when the plan+SQL loop asks the user to confirm the closest
    # question. Null on every other answer. 1.4 clients ignore it.
    reconfirm: PlanSqlReconfirm | None = None


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
