"""RSF-02 Cortex consumer: invent-green CERTIFIED must raise, not type-check."""

from __future__ import annotations

import pytest

from CortexOS.rsf import RsfConsumerError, parse_rsf_artifact


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
