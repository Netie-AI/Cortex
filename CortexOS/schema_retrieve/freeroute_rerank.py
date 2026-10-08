"""SCHEMA-RETRIEVE (#306) optional reranker. OV FreeRoute is the only model path.

The model sees the question and the granted candidates' table and visible column
names, never rows or values. It replies with a JSON array of table names; any
name outside the candidates is dropped by the retriever (``core._within``).
No provider SDK is imported here or below (import-linter contract 6).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from typing import Any

from CortexOS.integrations import freeroute
from CortexOS.schema_retrieve.core import Rerank, TableDoc

TASK = "schema_retrieve_rerank"
METHOD = "deterministic+freeroute_rerank"
_ARRAY = re.compile(r"\[[^\[\]]*\]", re.S)
_SYSTEM = (
    "You rank database tables by how relevant they are to a question. Reply with "
    "only a JSON array of table names, most relevant first, chosen from the list "
    "given. Never name a table that is not in the list."
)


def parse_order(text: str) -> list[str] | None:
    match = _ARRAY.search(text or "")
    if not match:
        return None
    try:
        raw = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
        return None
    order = [t.strip().lower() for t in raw if t.strip()]
    return order or None


class FreeRouteReranker:
    def __init__(
        self,
        *,
        complete: Callable[..., Any] | None = None,
        max_tokens: int = 200,
        timeout: float = 20.0,
        max_columns: int = 20,
    ) -> None:
        self._complete = complete or freeroute.complete
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_columns = max_columns

    def rerank(self, question: str, candidates: Sequence[TableDoc]) -> Rerank:
        lines = [
            f"- {t.key}({', '.join(t.visible_columns()[: self._max_columns])})" for t in candidates
        ]
        messages = [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Question: {question}\nTables:\n" + "\n".join(lines)},
        ]
        result = self._complete(
            TASK,
            messages,
            max_tokens=self._max_tokens,
            temperature=0.0,
            timeout=self._timeout,
            accept=lambda text: parse_order(text) is not None,
        )
        stamp = getattr(result, "stamp", None)
        provider = getattr(stamp, "served_provider", None)
        model = getattr(stamp, "served_model", None)
        if not getattr(result, "ok", False):
            return Rerank(
                (),
                False,
                METHOD,
                reason=str(getattr(result, "reason", "") or "FreeRoute call failed"),
                served_provider=provider,
                served_model=model,
            )
        order = parse_order(str(getattr(result, "text", "")))
        return Rerank(
            tuple(order or ()),
            order is not None,
            METHOD,
            reason="" if order else "FreeRoute reply was not a JSON array of table names",
            served_provider=provider,
            served_model=model,
        )
