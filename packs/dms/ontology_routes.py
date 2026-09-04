"""GET /dms/ontology* -- YAML registry + semantic metrics for the DMS Ontology page.

DMS proxies these as /v1/ontology. Viewer read. No invented rows. P1 parked.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from packs.dms.ontology.http import ontology_section
from packs.dms.security.api_auth import Caller, require_role

router = APIRouter(prefix="/dms/ontology", tags=["ontology"])

_SECTIONS = frozenset({"", "objects", "links", "actions", "functions", "metrics", "graph"})


def _payload(section: str, pack: str | None) -> dict[str, Any]:
    try:
        return ontology_section(section, pack)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ontology pack: {pack}") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown ontology section: {section}") from exc


@router.get("")
def ontology_summary(
    pack: str | None = Query(None),
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    return _payload("", pack)


@router.get("/{section}")
def ontology_named_section(
    section: str,
    pack: str | None = Query(None),
    caller: Caller = Depends(require_role("viewer")),
) -> dict[str, Any]:
    _ = caller
    if section not in _SECTIONS or section == "":
        raise HTTPException(status_code=404, detail=f"unknown ontology section: {section}")
    return _payload(section, pack)


def register_ontology_routes(app: Any) -> None:
    app.include_router(router)
