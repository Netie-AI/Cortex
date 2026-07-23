"""Context engineering HTTP surface for AirGPT / OpenIDE sidecar clients."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/api/context", tags=["context-engineering"])


class AssembleRequest(BaseModel):
    instructions: str = ""
    tools: str = ""
    examples: str = ""
    memory: str = ""
    retrieval: str = ""
    state: str = ""
    messages: List[str] = Field(default_factory=list)
    token_budget: int = Field(default=4096, ge=256, le=128000)


class CompactRequest(BaseModel):
    messages: List[str] = Field(default_factory=list)
    token_budget: int = Field(default=1200, ge=64, le=32000)
    keep_tail: int = Field(default=4, ge=1, le=40)


class NoteAppendRequest(BaseModel):
    workspace_root: str
    note: str
    heading: str = "Note"


@router.post("/assemble")
def context_assemble(
    req: AssembleRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    from CortexOS.context_engineering import ContextRequest, assemble_context

    _ = caller
    assembled = assemble_context(
        ContextRequest(
            instructions=req.instructions,
            tools=req.tools,
            examples=req.examples,
            memory=req.memory,
            retrieval=req.retrieval,
            state=req.state,
            messages=req.messages,
            token_budget=req.token_budget,
        )
    )
    return {
        "ok": True,
        "system": assembled.system,
        "user_context": assembled.user_context,
        "token_estimate": assembled.token_estimate,
        "truncated_layers": assembled.truncated_layers,
        "compacted": assembled.compacted,
        "layers": assembled.layers,
        "meta": assembled.meta,
    }


@router.post("/compact")
def context_compact(
    req: CompactRequest,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    from CortexOS.context_engineering import compact_messages, estimate_tokens

    _ = caller
    blocks, compacted = compact_messages(
        req.messages,
        token_budget=req.token_budget,
        keep_tail=req.keep_tail,
    )
    joined = "\n\n".join(blocks)
    return {
        "ok": True,
        "messages": blocks,
        "compacted": compacted,
        "token_estimate": estimate_tokens(joined),
    }


@router.post("/notes/append")
def context_notes_append(
    req: NoteAppendRequest,
    caller: Caller = Depends(require_role("steward")),
) -> Dict[str, Any]:
    from CortexOS.context_engineering import NoteStore, default_notes_path

    _ = caller
    path = default_notes_path(req.workspace_root)
    if path is None:
        return {"ok": False, "error": "invalid workspace_root"}
    return NoteStore(path).append(req.note, heading=req.heading)


@router.get("/notes")
def context_notes_read(
    workspace_root: str,
    caller: Caller = Depends(require_role("viewer")),
) -> Dict[str, Any]:
    from CortexOS.context_engineering import NoteStore, default_notes_path, estimate_tokens

    _ = caller
    path = default_notes_path(workspace_root)
    if path is None:
        return {"ok": False, "error": "invalid workspace_root"}
    text = NoteStore(path).read_recent()
    return {
        "ok": True,
        "path": str(path),
        "text": text,
        "token_estimate": estimate_tokens(text),
        "present": bool(text),
    }


def register_context_routes(app: Any) -> None:
    app.include_router(router)
