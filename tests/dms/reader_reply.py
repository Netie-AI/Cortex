"""The document reader's reply shape since #465: one ``{"answer", "citations"}`` object.

Test fixtures script the reader with this so the served text stays ``answer``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import pytest

_CITE = re.compile(r"\[(\d+)\]")


def reader_reply(answer: str, citations: Iterable[int] | None = None) -> str:
    """The JSON reply a reader sends for ``answer``; citations default to its inline ``[n]``."""
    cites = (
        list(citations)
        if citations is not None
        else sorted({int(c) for c in _CITE.findall(answer)})
    )
    return json.dumps({"answer": answer, "citations": cites})


#: The #465 check's confirmation.
DOC_CHECK_OK = '{"answers":true}'
DOC_CHECK_NO = '{"answers":false}'


def is_doc_check(body: dict[str, Any] | None) -> bool:
    from CortexOS.rag import documents

    msgs = (body or {}).get("messages") or []
    marker = documents.check_messages("q", [], "a")[0]["content"][:40]
    return bool(msgs) and str(msgs[0].get("content", "")).startswith(marker)


def answer_doc_checks(
    monkeypatch: pytest.MonkeyPatch, fake: Any, verdict: str = DOC_CHECK_OK
) -> list[dict[str, Any]]:
    """Answer every #465 doc-answer check with ``verdict``; every other call is unchanged.

    The check call is recorded on ``fake.calls`` like any chat call, so call
    counts include it. Returns the check request bodies.
    """
    from CortexOS.integrations import openvault_client

    inner = openvault_client.request_json
    seen: list[dict[str, Any]] = []

    def transport(method: str, path: str, *, body: Any = None, headers: Any = None,
                  timeout: float = 5.0, base: Any = None) -> Any:
        if path.split("?", 1)[0] == "/v1/chat/completions" and is_doc_check(body):
            fake.calls.append({"method": method.upper(), "path": path, "body": dict(body or {}),
                               "headers": dict(headers or {}), "base": base})
            seen.append(dict(body or {}))
            choice = {"message": {"role": "assistant", "content": verdict}, "finish_reason": "stop"}
            return 200, {"choices": [choice], "model": fake.served_for(str((body or {}).get("model") or ""))}
        return inner(method, path, body=body, headers=headers, timeout=timeout, base=base)

    monkeypatch.setattr(openvault_client, "request_json", transport)
    return seen


@pytest.fixture(autouse=True)
def confirm_doc_checks(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Imported into a test module, a test that arms the scripted OpenVault gets
    its #465 doc-answer checks confirmed. A test that wants another verdict
    calls ``answer_doc_checks`` itself."""
    if "armed_openvault" in request.fixturenames:
        answer_doc_checks(monkeypatch, request.getfixturevalue("armed_openvault"))
