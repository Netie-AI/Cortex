"""RSF-02 Cortex consumer. Schema of record: DMS dms_core.rsf (HTTP JSON).

Inbound body fields: artifact_id, stage, question, options, chosen_option,
route_trace, evidence, status, reasons. No dms_core import, no CCA, no
cortex_contract bump, no /v1/rsf route.

RSF-04 (``CortexOS.execution.rsf_orchestrator``) calls ``parse_rsf_artifact``
per stage and ``parse_rsf_trace`` on the run. ``RSF_STAGES`` order is the
pipeline: later CERTIFIED is illegal once a prior stage is not CERTIFIED.

Analog reuse: TAS-CONSTRUCTOR. Live: tests/dms/test_constructor_graph.py
(kind n8n refused). Analog read-only: none.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Fixed pipeline order (DMS schema of record + Constructor parseRsfTrace).
# Ticket #154 names the four stages; "prior" for CERTIFIED gating is this tuple.
RSF_STAGES = ("research", "segment", "classify", "filter")
RSF_STATUSES = frozenset({"CERTIFIED", "ABSTAIN", "REFUSE"})


class RsfConsumerError(ValueError):
    """Invent-green or unknown RSF wire shape. Fail closed."""


@dataclass(frozen=True)
class RsfArtifact:
    status: str
    options: tuple[str, ...]
    chosen_option: str | None = None
    evidence: tuple[str, ...] = ()
    stage: str = "research"

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if self.stage not in RSF_STAGES:
            raise RsfConsumerError(f"unknown RSF stage {self.stage!r}")
        if self.status not in RSF_STATUSES:
            raise RsfConsumerError(f"unknown status {self.status!r}")
        if self.status == "CERTIFIED":
            if not self.chosen_option or self.chosen_option not in self.options:
                raise RsfConsumerError("CERTIFIED requires chosen_option in options")
            if not self.evidence:
                raise RsfConsumerError("CERTIFIED requires evidence")
        elif self.chosen_option is not None:
            raise RsfConsumerError(f"{self.status} must not carry a chosen_option")


def parse_rsf_artifact(raw: Any) -> RsfArtifact:
    if not isinstance(raw, Mapping):
        raise RsfConsumerError("RSF artifact must be an object")
    chosen = raw.get("chosen_option")
    if chosen is not None and not isinstance(chosen, str):
        raise RsfConsumerError("chosen_option must be a string or null")
    options = raw.get("options") or ()
    evidence = raw.get("evidence") or ()
    if not isinstance(options, (list, tuple)) or not all(isinstance(x, str) for x in options):
        raise RsfConsumerError("options must be a list of strings")
    if not isinstance(evidence, (list, tuple)) or not all(isinstance(x, str) for x in evidence):
        raise RsfConsumerError("evidence must be a list of strings")
    return RsfArtifact(
        status=str(raw.get("status") or ""),
        options=tuple(options),
        chosen_option=chosen,
        evidence=tuple(evidence),
        stage=str(raw.get("stage") or ""),
    )


def require_certified_priors(trace: list[RsfArtifact] | list[Mapping[str, Any]]) -> None:
    """A later stage cannot be CERTIFIED after a prior ABSTAIN/REFUSE/missing.

    Order in ``RSF_STAGES`` is what "prior" means (DMS ``require_certified_priors``).
    """
    by_stage: dict[str, str] = {}
    for item in trace:
        if isinstance(item, RsfArtifact):
            by_stage[item.stage] = item.status
        elif isinstance(item, Mapping):
            by_stage[str(item.get("stage") or "")] = str(item.get("status") or "")
        else:
            raise RsfConsumerError("RSF trace item must be an artifact or object")
    blocked = False
    for stage in RSF_STAGES:
        status = by_stage.get(stage)
        if blocked:
            if status == "CERTIFIED":
                raise RsfConsumerError(
                    f"{stage} must not be CERTIFIED after a prior ABSTAIN/REFUSE/missing stage"
                )
            continue
        if status != "CERTIFIED":
            blocked = True


def parse_rsf_trace(raw: Any) -> list[RsfArtifact]:
    """Parse a run's stage artifacts. Invent-green CERTIFIED after a gap raises."""
    if not isinstance(raw, list):
        raise RsfConsumerError("RSF trace must be a list")
    out = [parse_rsf_artifact(item) for item in raw]
    seen: set[str] = set()
    for item in out:
        if item.stage in seen:
            raise RsfConsumerError(f"duplicate stage {item.stage}")
        seen.add(item.stage)
    require_certified_priors(out)
    return out
