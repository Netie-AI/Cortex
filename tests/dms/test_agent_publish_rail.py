"""C-SEC-7 — agent publish rail deny audit (S1 × F5).

Complements tests/dms/test_s1_agents.py with the negative paths that prove
nothing publishes autonomously:
  * detectors import NO model/LLM client (detection is deterministic SQL);
  * a rejected run can never be approved afterwards (no publish after reject);
  * a fresh detector config with no data does not fire (no phantom publish).
"""
from __future__ import annotations

import inspect

import pytest


@pytest.fixture
def lake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DMS_LAKEHOUSE_HOME", str(tmp_path / "lakehouse"))
    monkeypatch.setenv("DMS_OPS_DB", str(tmp_path / "ops.db"))
    from packs.dms.lakehouse import catalog

    catalog.reset_mode_cache()
    yield tmp_path
    catalog.reset_mode_cache()


def test_detectors_have_no_llm_dependency():
    from packs.dms.agents import detectors

    src = inspect.getsource(detectors)
    for banned in ("litellm", "openai", "anthropic", "answer_engine", "brain", "JudgmentModel"):
        assert banned not in src, f"detector must be pure SQL; found {banned!r}"


def test_no_publish_after_reject(lake_home):
    from packs.dms.agents import employee, registry
    from packs.dms.lakehouse.catalog import connect

    con = connect()
    con.execute("CREATE OR REPLACE TABLE lake.bronze.s AS SELECT * FROM (VALUES (1),(2),(3),(4)) t(v)")
    con.close()
    registry.create_agent("rail", name="Rail", created_by="steward",
                          detector_cfg={"type": "rowcount", "table": "bronze.s",
                                        "op": ">", "bound": 2})
    run = employee.run_agent("rail", actor="steward")
    assert run["status"] == "pending_approval"

    employee.reject_run(run["run_id"], approver="steward", reason="not actionable")
    # A rejected run must be terminal — approval (and thus publish) is refused.
    with pytest.raises(PermissionError):
        employee.approve_run(run["run_id"], approver="steward")

    from packs.dms.agents.employee import OUTPUTS
    assert not (OUTPUTS / "steward" / run["run_id"]).exists()  # nothing published


def test_below_bound_never_fires(lake_home):
    from packs.dms.agents import employee, registry
    from packs.dms.lakehouse.catalog import connect

    con = connect()
    con.execute("CREATE OR REPLACE TABLE lake.bronze.q AS SELECT * FROM (VALUES (1)) t(v)")
    con.close()
    registry.create_agent("quiet", created_by="steward",
                          detector_cfg={"type": "rowcount", "table": "bronze.q",
                                        "op": ">", "bound": 100})
    run = employee.run_agent("quiet", actor="steward")
    assert run["status"] == "no_trigger"  # no report, no pending approval, no publish
