"""SUGGEST (#308) model path: reword templated follow-ups through OpenVault FreeRoute.

This is the only way follow-ups reach a model (import contract 5). The model is
sent the user's question and the templates with every result value masked as
``{v1}``, ``{v2}``...; it never sees a row. Its text is not trusted:
``followups.suggest`` unmasks it and re-runs the grounding refusal on every
reworded question, keeping the template when one fails.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence

from CortexOS.integrations import freeroute
from CortexOS.suggest.followups import Rephrased

TASK = "suggest_followups"
MODEL_ENV = "CORTEX_SUGGEST_MODEL"

_SYSTEM = (
    "You reword follow-up questions for a data analyst. Rewrite each question so it "
    "reads naturally. Keep every {vN} placeholder exactly as written. Keep every table "
    "and column name exactly as written. Do not add tables, columns, filters or values. "
    "Reply with only a JSON array of strings: one per input question, same order."
)


def model_enabled() -> bool:
    return os.environ.get(MODEL_ENV, "").strip() == "1"


def _texts(raw: str, n: int) -> tuple[str | None, ...] | None:
    try:
        data = json.loads(raw.strip())
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or len(data) != n:
        return None
    return tuple(t if isinstance(t, str) else None for t in data)


def freeroute_rephrase(question: str, templates: Sequence[str]) -> Rephrased | None:
    if not templates:
        return None
    n = len(templates)
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": json.dumps({"question": question, "follow_ups": list(templates)}),
        },
    ]
    out = freeroute.complete(
        TASK,
        messages,
        max_tokens=400,
        temperature=0.0,
        accept=lambda text: _texts(text, n) is not None,
    )
    stamp = out.stamp
    if not out.ok or stamp is None:
        return None
    texts = _texts(out.text, n)
    if texts is None:
        return None
    reason = f"FreeRoute call {stamp.call_id}"
    return Rephrased(
        texts=texts,
        served_by=stamp.impl,
        served_reason=f"{reason}: {stamp.served_reason}" if stamp.served_reason else reason,
        served_provider=stamp.served_provider,
        served_model=stamp.served_model,
        served_local=stamp.served_local,
    )
