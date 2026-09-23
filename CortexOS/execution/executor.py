"""Executor helpers: pre-call cost gating + adapter invocation."""

from dataclasses import dataclass, replace
from typing import Any

from netie.decision import decision_log
from netie.execution.errors import CostCeilingExceeded
from netie.execution.model_router import ModelRequest, ModelRouter
from netie.routing.adapters.base import AdapterRequest, AdapterResponse
from netie.routing.cost_ledger import CostLedger, NodeExecutionRecord, now_utc
from netie.routing.judgment_model import JudgmentModel, JudgmentRequest
from netie.routing.tiers import Tier
from netie.routing.token_estimate import estimate_prompt_tokens
from netie.security.redact_port import RedactionFailed, redact_prompt_text


@dataclass(slots=True)
class RoutedCompletionOutcome:
    """Returned by ``invoke_routed_completion`` together with routed accounting."""

    response: AdapterResponse
    tier: str
    model: str
    cost_myr: float


def adapter_token_estimate_family(provider: str) -> str | None:
    """Map resolved provider keys to estimator mode (tiktoken only for OpenAI-family)."""
    if provider == "openai":
        return "openai_compat"
    return None


def _decision_state(model_req: ModelRequest) -> dict[str, Any]:
    """The JudgmentModel state dict for ``model_req``, built exactly as ``route()`` does."""
    return JudgmentModel._state_for(
        JudgmentRequest(
            request_type=model_req.request_type,
            content=model_req.prompt,
            context_size=len(model_req.prompt),
            prior_tier_failures=int(model_req.metadata.get("prior_tier_failures", 0)),
            user_tier_budget=Tier(model_req.metadata.get("user_tier_budget", model_req.max_tier.value)),
            is_vip=bool(model_req.metadata.get("is_vip", False)),
        )
    )


def _log_decision(
    router: ModelRouter,
    model_req: ModelRequest,
    routed: Any,
    *,
    run_id: str,
    node_id: str,
    status: str,
    error: BaseException | None = None,
    cost_myr: float = 0.0,
) -> None:
    """KEV-LOG (#246): one JSONL line per outcome. Never raises, never alters the result."""
    try:
        state = _decision_state(model_req)
        decision_log.log_decision(
            run_id=run_id,
            node_id=node_id,
            request_type=model_req.request_type,
            default_tier=model_req.default_tier,
            max_tier=model_req.max_tier,
            tier=routed.tier,
            reason=routed.reason,
            status=status,
            state=state,
            judgment_model=getattr(router, "judgment_model", None),
            provider=routed.provider,
            model=routed.model,
            error=error,
            cost_myr=cost_myr,
        )
    except Exception:
        # log_decision already counts its own failures; this guards the state build.
        return


def effective_cost_ceiling(workflow_cost_ceiling_myr: float, node_cost_ceiling_myr: float | None) -> float:
    if node_cost_ceiling_myr is None:
        return workflow_cost_ceiling_myr
    return min(workflow_cost_ceiling_myr, node_cost_ceiling_myr)


async def invoke_routed_completion(
    router: ModelRouter,
    ledger: CostLedger,
    *,
    run_id: str,
    workflow_cost_ceiling_myr: float,
    node_id: str,
    model_req: ModelRequest,
    adapter_req: AdapterRequest,
    node_cost_ceiling_myr: float | None = None,
) -> RoutedCompletionOutcome:
    """
    1. Route → adapter + tier + model string
    2. Estimate prompt tokens & projected MYR (`max_tokens` as upper bound on completion tokens)
    3. Gates on ``ledger.enforce_ceiling(..., projected_additional_myr=projected)`` BEFORE the call
    4. Persist actual cost to Postgres (when engine configured) + update in-run cache via ``ledger.add``.
    """
    routed = router.route(model_req)
    ceiling = effective_cost_ceiling(
        workflow_cost_ceiling_myr,
        node_cost_ceiling_myr if node_cost_ceiling_myr is not None else model_req.cost_ceiling_myr,
    )

    if routed.tier == Tier.T0:
        started_at = now_utc()
        ended_at = started_at
        record = NodeExecutionRecord(
            run_id=run_id,
            node_id=node_id,
            tier=routed.tier.value,
            model=routed.model,
            latency_ms=0,
            prompt_tokens=0,
            completion_tokens=0,
            cost_myr=0.0,
            cache_hit=False,
            started_at=started_at,
            ended_at=ended_at,
            status="ok",
            ceiling_myr=ceiling,
            error=None,
        )
        await ledger.add(record)
        _log_decision(router, model_req, routed, run_id=run_id, node_id=node_id, status="ok")
        return RoutedCompletionOutcome(
            response=AdapterResponse(
                content="",
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=0,
                raw={"tier": "T0", "skipped_adapter": True, "reason": routed.reason},
            ),
            tier=routed.tier.value,
            model=routed.model,
            cost_myr=0.0,
        )

    # GH-01: every non-T0 call passes system + prompt through the active redactor
    # before cost estimation and before the adapter. Fail closed: a redactor that
    # raises records status=error and re-raises; the adapter is never reached.
    try:
        adapter_req = replace(
            adapter_req,
            system=redact_prompt_text(adapter_req.system),
            prompt=redact_prompt_text(adapter_req.prompt),
        )
    except RedactionFailed as exc:
        failed_at = now_utc()
        await ledger.add(
            NodeExecutionRecord(
                run_id=run_id,
                node_id=node_id,
                tier=routed.tier.value,
                model=routed.model,
                latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
                cost_myr=0.0,
                cache_hit=False,
                started_at=failed_at,
                ended_at=failed_at,
                status="error",
                ceiling_myr=ceiling,
                error=str(exc),
            )
        )
        _log_decision(router, model_req, routed, run_id=run_id, node_id=node_id, status="error", error=exc)
        raise

    prompt_blob = f"{adapter_req.system}\n{adapter_req.prompt}"
    est_prompt_tokens = estimate_prompt_tokens(
        prompt_blob,
        family=adapter_token_estimate_family(routed.provider),
    )
    projected_cost = routed.adapter.cost_myr(est_prompt_tokens, adapter_req.max_tokens)

    if not ledger.enforce_ceiling(run_id, ceiling, projected_additional_myr=projected_cost):
        ceiling_exc = CostCeilingExceeded(
            node_id,
            projected_myr=projected_cost,
            spent_myr=ledger.total_cost(run_id),
            ceiling_myr=ceiling,
        )
        _log_decision(
            router, model_req, routed, run_id=run_id, node_id=node_id, status="error", error=ceiling_exc
        )
        raise ceiling_exc

    started_at = now_utc()
    final_req = replace(adapter_req, model=routed.model)
    try:
        resp = await routed.adapter.complete(final_req)
        ended_at = now_utc()
        actual_cost = routed.adapter.cost_myr(resp.prompt_tokens, resp.completion_tokens)
        record = NodeExecutionRecord(
            run_id=run_id,
            node_id=node_id,
            tier=routed.tier.value,
            model=routed.model,
            latency_ms=resp.latency_ms,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            cost_myr=actual_cost,
            cache_hit=False,
            started_at=started_at,
            ended_at=ended_at,
            status="ok",
            ceiling_myr=ceiling,
            error=None,
        )
        await ledger.add(record)
        _log_decision(
            router, model_req, routed, run_id=run_id, node_id=node_id, status="ok", cost_myr=actual_cost
        )
        return RoutedCompletionOutcome(
            response=resp,
            tier=routed.tier.value,
            model=routed.model,
            cost_myr=actual_cost,
        )
    except Exception as exc:
        ended_at = now_utc()
        err_record = NodeExecutionRecord(
            run_id=run_id,
            node_id=node_id,
            tier=routed.tier.value,
            model=routed.model,
            latency_ms=int((ended_at - started_at).total_seconds() * 1000),
            prompt_tokens=0,
            completion_tokens=0,
            cost_myr=0.0,
            cache_hit=False,
            started_at=started_at,
            ended_at=ended_at,
            status="error",
            ceiling_myr=ceiling,
            error=str(exc),
        )
        await ledger.add(err_record)
        _log_decision(router, model_req, routed, run_id=run_id, node_id=node_id, status="error", error=exc)
        raise
