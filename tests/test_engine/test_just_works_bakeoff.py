"""Just Works + bakeoff + WD40 thesis tests (no live GPU required)."""

from __future__ import annotations

from CortexOS.engine.bakeoff import probe_backend, run_bakeoff, score_probe
from CortexOS.engine.just_works import just_works
from CortexOS.engine.lubricant import thesis


def test_thesis_is_lubricant_not_competitor():
    t = thesis()
    assert t["role"] == "lubricant"
    assert "vllm" in t["orchestrates"]
    assert "sglang" in t["orchestrates"]
    assert t["competes_with"] == []
    assert "PagedAttention" in " ".join(t["does_not_rewrite"])


def test_just_works_cpu_picks_ollama():
    plan = just_works({"vram_gb": 0, "nvidia": {"present": False}})
    assert plan["ok"] is True
    assert plan["config"]["backend"] == "ollama"
    assert "turboquant" not in plan["config"]["optimizers"]
    # Research boosters never auto-on; list may be empty when backend has no gated opts.
    assert all(b.get("gated") for b in plan["boosters_available"])
    assert any("No GPU" in h for h in plan["human"])


def test_just_works_windows_blocks_vllm_without_docker_flag():
    plan = just_works(
        {"vram_gb": 24, "nvidia": {"present": True}, "platform": "windows"},
        prefer="vllm",
    )
    # prefer is overridden on Windows unless allow_docker_gpu
    assert plan["config"]["backend"] == "ollama"


def test_just_works_windows_allows_vllm_with_flag():
    plan = just_works(
        {
            "vram_gb": 24,
            "nvidia": {"present": True},
            "platform": "windows",
            "allow_docker_gpu": True,
        },
        prefer="vllm",
    )
    assert plan["config"]["backend"] == "vllm"
    assert "kv_fp8" in plan["config"]["optimizers"] or "paged_attention" in plan["config"]["optimizers"]


def test_research_false_excludes_turboquant_from_defaults():
    plan = just_works({"vram_gb": 12, "nvidia": {"present": True}}, research=False)
    assert "turboquant" not in plan["config"]["optimizers"]


def test_bakeoff_soft_fails_offline(monkeypatch):
    def fake_probe(backend_id: str):
        return {
            "id": backend_id,
            "name": backend_id,
            "ok": backend_id == "ollama",
            "health_ms": 40.0 if backend_id == "ollama" else None,
            "needs_gpu": backend_id in ("vllm", "sglang"),
            "install_how": "native",
        }

    monkeypatch.setattr("CortexOS.engine.bakeoff.probe_backend", fake_probe)
    report = run_bakeoff(hardware={"vram_gb": 0})
    assert report["ok"] is True
    assert report["live_count"] == 1
    assert report["recommended_backend"] == "ollama"
    assert report["thesis"]["role"] == "lubricant"
    assert "probe_wall_ms" in report


def test_bakeoff_probes_concurrent_wall_time(monkeypatch):
    """Absent backends must not serialize full timeouts."""
    import time

    def slow_offline(backend_id: str):
        time.sleep(0.35)
        return {
            "id": backend_id,
            "name": backend_id,
            "ok": False,
            "health_ms": None,
            "needs_gpu": False,
            "install_how": "native",
        }

    monkeypatch.setattr("CortexOS.engine.bakeoff.probe_backend", slow_offline)
    t0 = time.perf_counter()
    report = run_bakeoff(hardware={"vram_gb": 0})
    elapsed = time.perf_counter() - t0
    # 5 backends * 0.35s serial would be ~1.75s; concurrent should be ~0.35-0.8s
    assert elapsed < 1.2
    assert report["probe_wall_ms"] < 1200
    assert report["live_count"] == 0


def test_score_probe_offline_is_zero():
    assert score_probe({"ok": False}) == 0.0
    assert score_probe({"ok": True, "health_ms": 50, "needs_gpu": False}) > 0
