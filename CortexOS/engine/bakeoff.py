"""Engine bakeoff - probe OpenAI-compatible backends; soft-fail when absent.

Compares reachability + TTFT + tokens/s when a server answers /v1/models or
/api/tags. Never fabricates GPU numbers. Research boosters (TurboQuant, etc.)
are listed as gated, not auto-scored until their bench_gate runs green.

CLI: python -m bench.engine_bakeoff [--json PATH]
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from CortexOS.engine import registry
from CortexOS.engine.just_works import compare_backends_narrative, just_works
from CortexOS.engine.lubricant import thesis
from CortexOS.paths import data_path

_UA = "Netie-Cortex-Bakeoff/0.1"
# Cold probe: short timeout. Concurrent backends -> wall clock ~1 timeout, not N*paths.
_TIMEOUT = 0.9
_PRIMARY_PATHS = {
    "ollama": ("/api/tags",),
    "vllm": ("/v1/models",),
    "sglang": ("/v1/models",),
    "llamacpp": ("/v1/models",),
    "colibri": ("/v1/models",),
}


def _get_json(url: str, *, timeout: float = _TIMEOUT) -> tuple[bool, Any, float]:
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(200_000)
            ms = (time.perf_counter() - t0) * 1000
            try:
                return True, json.loads(raw.decode("utf-8", errors="replace")), ms
            except json.JSONDecodeError:
                return True, {"raw": raw[:200].decode("utf-8", errors="replace")}, ms
    except Exception as exc:  # noqa: BLE001 - offline is a bakeoff outcome
        ms = (time.perf_counter() - t0) * 1000
        return False, {"error": f"{type(exc).__name__}: {exc}"}, ms


def _base_url(backend_id: str) -> str:
    b = registry.BACKEND_BY_ID[backend_id]
    env = os.environ.get(b.base_url_env, "").strip()
    if env:
        return env.rstrip("/")
    return f"http://127.0.0.1:{b.port}"


def probe_backend(backend_id: str) -> dict[str, Any]:
    """Soft probe - never raises. Measures health latency only (not full gen)."""
    if backend_id not in registry.BACKEND_BY_ID:
        return {"id": backend_id, "ok": False, "error": "unknown_backend"}
    b = registry.BACKEND_BY_ID[backend_id]
    base = _base_url(backend_id)
    paths = _PRIMARY_PATHS.get(backend_id) or ("/v1/models", "/api/tags", "/health")
    last_err = ""
    for path in paths:
        ok, body, ms = _get_json(f"{base}{path}")
        if ok:
            return {
                "id": backend_id,
                "name": b.name,
                "ok": True,
                "base_url": base,
                "probe_path": path,
                "health_ms": round(ms, 1),
                "needs_gpu": b.needs_gpu,
                "install_how": b.install_how,
                "detail": "reachable",
            }
        last_err = str((body or {}).get("error") or "unreachable")
    return {
        "id": backend_id,
        "name": b.name,
        "ok": False,
        "base_url": base,
        "health_ms": None,
        "needs_gpu": b.needs_gpu,
        "install_how": b.install_how,
        "error": last_err,
        "detail": "offline_or_blocked",
    }


def score_probe(row: dict[str, Any]) -> float:
    """Higher is better. Offline -> 0. Fast health -> higher. GPU servers bonus if up."""
    if not row.get("ok"):
        return 0.0
    ms = float(row.get("health_ms") or 9999)
    speed = max(0.05, min(1.0, 200.0 / max(ms, 1.0)))
    gpu_bonus = 0.15 if row.get("needs_gpu") else 0.05
    return round(min(1.0, speed + gpu_bonus), 4)


def run_bakeoff(*, hardware: dict[str, Any] | None = None) -> dict[str, Any]:
    ids = [b.id for b in registry.BACKENDS]
    t0 = time.perf_counter()
    # Probe all backends in parallel so absent servers do not serialize timeouts.
    with ThreadPoolExecutor(max_workers=max(4, len(ids))) as pool:
        futs = {pool.submit(probe_backend, bid): bid for bid in ids}
        by_id: dict[str, dict[str, Any]] = {}
        for fut in as_completed(futs):
            by_id[futs[fut]] = fut.result()
    probes = [by_id[i] for i in ids]
    wall_ms = round((time.perf_counter() - t0) * 1000, 1)
    for p in probes:
        p["score"] = score_probe(p)
    ranked = sorted(probes, key=lambda r: (-r["score"], r["id"]))
    live = [p for p in probes if p["ok"]]
    plan = just_works(hardware)
    rec = plan["config"]["backend"]
    for p in ranked:
        if p["ok"] and p["id"] in ("vllm", "sglang") and p["score"] >= 0.3:
            if (hardware or {}).get("allow_docker_gpu") or not plan["platform"]["system"].startswith("win"):
                rec = p["id"]
                break
        if p["ok"] and p["id"] == "ollama":
            rec = "ollama"
            break

    gated = [
        {"id": o.id, "gate": o.bench_gate, "backends": list(o.backends)}
        for o in registry.OPTIMIZERS
        if o.research or o.bench_gate
    ]

    return {
        "ok": True,
        "thesis": thesis(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "probes": probes,
        "ranking": [{"id": r["id"], "score": r["score"], "ok": r["ok"]} for r in ranked],
        "live_count": len(live),
        "recommended_backend": rec,
        "just_works": plan,
        "narrative": compare_backends_narrative(),
        "middleware_boosters_gated": gated,
        "probe_wall_ms": wall_ms,
        "notes": [
            "Scores are reachability+health latency proxies, not full generation tok/s.",
            "Full tok/s / TTFT gen benches require a live model pull - opt-in later.",
            "Cortex remains the lubricant: pick the best live backend, govern on top.",
            f"Cold probes ran concurrently in {wall_ms} ms wall time.",
        ],
    }


def write_report(report: dict[str, Any], out_dir: Path | None = None) -> Path:
    root = out_dir or data_path("bench")
    root.mkdir(parents=True, exist_ok=True)
    path = root / "engine_bakeoff_last.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = root / "engine_bakeoff_last.md"
    lines = [
        "# Engine bakeoff",
        "",
        f"Generated: {report['generated_at']}",
        f"Thesis: **{report['thesis']['role']}** - {report['thesis']['metaphor']}",
        f"Live backends: {report['live_count']}/{len(report['probes'])}",
        f"Recommended: `{report['recommended_backend']}`",
        f"Probe wall: {report.get('probe_wall_ms', '?')} ms",
        "",
        "| Backend | OK | health_ms | score |",
        "|---------|----|-----------|-------|",
    ]
    for p in report["probes"]:
        ms = p.get("health_ms")
        ms_s = "-" if ms is None else str(ms)
        lines.append(
            f"| {p['id']} | {'yes' if p['ok'] else 'no'} | {ms_s} | {p.get('score')} |"
        )
    lines.extend(["", "## Just Works human copy", ""])
    for h in report["just_works"]["human"]:
        lines.append(f"- {h}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", default=None)
    parser.add_argument("--vram", type=float, default=0.0)
    parser.add_argument("--allow-docker-gpu", action="store_true")
    args = parser.parse_args()
    hw = {
        "vram_gb": args.vram,
        "nvidia": {"present": args.vram >= 6},
        "allow_docker_gpu": args.allow_docker_gpu,
    }
    report = run_bakeoff(hardware=hw)
    out = Path(args.json) if args.json else write_report(report)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    else:
        write_report(report)
    print(
        json.dumps(
            {
                "recommended": report["recommended_backend"],
                "live": report["live_count"],
                "probe_wall_ms": report.get("probe_wall_ms"),
                "out": str(out),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
