"""SUGGEST (#308) guard-removed proofs: each must-fail fails without its guard.

Each case reruns the named must-fail tests in a subprocess with one guard
switched off (tests/test_suggest/suggest_guard_off.py) and asserts they FAIL,
then that the same tests pass with the guard on. A must-fail that still passes
without its guard is not testing the guard.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = "tests/test_suggest"
UNIT = f"{HERE}/test_suggest_followups.py"
SEAM = f"{HERE}/test_suggest_ask_seam.py"
HTTP = f"{HERE}/test_suggest_contract_ask.py"
MODEL = f"{HERE}/test_suggest_freeroute.py"
IMPORTS = f"{HERE}/test_suggest_import_contract.py"
PROVIDERS = ("anthropic", "openai", "litellm", "httpx", "requests", "aiohttp", "google", "cohere", "mistralai", "groq")

CASES = {
    "grounding": (
        [
            f"{UNIT}::test_must_fail_suggestion_outside_grant_or_schema_is_rejected",
            f"{UNIT}::test_must_fail_model_rewording_that_widens_is_not_served",
            f"{MODEL}::test_must_fail_freeroute_rewording_that_widens_is_refused",
            f"{MODEL}::test_must_fail_contract_ask_model_cannot_widen_followups",
        ],
        [
            *(
                f"test_must_fail_suggestion_outside_grant_or_schema_is_rejected[{case}]"
                for case in (
                    "bare_column_not_in_result", "column_not_in_schema", "column_of_ungranted_table",
                    "hidden_column", "table_not_in_schema", "text_names_hidden_column",
                    "text_names_ungranted_table", "text_names_ungrounded_identifier",
                    "text_quotes_unreferenced_value", "ungranted_table", "value_not_in_result",
                )
            ),
            *(
                f"test_must_fail_model_rewording_that_widens_is_not_served[{case}]"
                for case in ("ungranted_table", "invented_column", "hidden_column", "invented_value")
            ),
            *(
                f"test_must_fail_freeroute_rewording_that_widens_is_refused[{case}]"
                for case in ("ungranted_table", "invented_column", "invented_value")
            ),
            "test_must_fail_contract_ask_model_cannot_widen_followups",
        ],
    ),
    "abstain": (
        [
            f"{SEAM}::test_must_fail_no_followups_on_an_abstain",
            f"{SEAM}::test_must_fail_no_followups_on_a_clarify",
            f"{HTTP}::test_must_fail_contract_ask_abstain_or_clarify_carries_no_followups",
        ],
        [
            *(
                f"test_must_fail_no_followups_on_an_abstain[{shape}]"
                for shape in (
                    "badge_abstain", "badge_blocked", "layer_abstain",
                    "provenance_model_abstain", "route_refused", "unmapped_badge",
                )
            ),
            *(
                f"test_must_fail_no_followups_on_a_clarify[{shape}]"
                for shape in (
                    "clarification_payload", "clarify_flag", "route_clarify", "route_needs_clarification",
                )
            ),
            *(
                f"test_must_fail_contract_ask_abstain_or_clarify_carries_no_followups[{q}"
                for q in ("revenue by location", "what is the weather", "drop table inventory", "stock by category")
            ),
        ],
    ),
    "provider_contract": (
        [f"{IMPORTS}::test_must_fail_import_contract_breaks"],
        [f"test_must_fail_import_contract_breaks[{p}-followups]" for p in PROVIDERS],
    ),
}


def _pytest(node_ids: list[str], guard_off: str | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    args = [sys.executable, "-m", "pytest", "-q", "-rf", "-p", "no:cacheprovider", *node_ids]
    if guard_off:
        env["SUGGEST_GUARD_OFF"] = guard_off
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / HERE), env.get("PYTHONPATH", "")])
        args[3:3] = ["-p", "suggest_guard_off"]
    return subprocess.run(
        args, cwd=ROOT, env=env, text=True, capture_output=True, timeout=600, check=False
    )


@pytest.mark.parametrize("guard", sorted(CASES))
def test_must_fail_tests_fail_without_their_guard(guard: str) -> None:
    node_ids, must_fail = CASES[guard]
    off = _pytest(node_ids, guard)
    failed = [line for line in off.stdout.splitlines() if line.startswith("FAILED ")]
    assert off.returncode == 1, off.stdout + off.stderr
    for name in must_fail:
        assert any(name in line for line in failed), (
            f"{name} passed with guard {guard} off:\n{off.stdout}"
        )

    on = _pytest(node_ids, None)
    assert on.returncode == 0, on.stdout + on.stderr
