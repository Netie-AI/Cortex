"""Re-record the loop-off golden. Run on the parent tree, never on a loop branch:

    git worktree add /tmp/parent c7469da4
    cp -r tests/test_loop /tmp/parent/tests/
    cd /tmp/parent && PYTHONPATH=/tmp/parent:/tmp/parent/packages \
        python -m pytest -q -p no:cacheprovider tests/test_loop/record_flag_off_golden.py
"""

from __future__ import annotations

import json

from tests.dms.test_c_mem_contract_ask import ask_http  # noqa: F401
from tests.test_loop.flag_off_cases import GOLDEN, run_cases


def test_record_flag_off_golden(ask_http, monkeypatch) -> None:  # noqa: F811
    monkeypatch.delenv("CORTEX_ANALYSIS_LOOP", raising=False)
    golden = run_cases(ask_http, monkeypatch)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n", encoding="utf-8")
