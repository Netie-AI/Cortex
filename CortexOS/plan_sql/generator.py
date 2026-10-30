"""C-LOOP-A (#303): SQL through OpenVault FreeRoute only.

``PlanSqlGenerator`` is the seam other loop slices inject or wrap. ``sql()``
asks for one SELECT (``prior_violations`` lets a caller feed gate errors back).
``plan()`` does not call a model: the ask seam folds that rung into the single
``sql()`` call so one attempt is one FreeRoute ``complete``. Each model step
returns a ``ModelStep`` stamped from the actual FreeRoute response.

``sql(..., reading=SECOND_READING)`` is the vote's second reading: its own
framing (``payload.second_reading_messages``), so it is a different prompt from
the folded first reading. ``sql_prompt()`` returns the exact messages ``sql()``
would send, and ``ModelStep.prompt_sha256`` is the hash of the ones it did
send, so the ask seam can refuse to count two readings of one prompt.

The generator only produces text. It holds no executor and no DB handle, and
it reaches a model only through ``CortexOS.integrations.freeroute`` (import
contract ``plan-sql-freeroute-only``). The caller gates and executes, and refuses any step whose route
stamp FreeRoute did not journal.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Protocol

from CortexOS.integrations import freeroute
from CortexOS.plan_sql.payload import (
    FIRST_READING,
    PlanSqlRequest,
    plan_lines,
    prompt_sha256,
    reading_messages,
    reconfirm_messages,
)

TASK_SQL = "plan-sql-sql"
TASK_RECONFIRM = "plan-sql-reconfirm"
SERVED_BY_FREEROUTE = freeroute.IMPL

_SELECT = re.compile(r"\b(select|with)\b", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class StepStamp:
    """One step of a plan+SQL run. Model steps copy ``served_*`` from FreeRoute."""

    served_step: str
    served_by: str
    served_at: str = field(default_factory=_now)
    served_provider: str | None = None
    served_model: str | None = None
    served_local: bool = False
    served_reason: str = ""
    served_call_id: str = ""
    # Copied from the FreeRoute stamp when CORTEX_OV_STRICT is on. Empty stays
    # out of public() and out of line() so a flag-off step reads as before.
    model_requested: str = ""
    model_served: str | None = None

    @classmethod
    def from_route(cls, step: str, route: freeroute.RouteStamp | None, reason: str) -> StepStamp:
        if route is None:
            return cls(step, SERVED_BY_FREEROUTE, served_reason=reason or "not sent")
        return cls(
            step,
            route.impl,
            served_provider=route.served_provider,
            served_model=route.served_model,
            served_local=bool(route.served_local),
            served_reason=reason or route.served_reason,
            served_call_id=route.call_id,
            model_requested=route.model_requested,
            model_served=route.model_served,
        )

    @classmethod
    def cortex(cls, step: str, by: str, reason: str) -> StepStamp:
        return cls(step, by, served_reason=reason)

    def line(self) -> str:
        local = "true" if self.served_local else "false"
        text = (
            f"step {self.served_step}: served_by={self.served_by} "
            f"served_provider={self.served_provider or 'none'} "
            f"served_model={self.served_model or 'none'} served_local={local}"
        )
        if self.served_call_id:
            text += f" call={self.served_call_id}"
        if self.model_requested or self.model_served:
            shown = self.model_served if self.model_served else "none"
            asked = self.model_requested or "none"
            text += f" model_requested={asked} model_served={shown}"
        if self.served_reason:
            text += f" ({freeroute.redact(self.served_reason, limit=200)})"
        return text

    def public(self) -> dict[str, Any]:
        body = asdict(self)
        if not self.model_requested and self.model_served is None:
            body.pop("model_requested", None)
            body.pop("model_served", None)
        return body


@dataclass(frozen=True)
class ModelStep:
    """What one model step produced. ``route`` is the FreeRoute stamp, if one was sent."""

    kind: str
    ok: bool
    text: str
    reason: str
    stamp: StepStamp
    route: freeroute.RouteStamp | None = None
    plan: tuple[str, ...] = ()
    # payload.prompt_sha256 of the messages this step sent. Empty when the
    # generator did not say (an injected one): no prompt is then provably distinct.
    prompt_sha256: str = ""


class PlanSqlGenerator(Protocol):
    """The seam the ask path calls. ``sql(..., reading=SECOND_READING)`` is the
    vote's second reading; the ask seam passes ``reading`` only on that call.

    ``sql_prompt(request, plan, *, prior_violations=(), reading=FIRST_READING)``
    (the exact messages ``sql()`` would send) is deliberately not a member: the
    ask seam reads it with ``getattr`` and treats a generator without it as one
    whose second reading cannot be shown to differ, so it gets no vote. A
    Protocol member would make that fail-closed case unrepresentable.
    """

    def plan(self, request: PlanSqlRequest) -> ModelStep: ...

    def sql(
        self,
        request: PlanSqlRequest,
        plan: ModelStep,
        *,
        prior_violations: Sequence[str] = (),
        reading: str = FIRST_READING,
    ) -> ModelStep: ...


def _step(kind: str, out: freeroute.Completion) -> ModelStep:
    reason = "" if out.ok else (out.reason or "FreeRoute returned no usable text")
    stamp = StepStamp.from_route(kind, out.stamp, reason)
    plan = plan_lines(out.text) if kind.startswith("plan") and out.ok else ()
    return ModelStep(kind, out.ok, out.text if out.ok else "", reason, stamp, out.stamp, plan)


class FreeRoutePlanSqlGenerator:
    """Default generator. One FreeRoute call per ``sql()`` or ``reconfirm()``.

    Raises ``FreeRouteStrictError`` when ``CORTEX_OV_STRICT`` is on and
    OpenVault refuses that pin or returns HTTP 429. The ladder must not
    try another model after that.
    """

    def __init__(
        self,
        *,
        pin: str = "",
        pin_source: str = "",
        max_tokens: int = 600,
        timeout: float = 45.0,
        tier: str = "",
    ) -> None:
        self._pin = pin
        self._pin_source = pin_source
        self._max_tokens = max_tokens
        self._timeout = timeout
        # "" is the first model. "strong" is the later OpenVault retry.
        self._tier = tier
        self.pin = pin

    def _name(self, base: str) -> str:
        return f"{base}-strong" if self._tier == "strong" else base

    def _complete(
        self, task: str, messages: list[dict[str, Any]], accept: Any
    ) -> freeroute.Completion:
        # Schema leaves the box: the OpenVault leave-machine gate runs first.
        return freeroute.complete(
            task,
            messages,
            temperature=0.0,
            max_tokens=self._max_tokens,
            timeout=self._timeout,
            accept=accept,
            pin=self._pin,
            pin_source=self._pin_source,
            egress="leave",
        )

    def plan(self, request: PlanSqlRequest) -> ModelStep:
        """No model call. The ask seam folds planning into ``sql()``."""
        del request
        kind = self._name("plan")
        reason = "folded into the sql call"
        return ModelStep(kind, True, "", reason, StepStamp.cortex(kind, "cortex:plan-folded", reason))

    def sql_prompt(
        self,
        request: PlanSqlRequest,
        plan: ModelStep,
        *,
        prior_violations: Sequence[str] = (),
        reading: str = FIRST_READING,
    ) -> list[dict[str, Any]]:
        """The exact messages :meth:`sql` sends for these arguments. No model call."""
        return reading_messages(
            request, plan.plan, reading=reading, prior_violations=prior_violations
        )

    def sql(
        self,
        request: PlanSqlRequest,
        plan: ModelStep,
        *,
        prior_violations: Sequence[str] = (),
        reading: str = FIRST_READING,
    ) -> ModelStep:
        messages = self.sql_prompt(
            request, plan, prior_violations=prior_violations, reading=reading
        )
        out = self._complete(
            self._name(TASK_SQL), messages, lambda t: bool(_SELECT.search(t or ""))
        )
        return replace(_step(self._name("sql"), out), prompt_sha256=prompt_sha256(messages))

    def reconfirm(self, request: PlanSqlRequest, failures: Sequence[str]) -> ModelStep:
        """Ask why this would abstain, and for the closest answerable question."""

        def _accept(text: str) -> bool:
            upper = (text or "").upper()
            return "WHY:" in upper and "CLOSEST:" in upper

        out = self._complete(
            self._name(TASK_RECONFIRM), reconfirm_messages(request, failures), _accept
        )
        return _step(self._name("reconfirm"), out)


__all__ = [
    "FreeRoutePlanSqlGenerator",
    "ModelStep",
    "PlanSqlGenerator",
    "SERVED_BY_FREEROUTE",
    "StepStamp",
    "TASK_RECONFIRM",
    "TASK_SQL",
]
