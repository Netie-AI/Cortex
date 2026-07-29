from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Diff(BaseModel):
    target: str
    before: Any = None
    after: Any = None
    reason: str | None = None


class GateResult(BaseModel):
    passed: bool
    violations: list[str] = Field(default_factory=list)


class ProposalVersion(BaseModel):
    proposal_id: str
    version: int
    diff: Diff
    gate: GateResult


class Proposal(BaseModel):
    id: str
    space_id: str | None = None
    status: str
    latest: ProposalVersion
