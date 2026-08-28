"""Discovery data models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["skill", "mcp", "subagent", "toolkit", "local_skill"]


class RefItem(BaseModel):
    id: str
    kind: Kind
    name: str
    description: str = ""
    url: str = ""
    source: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    install_hint: str = ""
    reputation: str = "community"
    installs_hint: int | None = None


class DiscoveryHit(BaseModel):
    id: str
    kind: Kind
    name: str
    description: str
    score: float
    url: str = ""
    source: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    install_hint: str = ""
    reputation: str = "community"
    why: str = ""
    evolved: bool = False
    skillopt_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()


class DiscoveryResult(BaseModel):
    ok: bool = True
    goal: str
    question: str = ""
    policy: str = "skills_first_then_mcp; evolve_with_skillopt"
    matches: list[DiscoveryHit] = Field(default_factory=list)
    best: DiscoveryHit | None = None
    sources_consulted: list[str] = Field(default_factory=list)
    hint: str = (
        "Install find-skills meta-skill once, then ask: "
        "'Are there any good skills for [GOAL]?'"
    )
    skillopt: dict[str, Any] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()
