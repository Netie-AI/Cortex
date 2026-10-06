"""1.5.0 SCHEMA-RETRIEVE (#306): what was retrieved from the schema before planning.

Every name here is inside the signed grant and the caller's Space: candidates,
chosen tables, columns and join paths alike. ``served_*`` naming follows
``Answer.served_*`` (1.3.0) and the C-MEM memory stamps (1.4.0).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RetrievedColumn(BaseModel):
    name: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class RetrievedTable(BaseModel):
    table: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    source: str
    columns: list[RetrievedColumn] = Field(default_factory=list)


class RetrievalCandidate(BaseModel):
    table: str
    score: float


class RetrievedJoinPath(BaseModel):
    tables: list[str]
    on: list[str]
    reason: str


class SchemaRetrieval(BaseModel):
    served_op: str = "schema_retrieve"
    served_space_id: str
    served_method: str
    served_candidates: list[RetrievalCandidate] = Field(default_factory=list)
    served_chosen: list[RetrievedTable] = Field(default_factory=list)
    served_join_paths: list[RetrievedJoinPath] = Field(default_factory=list)
    served_memory_ids_read: list[str] = Field(default_factory=list)
    served_memory_ids_written: list[str] = Field(default_factory=list)
    served_memory_refusals: list[str] = Field(default_factory=list)
    served_bounds: dict[str, int] = Field(default_factory=dict)
    served_at: str
    served_by: str
    served_reason: str = ""
    served_provider: str | None = None
    served_model: str | None = None
