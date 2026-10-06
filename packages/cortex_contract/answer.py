from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_serializer, model_validator


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
    # 1.5.0: ids of clarify options the asker chose, in the order they were
    # offered, re-sent with the original question. Ignored when clarify is off.
    clarify_option_ids: list[str] = Field(default_factory=list)


class AmbiguityType(str, Enum):
    """1.5.0: why an ask was answered with a clarify question."""

    METRIC = "metric"
    TIME_WINDOW = "time_window"
    TERM_COLUMN = "term_column"


class ClarifyOption(BaseModel):
    """1.5.0: one concrete interpretation the asker can pick."""

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    resolved_question: str = Field(min_length=1)
    interpretation: dict[str, str] = Field(default_factory=dict)


def _require_options_and_type(clarify: Clarify) -> None:
    if not isinstance(clarify.ambiguity_type, AmbiguityType):
        raise ValueError("a clarify question must name its ambiguity_type")
    if not 2 <= len(clarify.options) <= 5:
        raise ValueError("a clarify question carries 2-5 options")
    if len({option.id for option in clarify.options}) != len(clarify.options):
        raise ValueError("clarify option ids must be unique")


class Clarify(BaseModel):
    """1.5.0: the ask is ambiguous; one question with 2-5 options, never a guess.

    Distinct from abstain: an abstain says no trustworthy answer exists, a
    clarify says several do and names which choice would pick one.
    """

    question: str = Field(min_length=1)
    ambiguity_type: AmbiguityType
    term: str = Field(min_length=1)
    options: list[ClarifyOption] = Field(json_schema_extra={"minItems": 2, "maxItems": 5})
    served_by: str
    served_at: str
    served_reason: str
    served_catalog: str

    @model_validator(mode="after")
    def _options_and_type(self) -> Clarify:
        _require_options_and_type(self)
        return self


def _require_no_numbers_on_clarify(answer: Answer) -> None:
    if answer.clarify is None:
        return
    if answer.sql_used or answer.rows or answer.row_count or answer.drillthrough_token:
        raise ValueError("a clarify answer carries no SQL, rows or drillthrough")
    if answer.provenance.badge is not Badge.ABSTAIN:
        raise ValueError("a clarify answer never carries a confident badge")


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
    # 1.5.0 CLARIFY: set instead of a guessed number when the ask is ambiguous.
    # ``clarify_resolved`` lists the chosen option ids this answer applied. Both
    # are left off the wire when unset, so a 1.4 envelope is unchanged.
    clarify: Clarify | None = None
    clarify_resolved: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _clarify_is_not_a_number(self) -> Answer:
        _require_no_numbers_on_clarify(self)
        return self

    @model_serializer(mode="wrap")
    # Unannotated on purpose: a return annotation replaces the serialization
    # schema, and FastAPI would then publish Answer-Input / Answer-Output.
    def _omit_unset_clarify(self, handler):
        data = handler(self)
        if isinstance(data, dict):
            if data.get("clarify") is None:
                data.pop("clarify", None)
            if not data.get("clarify_resolved"):
                data.pop("clarify_resolved", None)
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
