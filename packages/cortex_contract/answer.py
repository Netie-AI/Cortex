from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


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
    # 1.5.0 (#329): ignored unless the engine runs with its plan+SQL path on.
    dms_payload: AskPayload | None = None


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


class InsightsSchemaContext(BaseModel):
    """1.5.0 request field on POST /v1/insights.

    The engine reads this on ``InsightsWireIn.schema_context``, not on
    ``InsightsAskIn``. One optional string. Present: it replaces the pack
    table list. Absent: the pack list stays, and the response stays
    byte-equal to the 279cbd85 pin apart from ``usage``.

    Shortlist pick reasons are not a sibling field. The caller writes them
    into this string: a table line carries ``reason=score:<score>``, a
    column line may carry ``reason=<why>``, and a join line carries
    ``reason=<why>``.
    """

    schema_context: str | None = Field(
        default=None,
        description=(
            "Caller shortlist. Replaces the pack table list when present. "
            "Pick reasons live in this string, not in a separate field: "
            "table lines use reason=score:<score>, column lines may include "
            "reason=<why>, join lines include reason=<why>."
        ),
    )


# Class names keep the Contract prefix so the exported $ref matches the
# published component. A short name would point at a schema that is not emitted.
class ContractInsightsTraceStep(BaseModel):
    """One governed step on POST /v1/insights. Additive on contract 1.5.0.

    kind is shortlist, think, generate, retry, execute, check, or refuse.
    refusal is set when this step refused. shortlist is step 1.
    On execute, sql is the executed string unchanged, and rows is the
    capped, masked sample. row_cap and truncated are stamped there.
    """

    n: int | None = None
    kind: str
    status: str
    refusal: str | None = None
    stamps: dict[str, Any] = Field(default_factory=dict)
    shortlist: list[dict[str, Any]] | None = None
    sql: str | None = None
    rows: list[dict[str, Any]] | None = None
    row_cap: int | None = None
    truncated: bool | None = None


class ContractInsightsTrace(BaseModel):
    """Optional ``steps`` on the insights.ask 200 response.

    Omitted when the caller did not ask, or when ``schema_context`` is absent.
    """

    steps: list[ContractInsightsTraceStep] | None = None


class ContractInsightsTraceRequest(BaseModel):
    """Optional ask for the step trace. Not a field of ``InsightsAskIn``."""

    step_trace: bool | None = Field(
        default=None,
        description=(
            "When true and schema_context is present, the response includes "
            "steps. When omitted, false, or schema_context is absent, steps "
            "is omitted and the rest of the envelope stays byte-equal."
        ),
    )


class InsightsUsage(BaseModel):
    """1.5.0 response field ``usage`` on POST /v1/insights.

    Sum of the counts each FreeRoute complete() call reported. A count is
    null when it was omitted. A missing count is never stored as 0. The
    live crew return carries prompt_tokens and completion_tokens. total_tokens
    is null when that return omits the key.
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
