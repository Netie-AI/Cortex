"""The document reader's reply shape since #465: one ``{"answer", "citations"}`` object.

Test fixtures script the reader with this so the served text stays ``answer``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

_CITE = re.compile(r"\[(\d+)\]")


def reader_reply(answer: str, citations: Iterable[int] | None = None) -> str:
    """The JSON reply a reader sends for ``answer``; citations default to its inline ``[n]``."""
    cites = (
        list(citations)
        if citations is not None
        else sorted({int(c) for c in _CITE.findall(answer)})
    )
    return json.dumps({"answer": answer, "citations": cites})
