"""C-LOOP-C (#305) tier:fast proof: flag off, the ask and contract paths answer
byte for byte as main did.

``fixtures/c_loop_c_305/main_flag_off.json`` holds the raw response bytes
captured on main (``captured_on``) for the same requests. This test replays
them on this tree with ``CORTEX_RESULT_PACKAGE`` unset and set to ``0`` and
compares bytes. Only values that differ per request on main itself are masked:
UUIDs, ISO timestamps and the opaque drillthrough token. Drillthrough itself is
not replayed: on the demo warehouse main raises on it (no ``_src_ref_id``).

Recapture on a main worktree after main changes these responses:
``C_LOOP_C_GOLDEN_WRITE=1 pytest tests/dms/test_c_loop_c_flag_off_golden.py``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.dms.test_c_mem_contract_ask import ask_http  # noqa: F401 - pytest fixture

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "fixtures" / "c_loop_c_305" / "main_flag_off.json"
FLAG_ENV = "CORTEX_RESULT_PACKAGE"
WRITE_ENV = "C_LOOP_C_GOLDEN_WRITE"
SESSION = "c-loop-c-golden"
SPACE = "alpha"
QUESTIONS = [
    "top 5 skus by sales",
    "what is our total revenue",
    "revenue by month",
    "Which SKUs are below reorder level?",
]
_UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_ISO_TS = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def _mask(raw: bytes, token: str | None) -> str:
    if token:
        raw = raw.replace(json.dumps(token).encode(), b'"<drillthrough_token>"')
    raw = _UUID.sub(b"<uuid>", raw)
    return _ISO_TS.sub(b"<ts>", raw).decode("utf-8")


def _record(resp, method: str, path: str, body: Any, token: str | None) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "request": body,
        "status": resp.status_code,
        "body": _mask(resp.content, token),
    }


def _replay(client) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for question in QUESTIONS:
        req = {"question": question, "session_id": SESSION, "space_id": SPACE}
        resp = client.post("/v1/contract/ask", json=req)
        token = resp.json().get("drillthrough_token") if resp.status_code == 200 else None
        out.append(_record(resp, "POST", "/v1/contract/ask", req, token))
    for question in QUESTIONS:
        req = {"question": question, "session_id": SESSION, "space_id": SPACE}
        out.append(_record(client.post("/dms/query", json=req), "POST", "/dms/query", req, None))
    out.append(_record(client.get("/v1/contract/tools"), "GET", "/v1/contract/tools", None, None))
    return out


def _head_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("flag", [None, "0"], ids=["flag_unset", "flag_0"])
def test_flag_off_ask_and_contract_paths_are_byte_identical_to_main(
    ask_http, monkeypatch: pytest.MonkeyPatch, flag: str | None  # noqa: F811
) -> None:
    if flag is None:
        monkeypatch.delenv(FLAG_ENV, raising=False)
    else:
        monkeypatch.setenv(FLAG_ENV, flag)
    ask_http.bind_session(SESSION, SPACE)
    got = _replay(ask_http)

    if os.environ.get(WRITE_ENV) and flag is None:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        payload = {"captured_on": _head_sha(), "responses": got}
        GOLDEN.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", "utf-8")

    golden = json.loads(GOLDEN.read_text("utf-8"))
    assert re.fullmatch(r"[0-9a-f]{40}", golden["captured_on"])
    assert len(got) == len(golden["responses"])
    for mine, main in zip(got, golden["responses"], strict=True):
        where = f"{mine['method']} {mine['path']} {mine['request']}"
        assert (mine["method"], mine["path"], mine["request"]) == (
            main["method"], main["path"], main["request"]
        )
        assert mine["status"] == main["status"], where
        assert mine["body"] == main["body"], where
