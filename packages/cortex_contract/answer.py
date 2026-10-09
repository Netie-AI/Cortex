from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_serializer


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


class PackageStamp(BaseModel):
    """1.5.0 C-LOOP-C: what a packaged artifact was built from, by whom and when."""

    served_by: str
    served_method: str
    served_at: str
    served_rows_sha256: str
    served_sql_sha256: str
    served_row_count: int
    served_rows_truncated: bool = False
    # Set when the artifact was not produced or was refused; its body is then empty.
    served_reason: str | None = None


class ChartSpec(PackageStamp):
    """1.5.0: a declarative Vega-Lite-style spec over ``Answer.rows``.

    ``spec.data`` is ``{"name": "rows"}``: the consumer binds the answer's rows.
    The spec never embeds values, and every ``field`` it names is a row column.
    """

    spec: dict[str, Any] | None = None
    fields: list[str] = Field(default_factory=list)


class InsightFact(BaseModel):
    """1.5.0: one number an insight states, and where in the rows it comes from."""

    op: str
    value: float
    column: str | None = None
    row_index: int | None = None


class Insight(PackageStamp):
    """1.5.0: short text stating only facts computed from ``Answer.rows``."""

    text: str | None = None
    facts: list[InsightFact] = Field(default_factory=list)


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
    # 1.5.0 C-LOOP-C: result packaging. Absent from the wire unless packaging
    # ran, so an answer without a package serialises exactly as 1.4.0 did.
    chart_spec: ChartSpec | None = None
    insight: Insight | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_package(self, handler):
        # Unannotated on purpose: pydantic would publish a return annotation as
        # the serialization schema and drop every Answer property from the spec.
        data = handler(self)
        if isinstance(data, dict):
            for key in _PACKAGE_FIELDS:
                if data.get(key) is None:
                    data.pop(key, None)
        return data


_PACKAGE_FIELDS = ("chart_spec", "insight")


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
