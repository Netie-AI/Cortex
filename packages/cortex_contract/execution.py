from __future__ import annotations

from typing import Any, Mapping, Protocol

from pydantic import BaseModel, Field


class PoolSpec(BaseModel):
    id: str
    class_name: str = "default"
    max_concurrency: int = 1


class Manifest(BaseModel):
    session_id: str
    org_id: str
    space_id: str | None = None
    allowed_paths: list[str] = Field(default_factory=list)
    row_predicate_sql: str | None = None
    expires_at: str
    signature: str


class SubmitRequest(BaseModel):
    pool: PoolSpec
    plan: dict[str, Any]
    body: dict[str, Any]
    manifest: Manifest


class QueryResult(BaseModel):
    ok: bool
    status: str
    run_id: str | None = None
    output: Any = None
    error: str | None = None


class EngineSubmitter(Protocol):
    async def submit(
        self,
        plan: Mapping[str, Any],
        body: Mapping[str, Any],
        *,
        caller: Any = None,
    ) -> dict[str, Any]: ...
