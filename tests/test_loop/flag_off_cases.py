"""C-LOOP (#291) loop-off byte-identity cases, shared by the recorder and the test.

The golden is recorded on parent ``c7469da4`` (``record_flag_off_golden.py``).
Only per-call randomness is normalised: uuids, drillthrough tokens, memory
entry ids and timestamps. Every other byte must match.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from CortexOS.memory import space_memory as sm
from CortexOS.memory.space_memory import Actor

GOLDEN = Path(__file__).parent / "fixtures" / "flag_off_golden_c7469da4.json"
SESSION = "c-loop-291"
STEWARD = Actor("steward-alice", "steward")
# (name, question, memory_on, confirm_first)
CASES: tuple[tuple[str, str, bool, bool], ...] = (
    ("governed_metric", "what is our total revenue", False, False),
    ("abstain", "what is the weather in penang", False, False),
    ("blocked", "drop table inventory", False, False),
    ("memory_reuse", "what is our total revenue", True, True),
)

_UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TOKEN = re.compile(rb'"drillthrough_token":"[^"]*"')
_MEM = re.compile(rb"mem_[a-z]+_[0-9a-f]{16}")
_TS = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?")


def normalise(raw: bytes) -> str:
    out = _TOKEN.sub(b'"drillthrough_token":"<token>"', raw)
    out = _UUID.sub(b"<uuid>", out)
    out = _MEM.sub(b"<mem>", out)
    out = _TS.sub(b"<ts>", out)
    return out.decode("utf-8")


def run_cases(client: Any, monkeypatch: Any) -> dict[str, str]:
    """POST each case to /v1/contract/ask; return normalised raw response bytes."""
    out: dict[str, str] = {}
    for i, (name, question, memory_on, confirm_first) in enumerate(CASES):
        space = f"space-{i}"
        if memory_on:
            monkeypatch.setenv(sm.ENABLED_ENV, "1")
        else:
            monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
        client.bind_session(SESSION, space)
        payload = {"question": question, "session_id": SESSION, "space_id": space}
        if confirm_first:
            first = client.post("/v1/contract/ask", json=payload).json()
            sm.get_space_memory().confirm_solution(
                space_id=space,
                question=question,
                sql=first["sql_used"],
                steward=STEWARD,
                source=f"ask:{first['answer_id']}",
            )
        resp = client.post("/v1/contract/ask", json=payload)
        out[name] = f"{resp.status_code} " + normalise(resp.content)
    monkeypatch.delenv(sm.ENABLED_ENV, raising=False)
    return out


def load_golden() -> dict[str, str]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))
