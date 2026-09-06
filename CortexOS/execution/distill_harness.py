"""RSF-06 distill harness: options-class recipe → Netie-native learn traces.

Loop is improve → scale-check → improve. Analogs (myn8n / langchain / langflow /
gencfsm_dag) are distill/compete/learn. They are never pasted as Constructor
``product_engine``. Cortex stays the meta-router.

Study trees are names-only (RSF-03). gen-cFSM studies the in-repo
``CortexOS.execution.gen_cfsm`` module plus G1 docs, not a third orchestrator.
OmniRoute :20128 is refused; absorb notes prefer FreeRoute.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from CortexOS.execution.distill_options import (
    PRODUCT_ENGINE_ROLE,
    REQUIRED_OPTION_IDS,
    get_option,
    is_banned_engine_import,
)
from CortexOS.execution.rsf_boundary import is_omniroute_destination, read_study_tree
from CortexOS.execution.rsf_orchestrator import BAKEOFF_SURFACES, is_banned_engine_id

ROOT = Path(__file__).resolve().parents[2]
GEN_CFSM_PY = Path(__file__).resolve().parent / "gen_cfsm.py"
G1_DOC = ROOT / "docs" / "research" / "findings" / "G1_GEN_CFSM_JEPA.md"

LOOP_STEPS: tuple[str, ...] = ("improve", "scale_check", "improve")
SCALE_SURFACES: tuple[str, ...] = (*BAKEOFF_SURFACES, "constructor")
BANNED_CONSTRUCTOR_KINDS: frozenset[str] = frozenset({"n8n", *REQUIRED_OPTION_IDS})

STATUS_COMPLETE = "COMPLETE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_REFUSE = "REFUSE"
STATUS_ABSTAIN = "ABSTAIN"

_NATIVE: dict[str, dict[str, Any]] = {
    "myn8n": {
        "title": "Map analog workflow nodes onto Constructor canvas kinds",
        "netie_target": "CortexOS.constructor_graph",
        "via": None,
        "surfaces": ("constructor", "agentic_actions"),
    },
    "langchain": {
        "title": "Map analog chains onto governed DAG nodes behind the Cortex gate",
        "netie_target": "CortexOS.execution.dag_runner",
        "via": None,
        "surfaces": ("dms_rag", "normal_chat", "agentic_actions"),
    },
    "langflow": {
        "title": "Map analog flow canvas onto Constructor compile, not LangFlow runtime",
        "netie_target": "CortexOS.constructor_graph",
        "via": None,
        "surfaces": ("constructor", "agentic_actions"),
    },
    "gencfsm_dag": {
        "title": "Learn via existing gen_cfsm compile into dag_runner",
        "netie_target": "CortexOS.execution.gen_cfsm",
        "via": "CortexOS.execution.dag_runner",
        "surfaces": ("agentic_actions", "constructor"),
    },
}


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, (list, tuple)):
        return []
    return [x for x in value if isinstance(x, str) and x.strip()]


def _is_native_target(target: str) -> bool:
    blob = (target or "").strip()
    if not blob:
        return False
    if is_banned_engine_import(blob) or is_banned_engine_id(blob):
        return False
    root = blob.split(".", 1)[0]
    if is_banned_engine_id(root) or is_banned_engine_import(root):
        return False
    return blob.startswith("CortexOS.") or blob.startswith("skill_distill/")


def _kind_of(item: Mapping[str, Any]) -> str | None:
    raw = item.get("constructor_kind") or item.get("kind")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _module_of(item: Mapping[str, Any]) -> str:
    for key in ("netie_target", "module", "import"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _paste_reasons(recipe: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    reasons: list[str] = []
    role = recipe.get("engine_role")
    if role == PRODUCT_ENGINE_ROLE:
        reasons.append("BAN: analog cannot be Constructor product_engine")
    for flag in ("paste", "paste_engine", "ship_engine"):
        if recipe.get(flag):
            reasons.append("BAN: paste of upstream into Constructor engine")
            break
    chosen = recipe.get("chosen_engine") or recipe.get("product_engine")
    if isinstance(chosen, str) and is_banned_engine_id(chosen):
        reasons.append(
            f"BAN: {chosen} cannot be the Constructor product_engine. Distill-only analog."
        )
    dest = recipe.get("destination") or recipe.get("egress")
    if isinstance(dest, str) and is_omniroute_destination(dest):
        reasons.append("BAN: do not vendor OmniRoute :20128; prefer FreeRoute")

    for cand in candidates:
        kind = _kind_of(cand)
        if kind is not None and kind in BANNED_CONSTRUCTOR_KINDS:
            reasons.append(
                f"BAN: {kind!r} is a distill-only analog; not a Constructor engine kind"
            )
        target = _module_of(cand)
        if target and (
            is_banned_engine_import(target)
            or is_banned_engine_id(target)
            or is_banned_engine_id(target.split(".", 1)[0])
        ):
            reasons.append(f"BAN: {target} is not a Netie-native engine")
        if cand.get("engine_role") == PRODUCT_ENGINE_ROLE:
            reasons.append("BAN: candidate engine_role must not be product_engine")
        egress = cand.get("egress") or cand.get("destination")
        if isinstance(egress, str) and is_omniroute_destination(egress):
            reasons.append("BAN: do not vendor OmniRoute :20128; prefer FreeRoute")
    # unique, stable
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _headings(path: Path, *, cap: int = 8) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        if line.startswith("#"):
            title = line.lstrip("# ").strip()
            if title:
                out.append(title)
            if len(out) >= cap:
                break
    return out


def _study(option_id: str, *, study_root: Path | None) -> dict[str, Any]:
    if option_id == "gencfsm_dag":
        samples = []
        if GEN_CFSM_PY.is_file():
            samples.append(GEN_CFSM_PY.name)
        if G1_DOC.is_file():
            samples.append(G1_DOC.name)
        samples.extend(_headings(G1_DOC, cap=4))
        return {
            "ok": True,
            "allowed": True,
            "id": option_id,
            "engine_role": "learn",
            "path": str(GEN_CFSM_PY),
            "exists": GEN_CFSM_PY.is_file(),
            "samples": samples,
            "reasons": [],
            "native": "CortexOS.execution.gen_cfsm",
            "via": "CortexOS.execution.dag_runner",
        }
    return read_study_tree(option_id, root=study_root)


def _study_evidence(
    option: Mapping[str, Any],
    study: Mapping[str, Any],
    recipe: Mapping[str, Any],
) -> list[str]:
    evidence = _str_list(recipe.get("observations"))
    evidence.extend(_str_list(recipe.get("evidence")))
    samples = study.get("samples") or []
    if isinstance(samples, list):
        evidence.extend(f"study:{name}" for name in samples if isinstance(name, str))
    blurb = option.get("blurb")
    if isinstance(blurb, str) and blurb.strip():
        evidence.append(f"registry:{blurb.strip()}")
    if study.get("exists") and study.get("path"):
        evidence.append(f"path:{study['path']}")
    # unique
    seen: set[str] = set()
    out: list[str] = []
    for item in evidence:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _norm_candidate(raw: Mapping[str, Any], *, default_surfaces: Sequence[str]) -> dict[str, Any]:
    surfaces = _str_list(raw.get("surfaces")) or list(default_surfaces)
    evidence = _str_list(raw.get("evidence"))
    via = raw.get("via")
    return {
        "title": str(raw.get("title") or "").strip() or "Netie-native mapping",
        "netie_target": _module_of(raw),
        "via": via if isinstance(via, str) and via.strip() else None,
        "surfaces": surfaces,
        "evidence": evidence,
        "constructor_kind": _kind_of(raw),
        "egress": raw.get("egress") if isinstance(raw.get("egress"), str) else "freeroute",
        "engine_role": raw.get("engine_role"),
        "module": raw.get("module") if isinstance(raw.get("module"), str) else None,
    }


def _default_candidate(option: Mapping[str, Any], evidence: list[str]) -> dict[str, Any]:
    spec = _NATIVE[str(option["id"])]
    return {
        "title": spec["title"],
        "netie_target": spec["netie_target"],
        "via": spec.get("via"),
        "surfaces": list(spec["surfaces"]),
        "evidence": list(evidence),
        "constructor_kind": None,
        "egress": "freeroute",
        "engine_role": option["engine_role"],
        "module": None,
    }


def _candidates_from_recipe(
    recipe: Mapping[str, Any],
    option: Mapping[str, Any],
    study: Mapping[str, Any],
) -> list[dict[str, Any]]:
    spec = _NATIVE[str(option["id"])]
    raw = recipe.get("proposed") or recipe.get("candidates") or []
    items = [c for c in raw if isinstance(c, Mapping)] if isinstance(raw, list) else []
    study_ev = _study_evidence(option, study, recipe)
    if items:
        out = []
        for item in items:
            cand = _norm_candidate(item, default_surfaces=spec["surfaces"])
            if not cand["evidence"]:
                cand["evidence"] = list(study_ev)
            out.append(cand)
        return out
    if not study_ev:
        return []
    return [_default_candidate(option, study_ev)]


def _surface_fail(candidate: Mapping[str, Any], surface: str) -> str | None:
    target = candidate.get("netie_target") or ""
    kind = candidate.get("constructor_kind")
    if not candidate.get("evidence"):
        return "missing evidence; will not invent-green COMPLETE"
    if not _is_native_target(str(target)):
        return f"{target or '(missing)'} is not a Netie-native target"
    if kind is not None and kind in BANNED_CONSTRUCTOR_KINDS:
        return f"{kind!r} cannot land on Constructor"
    if surface not in SCALE_SURFACES:
        return f"unknown scale surface {surface!r}"
    egress = candidate.get("egress") or ""
    if isinstance(egress, str) and is_omniroute_destination(egress):
        return "OmniRoute gated; prefer FreeRoute"
    if surface == "constructor" and kind in BANNED_CONSTRUCTOR_KINDS:
        return f"constructor paste of {kind!r}"
    return None


def scale_check(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Fail closed. No invented targets. Analogs are not executed."""
    failures: list[dict[str, str]] = []
    surfaces: dict[str, dict[str, Any]] = {}
    for surface in SCALE_SURFACES:
        surface_fails: list[str] = []
        for cand in candidates:
            reason = _surface_fail(cand, surface)
            if reason:
                title = str(cand.get("title") or cand.get("netie_target") or "candidate")
                surface_fails.append(f"{title}: {reason}")
                failures.append({"surface": surface, "title": title, "reason": reason})
        surfaces[surface] = {
            "ok": not surface_fails,
            "note": (
                "constructor kinds stay Netie-native; analogs not executed"
                if surface == "constructor"
                else "instrumentation only; analog runtime not shipped"
            ),
            "failures": surface_fails,
        }
    if not candidates:
        failures.append(
            {
                "surface": "constructor",
                "title": "(none)",
                "reason": "no native candidates; will not invent-green COMPLETE",
            }
        )
        surfaces["constructor"]["ok"] = False
        surfaces["constructor"]["failures"] = [
            "no native candidates; will not invent-green COMPLETE"
        ]
    return {
        "ok": not failures,
        "surfaces": surfaces,
        "failures": failures,
    }


def _improve_retry(
    candidates: Sequence[Mapping[str, Any]],
    check: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Drop scale-check failures. Never mint a replacement without evidence."""
    failed_titles = {f["title"] for f in check.get("failures") or [] if isinstance(f, dict)}
    kept: list[dict[str, Any]] = []
    for cand in candidates:
        title = str(cand.get("title") or cand.get("netie_target") or "candidate")
        if title in failed_titles:
            continue
        if _surface_fail(cand, "constructor"):
            continue
        kept.append(dict(cand))
    return kept


def _learn_trace(
    *,
    option: Mapping[str, Any],
    study: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    status: str,
    reasons: Sequence[str],
    loop: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    oid = str(option["id"])
    facts = [
        {
            "fact": (
                f"{oid} stays {option['engine_role']}; product engine is cortex; "
                "analogs are not shipped"
            ),
            "evidence": "docs",
            "confidence": "high",
            "promote": "none",
        }
    ]
    for cand in candidates:
        facts.append(
            {
                "fact": f"{cand['title']} → {cand['netie_target']}",
                "evidence": "observed",
                "confidence": "med",
                "promote": "parking" if status != STATUS_COMPLETE else "none",
            }
        )
    return {
        "id": f"rsf06_{oid}_{uuid.uuid4().hex[:8]}",
        "source": "distill_harness",
        "option_id": oid,
        "engine_role": option["engine_role"],
        "product_engine": "cortex",
        "status": "normalized",
        "improve_status": status,
        "study": {
            "path": study.get("path"),
            "exists": study.get("exists"),
            "samples": list(study.get("samples") or []),
        },
        "facts": facts,
        "netie_implications": [
            c["title"] + " (" + c["netie_target"] + ")" for c in candidates
        ],
        "citations": [
            f"distill: CortexOS/execution/distill_options.py#{oid}",
            "distill: CortexOS/execution/distill_harness.py",
        ],
        "reasons": list(reasons),
        "loop": [dict(step) for step in loop],
        "distill_trace": "skill_distill/DISTILL.md",
    }


def _write_traces(out_dir: Path, trace: Mapping[str, Any]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    oid = str(trace["option_id"])
    json_path = out_dir / f"{oid}_learn_trace.json"
    md_path = out_dir / f"{oid}_capture.md"
    json_path.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    rows = [
        f"| {f['fact']} | {f['evidence']} | {f['confidence']} | {f['promote']} |"
        for f in (trace.get("facts") or [])
        if isinstance(f, dict)
    ]
    md = "\n".join(
        [
            f"id: {trace['id']}",
            "source: distill_harness",
            f"option_id: {oid}",
            "distill_trace: skill_distill/DISTILL.md",
            f"status: {trace['status']}",
            "",
            "## Extracted facts",
            "",
            "| Fact | Evidence | Confidence | Promote |",
            "|------|----------|------------|---------|",
            *rows,
            "",
            "## Netie implications",
            "",
            *[f"- {line}" for line in (trace.get("netie_implications") or [])],
            "",
            "## Citations",
            "",
            *[f"- {c}" for c in (trace.get("citations") or [])],
            "",
        ]
    )
    md_path.write_text(md, encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _result(
    *,
    status: str,
    option: Mapping[str, Any],
    study: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
    reasons: Sequence[str],
    loop: Sequence[Mapping[str, Any]],
    out_dir: Path | None,
) -> dict[str, Any]:
    trace = _learn_trace(
        option=option,
        study=study,
        candidates=candidates,
        status=status,
        reasons=reasons,
        loop=loop,
    )
    paths: dict[str, str] = {}
    if out_dir is not None:
        paths = _write_traces(Path(out_dir), trace)
    return {
        "ok": status == STATUS_COMPLETE,
        "status": status,
        "option_id": option["id"],
        "engine_role": option["engine_role"],
        "product_engine": "cortex",
        "shipped_engine": None,
        "candidates": [dict(c) for c in candidates],
        "learn_trace": trace,
        "loop": [dict(step) for step in loop],
        "reasons": list(reasons),
        "study": {
            "path": study.get("path"),
            "exists": bool(study.get("exists")),
            "samples": list(study.get("samples") or []),
            "engine_role": study.get("engine_role") or option["engine_role"],
        },
        "artifact_paths": paths,
    }


def run_distill(
    recipe: Mapping[str, Any] | None,
    *,
    study_root: Path | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    """Run improve → scale-check → improve. Never vendors analog engines."""
    raw = recipe if isinstance(recipe, Mapping) else {}
    option_id = str(raw.get("option_id") or "").strip()
    empty_option = {
        "id": option_id or "(missing)",
        "engine_role": "distill_only",
        "blurb": "",
    }
    if not option_id:
        return _result(
            status=STATUS_ABSTAIN,
            option=empty_option,
            study={"exists": False, "samples": [], "path": None},
            candidates=[],
            reasons=["option_id required; will not invent a distill"],
            loop=[{"step": "improve", "round": 1, "status": STATUS_ABSTAIN}],
            out_dir=out_dir,
        )
    try:
        option = get_option(option_id)
    except KeyError:
        return _result(
            status=STATUS_ABSTAIN,
            option={**empty_option, "id": option_id},
            study={"exists": False, "samples": [], "path": None},
            candidates=[],
            reasons=[f"unknown distill option {option_id!r}"],
            loop=[{"step": "improve", "round": 1, "status": STATUS_ABSTAIN}],
            out_dir=out_dir,
        )
    if option["engine_role"] == PRODUCT_ENGINE_ROLE:
        return _result(
            status=STATUS_REFUSE,
            option={**option, "engine_role": "distill_only"},
            study={"exists": False, "samples": [], "path": None, "engine_role": "distill_only"},
            candidates=[],
            reasons=["BAN: analog cannot be Constructor product_engine"],
            loop=[{"step": "improve", "round": 1, "status": STATUS_REFUSE}],
            out_dir=out_dir,
        )

    study = _study(option_id, study_root=study_root)
    if study.get("allowed") is False:
        return _result(
            status=STATUS_REFUSE,
            option=option,
            study=study,
            candidates=[],
            reasons=list(study.get("reasons") or ["study tree must not be Constructor product_engine"]),
            loop=[{"step": "improve", "round": 1, "status": STATUS_REFUSE}],
            out_dir=out_dir,
        )

    candidates = _candidates_from_recipe(raw, option, study)
    paste = _paste_reasons(raw, candidates)
    if paste:
        return _result(
            status=STATUS_REFUSE,
            option=option,
            study=study,
            candidates=[],
            reasons=paste,
            loop=[{"step": "improve", "round": 1, "status": STATUS_REFUSE, "reasons": paste}],
            out_dir=out_dir,
        )

    loop: list[dict[str, Any]] = [
        {
            "step": "improve",
            "round": 1,
            "status": STATUS_INCOMPLETE,
            "candidate_count": len(candidates),
        }
    ]
    check = scale_check(candidates)
    loop.append(
        {
            "step": "scale_check",
            "round": 1,
            "ok": check["ok"],
            "surfaces": {k: {"ok": v["ok"]} for k, v in check["surfaces"].items()},
            "failures": list(check["failures"]),
        }
    )
    if check["ok"] and candidates:
        loop.append({"step": "improve", "round": 2, "status": STATUS_COMPLETE, "note": "scale-check passed"})
        return _result(
            status=STATUS_COMPLETE,
            option=option,
            study=study,
            candidates=candidates,
            reasons=[],
            loop=loop,
            out_dir=out_dir,
        )

    retried = _improve_retry(candidates, check)
    check2 = scale_check(retried)
    loop.append(
        {
            "step": "improve",
            "round": 2,
            "status": STATUS_COMPLETE if check2["ok"] and retried else STATUS_INCOMPLETE,
            "candidate_count": len(retried),
            "note": "dropped scale-check failures; will not invent-green COMPLETE",
        }
    )
    loop.append(
        {
            "step": "scale_check",
            "round": 2,
            "ok": check2["ok"],
            "failures": list(check2["failures"]),
        }
    )
    if check2["ok"] and retried:
        return _result(
            status=STATUS_COMPLETE,
            option=option,
            study=study,
            candidates=retried,
            reasons=[],
            loop=loop,
            out_dir=out_dir,
        )
    reasons = [
        f["reason"] for f in (check2["failures"] or check["failures"]) if isinstance(f, dict)
    ] or ["scale-check failed; will not invent-green COMPLETE"]
    return _result(
        status=STATUS_INCOMPLETE,
        option=option,
        study=study,
        candidates=retried,
        reasons=reasons,
        loop=loop,
        out_dir=out_dir,
    )


__all__ = [
    "BANNED_CONSTRUCTOR_KINDS",
    "LOOP_STEPS",
    "SCALE_SURFACES",
    "STATUS_ABSTAIN",
    "STATUS_COMPLETE",
    "STATUS_INCOMPLETE",
    "STATUS_REFUSE",
    "run_distill",
    "scale_check",
]
