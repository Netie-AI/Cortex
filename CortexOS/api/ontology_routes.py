"""GET /dms/ontology: the Studio page read of pack YAML the engine already loads.

Viewer gate is the same ``require_role("viewer")`` the other ``/dms/`` reads
use. The caller passes that dependency in so this module does not import
``packs``. Insights ranking and the constructor catalog stay on their own routes.
"""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from CortexOS.ontology.page import OntologyPageError, ontology_page

_PATHS: tuple[tuple[str, str, str], ...] = (
    ("/dms/ontology", "summary", "dms.ontology.summary"),
    ("/dms/ontology/objects", "objects", "dms.ontology.objects"),
    ("/dms/ontology/links", "links", "dms.ontology.links"),
    ("/dms/ontology/actions", "actions", "dms.ontology.actions"),
    ("/dms/ontology/functions", "functions", "dms.ontology.functions"),
    ("/dms/ontology/metrics", "metrics", "dms.ontology.metrics"),
    ("/dms/ontology/graph", "graph", "dms.ontology.graph"),
)


def _read(section: str, pack: str | None) -> dict[str, Any]:
    try:
        return ontology_page(section, pack)
    except OntologyPageError as exc:
        raise HTTPException(status_code=404, detail=exc.code) from None


def register_ontology_routes(app: Any, viewer: Callable[..., Any]) -> None:
    """Mount the seven Studio ontology reads. ``viewer`` is ``require_role("viewer")``."""
    router = APIRouter(tags=["ontology"], dependencies=[Depends(viewer)])

    for path, section, operation_id in _PATHS:
        _add(router, path, section, operation_id)

    app.include_router(router)


def _add(router: APIRouter, path: str, section: str, operation_id: str) -> None:
    def _section(pack: str | None = None) -> dict[str, Any]:
        """Pack vocabulary for this section. Not an Insights ranking."""
        return _read(section, pack)

    _section.__name__ = operation_id.replace(".", "_")
    router.add_api_route(path, _section, methods=["GET"], operation_id=operation_id)
