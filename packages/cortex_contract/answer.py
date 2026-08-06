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


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    session_id: str = "demo"
    space_id: str | None = None


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
    #: INS-02 presentation — optional; DMS maps to envelope.chart.
    chart_spec: dict[str, Any] | None = None


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
