"""RSF-02 Cortex consumer: invent-green CERTIFIED must raise, not type-check."""

from __future__ import annotations

import pytest

from CortexOS.rsf import RsfConsumerError, parse_rsf_artifact, parse_rsf_trace


def test_certified_without_chosen_option_is_refused() -> None:
    with pytest.raises(RsfConsumerError, match="chosen_option"):
        parse_rsf_artifact(
            {
                "stage": "segment",
                "status": "CERTIFIED",
                "chosen_option": None,
                "options": ["region", "country"],
                "evidence": ["column=sales_fact.region"],
            }
        )


def test_parse_rsf_trace_refuses_certified_after_abstain() -> None:
    research = {
        "stage": "research",
        "status": "CERTIFIED",
        "chosen_option": "warehouse_grant",
        "options": ["warehouse_grant"],
        "evidence": ["grant=sales_fact"],
    }
    segment = {
        "stage": "segment",
        "status": "ABSTAIN",
        "chosen_option": None,
        "options": ["region"],
        "evidence": [],
    }
    classify = {
        "stage": "classify",
        "status": "CERTIFIED",
        "chosen_option": "sku",
        "options": ["sku"],
        "evidence": ["metric=units_sold"],
    }
    with pytest.raises(RsfConsumerError, match="classify must not be CERTIFIED"):
        parse_rsf_trace([research, segment, classify])
