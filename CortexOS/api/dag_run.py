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


def register_dag_run_routes(app: Any) -> None:
    try:
        require_extra("agentic", feature="dag_run")
    except FeatureNotInstalled:
        return

    from netie.execution.dag_runner import ExecutionContext, run_dag
    from netie.execution.model_router import ModelRouter
    from netie.fabrication.dsl_parser import parse_dsl

    @app.post("/run")
    async def run_inline_dag(request: Request, body: RunDAGRequest) -> dict[str, Any]:
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
        )

        serialized: dict[str, Any] = {}
        for nid, nr in dag_result.outputs.items():
            serialized[nid] = {"output": nr.output, "tier": nr.tier, "cost_myr": nr.cost_myr}
        return {"run_id": body.run_id, "nodes": serialized}