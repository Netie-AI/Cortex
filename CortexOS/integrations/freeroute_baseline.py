"""ROUTER-1 baseline recorder (Cortex #269).

Refuses unless ``CORTEX_FREEROUTE_LEARN`` is set explicitly. Refuses to
record a baseline unless ``arming()`` reports ``armed=True`` and
``spendable_hops > 0``. Writes the arming reason either way. Does not invent
coverage / WRONG / cost / latency. A live pack run is not required for merge
and does not count until prove is armed. Masking is off until Cortex #268;
masking-off numbers must never be compared with masking-on runs.

Does not change ``arming()`` or RouteStamp served_* (#272).
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import Any

from CortexOS.integrations import freeroute as core

SLICE = "ROUTER-1"
ISSUE = 269
LEARN_UNSET = (
    "CORTEX_FREEROUTE_LEARN is unset; baseline refuses to run on the default"
)
UNARMED_REFUSE = (
    "FreeRoute is not armed with a spendable hop; baseline refuses to record "
    "(a run with zero spendable hops only measures refusals)"
)
MASKING_COMPARE = (
    "masking-off numbers must never be compared with masking-on runs"
)
PLAN_SOURCES = frozenset({"ontology_plan", "bind_plan", "other"})
PLAN_SOURCE_FROM = "per-answer plan_source stamp (#266); never labels"


def require_explicit_learn() -> None:
    if core.LEARN_ENV not in os.environ:
        raise SystemExit(LEARN_UNSET)


def arming_snapshot(arm: core.Arming | None = None) -> dict[str, Any]:
    """Read ``arming()``. Does not change arming rules or hop pooling."""
    current = arm if arm is not None else core.arming()
    return {
        "armed": bool(current.armed),
        "spendable_hops": int(current.spendable_hops),
        "arming_reason": current.reason,
        "arming_url": current.url,
        "arming_probed": bool(current.probed),
    }


def can_record(snap: Mapping[str, Any]) -> bool:
    return bool(snap.get("armed")) and int(snap.get("spendable_hops") or 0) > 0


def row_plan_source(envelope: Mapping[str, Any]) -> str:
    """Insights ranking vs generate, from the plan_source stamp only.

    Never from route labels, requested model, or other display strings.
    """
    raw = str(envelope.get("plan_source") or "").strip().lower()
    return raw if raw in PLAN_SOURCES else "other"


def evaluate(*, arm: core.Arming | None = None) -> dict[str, Any]:
    """Setup fingerprint. Always includes arming reason. Numbers stay null here."""
    require_explicit_learn()
    snap = arming_snapshot(arm)
    recorded = can_record(snap)
    learn = core.learn_state()
    store = core.store_state()
    return {
        "slice": SLICE,
        "issue": ISSUE,
        "masking_state": core.MASKING_STATE,
        "masking_compare_forbidden": True,
        "masking_compare": MASKING_COMPARE,
        "comparable_with_masking_on": False,
        "learn_enabled": learn["learn_enabled"],
        "learn_source": learn["learn_source"],
        "route_store_id": store["id"],
        "route_store": store,
        "armed": snap["armed"],
        "spendable_hops": snap["spendable_hops"],
        "arming_reason": snap["arming_reason"],
        "arming_url": snap["arming_url"],
        "arming_probed": snap["arming_probed"],
        "recorded": recorded,
        "baseline_recorded": recorded,
        "numbers": None,
        "coverage": None,
        "wrong": None,
        "mean_cost": None,
        "p50_latency_ms": None,
        "p95_latency_ms": None,
        "invented_numbers": False,
        "live_pack_run": False,
        "live_baseline_counts": False,
        "plan_source_from": PLAN_SOURCE_FROM,
        "refuse_reason": None if recorded else UNARMED_REFUSE,
        "note": (
            "This ticket only records today's setup when FreeRoute is armed "
            "with spendable hops. A live pack run is not required for merge "
            "and no live baseline number counts until prove is armed. Do not "
            "invent baseline numbers. When #268 merges, re-run with masking "
            "on; do not compare masking-off with masking-on. plan_source on "
            "each row is the Insights stamp (#266), never a label."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    _ = argv
    try:
        body = evaluate()
    except SystemExit as exc:
        msg = str(exc)
        sys.stderr.write(msg + "\n")
        return 2
    json.dump(body, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if not body.get("recorded"):
        sys.stderr.write(str(body.get("refuse_reason") or UNARMED_REFUSE) + "\n")
        sys.stderr.write(f"arming_reason: {body.get('arming_reason') or ''}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
