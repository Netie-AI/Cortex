"""The use-case benchmark itself must stay green and free."""

from __future__ import annotations


def test_usecase_bench_all_green(tmp_path):
    from bench.usecases import run_all

    report = run_all(state_dir=tmp_path / "state", write=False)

    failed = [f"{c['surface']}/{c['case']}: {c['note']}" for c in report["cases"] if not c["ok"]]
    assert failed == []
    assert report["token_cost"] == 0
    assert report["total"] >= 12


def test_usecase_bench_restores_global_paths(tmp_path):
    from CortexOS.execution import app_store, routine_scheduler, scoreboard
    from bench.usecases import run_all

    before = (scoreboard.DB_PATH, routine_scheduler.DB_PATH, app_store.DB_PATH, app_store.APPS_ROOT)
    run_all(state_dir=tmp_path / "state", write=False)
    after = (scoreboard.DB_PATH, routine_scheduler.DB_PATH, app_store.DB_PATH, app_store.APPS_ROOT)

    assert before == after
