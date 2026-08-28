from __future__ import annotations

from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, Field


class ToolClass(str, Enum):
    READ = "read"
    PROPOSE = "propose"
    APPLY = "apply"


class ToolSpec(BaseModel):
    id: str
    class_name: ToolClass
    description: str


class ToolCall(BaseModel):
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    actor: str
    run_id: str | None = None


class ToolResult(BaseModel):
    ok: bool
    tool: str
    verdict: str
    path: str | None = None
    run_id: str | None = None


class ToolRuntime(Protocol):
    def run_tool_call(
        self,
        tool: str,
        params: dict[str, Any] | None = None,
        *,
        actor: str,
        run_id: str | None = None,
        db_path: str | None = None,
    ) -> dict[str, Any]: ...
