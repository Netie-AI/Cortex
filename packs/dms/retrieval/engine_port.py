"""DMS implementation of the engine's document-retrieval port (C2 inversion).

The engine declares the seam in ``CortexOS/dms/document_retrieval_port.py`` and
never imports this module; ``packs/dms/__init__.py`` pushes the singleton below
in at pack load.

Production path: HTTP GET to DMS ``/v1/library/chunks/search`` (``DMS_URL``).
Tests may register a stub provider on the port instead of hitting the network.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class DmsDocumentRetrieval:
    """Space-scoped chunk retrieval via DMS library search API."""

    def is_configured(self) -> bool:
        return bool(os.environ.get("DMS_URL", "").strip())

    def retrieve(
        self,
        question: str,
        *,
        space_id: str,
        top_k: int = 8,
        source_ids: list[str] | None = None,
        depth: str | None = None,
    ) -> list[dict[str, Any]]:
        del depth  # SiRA depth tier — dense pool sizing deferred (RAG-02 lexical)
        if not space_id:
            return []
        q = (question or "").strip()
        if not q:
            return []
        base = os.environ.get("DMS_URL", "").rstrip("/")
        if not base:
            return []
        params: list[tuple[str, str]] = [
            ("space_id", space_id),
            ("q", q),
            ("limit", str(max(1, min(int(top_k), 100)))),
        ]
        for sid in source_ids or []:
            params.append(("source_ids", str(sid)))
        url = f"{base}/v1/library/chunks/search?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            return []
        if not isinstance(payload, list):
            return []
        hits: list[dict[str, Any]] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("space_id") or "") != str(space_id):
                continue
            hits.append(row)
        return hits


provider = DmsDocumentRetrieval()

__all__ = ["DmsDocumentRetrieval", "provider"]
