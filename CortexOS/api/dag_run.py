"""POST /run — execute inline DAG JSON (Phase 2)."""

import json
from typing import Any

from fastapi import HTTPException, Request
from netie.result import Ok
from pydantic import BaseModel, Field

from CortexOS.packaging import FeatureNotInstalled, require_extra


class RunDAGRequest(BaseModel):
    dag: dict[str, Any] = Field(..., description="DAG envelope (parsed directly)")
    run_id: str = "integration_run"
    context: dict[str, Any] = Field(default_factory=dict)
    workflow_cost_ceiling_myr: float | None = None
    parallel: bool = False
    max_parallel: int | None = None
    resume: bool = False


def _serialize_records(records: list[Any]) -> list[dict[str, Any]]:
    from netie.routing.cost_ledger import NodeExecutionRecord

    out: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, NodeExecutionRecord):
            out.append(
                {
                    "node_id": record.node_id,
                    "tier": record.tier,
                    "model": record.model,
                    "latency_ms": record.latency_ms,
                    "prompt_tokens": record.prompt_tokens,
                    "completion_tokens": record.completion_tokens,
                    "cost_myr": record.cost_myr,
                    "cache_hit": record.cache_hit,
                    "status": record.status,
                    "ceiling_myr": record.ceiling_myr,
                    "error": record.error,
                }
            )
    return out


def register_dag_run_routes(app: Any) -> None:
    """Always register ``POST /run`` — core profile returns HTTP 501."""
    from CortexOS.api.feature_stubs import feature_not_installed_detail

    @app.post("/run")
    async def run_inline_dag(request: Request, body: RunDAGRequest) -> dict[str, Any]:
        try:
            require_extra("agentic", feature="dag_run")
        except FeatureNotInstalled as exc:
            raise HTTPException(
                status_code=501, detail=feature_not_installed_detail(exc)
            ) from exc

        from netie.execution.dag_runner import ExecutionContext, run_dag
        from netie.execution.model_router import ModelRouter
        from netie.fabrication.dsl_parser import parse_dsl

        dag_json = json.dumps(body.dag)
        parsed = parse_dsl(dag_json, intent_hash="inline")
        if not isinstance(parsed, Ok):
            raise HTTPException(status_code=400, detail=parsed.message)

        ledger = getattr(request.app.state, "ledger", None)
        if ledger is None:
            raise HTTPException(status_code=503, detail="Ledger not initialized")

        router = getattr(request.app.state, "model_router", None)
        if not isinstance(router, ModelRouter):
            router = ModelRouter()

        ctx = ExecutionContext(body.run_id, body.context)
        dag_result = await run_dag(
            parsed.value,
            ctx,
            router,
            ledger,
            workflow_cost_ceiling_myr=body.workflow_cost_ceiling_myr,
            parallel=body.parallel,
            max_parallel=body.max_parallel,
            resume=body.resume,
        )

        serialized: dict[str, Any] = {}
        for nid, nr in dag_result.outputs.items():
            serialized[nid] = {"output": nr.output, "tier": nr.tier, "cost_myr": nr.cost_myr}
        return {
            "run_id": body.run_id,
            "nodes": serialized,
            "total_myr": ledger.total_cost(body.run_id),
        }

    @app.get("/api/engine/runs/{run_id}/cost")
    async def get_run_cost(request: Request, run_id: str) -> dict[str, Any]:
        try:
            require_extra("agentic", feature="dag_run")
        except FeatureNotInstalled as exc:
            raise HTTPException(
                status_code=501, detail=feature_not_installed_detail(exc)
            ) from exc

        ledger = getattr(request.app.state, "ledger", None)
        if ledger is None:
            raise HTTPException(status_code=503, detail="Ledger not initialized")

        await ledger.hydrate_run_total(run_id)
        records = ledger.records_for_run(run_id)
        return {
            "run_id": run_id,
            "total_myr": ledger.total_cost(run_id),
            "records": _serialize_records(records),
        }
