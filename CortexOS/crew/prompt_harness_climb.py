"""EPIC-PROMPT-HARNESS-CLIMB: measure CoT/gen vs DMS #180 (Cortex #227).

Consumes tip surfaces; does not rewrite them:
  - ``CortexOS.crew.cot_climb`` think/route/improve + ``measure_climb``
  - ``CortexOS.crew.freeroute`` arming / candidates / complete (free/normal only)
  - ``CortexOS.execution.distill_harness.run_distill`` ideas-only (D)

Never GPU-finetunes. Never pastes LangGraph/LangChain/n8n as an engine.
Never invents a better % than this run measured. Never stamps COMPLETE that
closes #212. Fixture coverage is not like-with-like vs the frozen DMS #180
curated 26 @ d2f116a6. Unarmed FreeRoute is fail-closed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SLICE = "EPIC-PROMPT-HARNESS-CLIMB"
ISSUE = 227
EXECUTE = "POST /crew/prompt-harness"
DISPLAY = "GET /crew/prompt-harness"
LIKE_WITH_LIKE_CORPUS = "dms-180-curated-26"
DMS_180_N = 26
JEPA_PATH = "proxy"

_PREMIUM = (
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-4-",
    "gpt-5",
    "o1-",
    "o1",
    "o3-",
    "claude-3-opus",
    "claude-3.5",
    "claude-3.7",
    "claude-opus",
    "claude-sonnet",
    "grok-4",
    "grok-3",
    "gemini-1.5-pro",
    "gemini-2.5-pro",
)
_FINETUNE = ("finetune", "ft:", "lora", "qlora", "jepa-trained")
_FREE_MARK = (":free", "/free", "-free")
_NORMAL_FAMILIES = (
    "deepseek",
    "qwen",
    "llama",
    "mistral",
    "gemma",
    "phi-",
    "gpt-oss",
    "kimi",
    "yi-",
    "glm-",
    "openai/gpt-oss",
)


def _baseline() -> dict[str, Any]:
    from CortexOS.crew import freeroute as fr

    body = dict(fr.DMS_180_BASELINE)
    body.setdefault("cite", "DMS #180 Formal GREEN @ d2f116a6")
    body.setdefault("gen", "57.69%")
    body.setdefault("exact", "38.46%")
    body.setdefault("wrong", 0)
    body["n"] = DMS_180_N
    body["replaces_baseline"] = False
    body["invented_better"] = False
    return body


def public_map() -> dict[str, Any]:
    """GET-shaped law. No model call. No invented climb %."""
    return {
        "ok": True,
        "slice": SLICE,
        "issue": ISSUE,
        "execute": EXECUTE,
        "display": DISPLAY,
        "complete": False,
        "status": "INCOMPLETE",
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "like_with_like": False,
        "climb_measured": False,
        "distill": {
            "path": "D",
            "ideas_only": True,
            "gpu_finetune": False,
            "via": "CortexOS.execution.distill_harness.run_distill",
        },
        "freeroute": (
            "consume crew.freeroute public API; free/normal models only; "
            "unarmed fail-closed"
        ),
        "cot_climb": "consume CortexOS.crew.cot_climb.measure_climb; leftover #212 OPEN",
        "jepa": "proxy cosine; no trained path named",
        "excel_ppt": "deferred #197 #198 #199",
        "live_5000_ci": False,
        "live_8020_ci": False,
        "issue_211_complete": False,
        "issue_212_complete": False,
        "issue_227_complete": False,
        "closes_212": False,
        "langgraph": False,
        "gpu_finetune": False,
    }


def model_lane(model: str, kind: str = "") -> str:
    """Classify a FreeRoute candidate. Unknown is not free/normal (fail-closed)."""
    blob = (model or "").strip().lower()
    kind_l = (kind or "").strip().lower()
    if kind_l in {"premium", "paid", "frontier"}:
        return "premium"
    if kind_l in {"finetune", "lora", "trained"}:
        return "finetune"
    if any(mark in blob for mark in _FINETUNE) or kind_l in _FINETUNE:
        return "finetune"
    if any(mark in blob for mark in _PREMIUM):
        return "premium"
    if kind_l in {"free", "normal"}:
        return kind_l
    if any(mark in blob for mark in _FREE_MARK):
        return "free"
    if any(fam in blob for fam in _NORMAL_FAMILIES):
        return "normal"
    return "unknown"


def is_free_or_normal(model: str, kind: str = "") -> bool:
    return model_lane(model, kind) in {"free", "normal"}


def filter_free_normal(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in candidates or []:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("model") or "")
        kind = str(row.get("kind") or "")
        if not model:
            continue
        if is_free_or_normal(model, kind):
            item = dict(row)
            item["lane"] = model_lane(model, kind)
            out.append(item)
    return out


def _honesty() -> dict[str, Any]:
    return {
        "complete": False,
        "status": "INCOMPLETE",
        "slice": SLICE,
        "issue": ISSUE,
        "measured_baseline": _baseline(),
        "replaces_baseline": False,
        "invented_better": False,
        "like_with_like": False,
        "climb_measured": False,
        "issue_211_complete": False,
        "issue_212_complete": False,
        "issue_227_complete": False,
        "closes_212": False,
        "jepa": JEPA_PATH,
        "gpu_finetune": False,
        "live_5000_ci": False,
        "live_8020_ci": False,
        "langgraph": False,
        "excel_ppt": "deferred #197 #198 #199",
    }


def _refuse(reason: str, **extra: Any) -> dict[str, Any]:
    body = _honesty()
    body.update(
        {
            "ok": False,
            "status": "REFUSE",
            "refuse_reason": reason,
            "values": [],
        }
    )
    body.update(extra)
    return body


def _ban_reasons(
    *,
    claim_complete: bool,
    gpu_finetune: bool,
    trained_jepa: bool,
    claimed_gen: str,
    live_5000_ci: bool,
    live_8020_ci: bool,
    paste_langgraph: bool,
    distill_recipe: Mapping[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if claim_complete:
        reasons.append("BAN: invent COMPLETE that closes #212 (CoT leftover stays OPEN)")
    if gpu_finetune:
        reasons.append("BAN: GPU finetune; distill (D) is ideas-only")
    if trained_jepa:
        reasons.append("BAN: invent trained JEPA; proxy only")
    if live_5000_ci or live_8020_ci:
        reasons.append("BAN: invent live-host :5000/:8020 green")
    if paste_langgraph:
        reasons.append("BAN: LangGraph/LangChain/n8n paste; Netie-native only")
    raw = distill_recipe if isinstance(distill_recipe, Mapping) else {}
    for flag in ("gpu_finetune", "finetune", "lora", "qlora"):
        if raw.get(flag):
            reasons.append("BAN: GPU finetune; distill (D) is ideas-only")
            break
    if raw.get("trained_jepa") or raw.get("trained"):
        reasons.append("BAN: invent trained JEPA; proxy only")
    if raw.get("paste") or raw.get("paste_engine") or raw.get("engine_role") == "product_engine":
        reasons.append("BAN: analog paste as product_engine; ideas-only distill")
    chosen = str(raw.get("chosen_engine") or raw.get("product_engine") or "")
    if chosen.lower() in {"langgraph", "langchain", "n8n", "langflow", "mybot"}:
        reasons.append(f"BAN: {chosen} paste; Netie-native only")
    claimed = (claimed_gen or "").strip()
    if claimed in {"99.95%", "100%", "100.00%", "99.99%"}:
        reasons.append("BAN: invent better % than measured")
    if claimed and claimed != "57.69%" and claimed.endswith("%"):
        try:
            val = float(claimed.rstrip("%"))
        except ValueError:
            val = -1.0
        if val > 57.69:
            reasons.append(
                "BAN: claimed_gen better than frozen DMS #180 without like-with-like measure"
            )
    return list(dict.fromkeys(reasons))


def _like_with_like(corpus: str, ids: Sequence[str]) -> bool:
    """Delegate to cot_climb pin. Label + n==26 is not like-with-like."""
    from CortexOS.crew import cot_climb

    return cot_climb.like_with_like(corpus, ids)


def distill_ideas(recipe: Mapping[str, Any] | None) -> dict[str, Any]:
    """Ideas-only D path. Distill COMPLETE is not climb COMPLETE."""
    from CortexOS.execution.distill_harness import run_distill

    raw = dict(recipe) if isinstance(recipe, Mapping) else {}
    if not str(raw.get("option_id") or "").strip():
        raw["option_id"] = "gencfsm_dag"
    out = run_distill(raw)
    ideas = [
        str(c.get("title") or c.get("netie_target") or "")
        for c in (out.get("candidates") or [])
        if isinstance(c, Mapping)
    ]
    return {
        "ok": bool(out.get("ok")) and out.get("status") != "REFUSE",
        "status": out.get("status"),
        "ideas_only": True,
        "gpu_finetune": False,
        "product_engine": out.get("product_engine") or "cortex",
        "shipped_engine": out.get("shipped_engine"),
        "option_id": out.get("option_id"),
        "ideas": [x for x in ideas if x],
        "reasons": list(out.get("reasons") or []),
        "engine_role": out.get("engine_role"),
        "climb_complete": False,
    }


def _wrap_complete(
    inner: Any,
    allowed: Sequence[Mapping[str, Any]],
) -> Any:
    allowed_models = {str(row.get("model") or "") for row in allowed if row.get("model")}

    async def gated(
        messages=None, *, purpose="", prompt="", **kwargs
    ):  # noqa: ANN001
        from CortexOS.crew import freeroute as fr

        pick = kwargs.get("pick")
        if pick is None and inner is None:
            try:
                current = fr.pick_route(purpose=purpose or "think")
            except Exception:  # noqa: BLE001 - pick miss is a named refuse
                current = None
            if current is not None and current.model not in allowed_models:
                row = next(iter(allowed))
                kwargs["pick"] = fr.RoutePick(
                    label=str(row.get("label") or "openvault"),
                    model=str(row.get("model") or ""),
                    kind="freeroute",
                    why="prompt-harness free/normal pin (premium not spent)",
                )
            elif current is not None:
                kwargs["pick"] = current
        runner = inner or fr.complete
        out = await runner(messages, purpose=purpose, prompt=prompt, **kwargs)
        if not isinstance(out, dict):
            return {
                "ok": False,
                "refused": "FreeRoute complete returned no envelope",
            }
        stamp = out.get("stamp") if isinstance(out.get("stamp"), Mapping) else {}
        route = out.get("route") if isinstance(out.get("route"), Mapping) else {}
        served = str(stamp.get("served") or out.get("model") or "")
        requested = str(stamp.get("requested") or route.get("model") or "")
        kind = str(route.get("kind") or "")
        probe = served or requested
        if probe and not is_free_or_normal(probe, kind):
            return {
                "ok": False,
                "status": "REFUSE",
                "refused": (
                    f"FreeRoute served/requested {probe!r} is not free/normal "
                    "(fail-closed; no premium spend)"
                ),
                "identity": out.get("identity"),
                "route": route or None,
                "stamp": stamp or None,
                "values": [],
            }
        return out

    return gated


async def run_harness(
    *,
    cases: Sequence[Mapping[str, Any]] | None = None,
    complete: Any | None = None,
    distill_recipe: Mapping[str, Any] | None = None,
    claim_complete: bool = False,
    gpu_finetune: bool = False,
    trained_jepa: bool = False,
    claimed_gen: str = "",
    live_5000_ci: bool = False,
    live_8020_ci: bool = False,
    paste_langgraph: bool = False,
    corpus: str = "",
    candidates: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Measure climb vs frozen #180. Stamp INCOMPLETE. Fail-closed when unarmed."""
    from CortexOS.crew import cot_climb
    from CortexOS.crew import freeroute as fr

    bans = _ban_reasons(
        claim_complete=claim_complete,
        gpu_finetune=gpu_finetune,
        trained_jepa=trained_jepa,
        claimed_gen=claimed_gen,
        live_5000_ci=live_5000_ci,
        live_8020_ci=live_8020_ci,
        paste_langgraph=paste_langgraph,
        distill_recipe=distill_recipe,
    )
    if bans:
        return _refuse("; ".join(bans), distill={"ideas_only": True, "gpu_finetune": False})

    arm = fr.arming()
    if not arm.get("armed"):
        return _refuse(
            "OpenVault FreeRoute unarmed: "
            + str(arm.get("detail") or "unreachable")
            + " (no invent-green keys; no invent-green climb %)",
            arming=dict(arm),
            final="UNARMED",
        )

    raw_cands = list(candidates) if candidates is not None else fr.candidates(
        purpose="generative_ask"
    )
    allowed = filter_free_normal(raw_cands)
    if not allowed:
        return _refuse(
            "no FreeRoute free/normal model armed (fail-closed; premium/finetune not spent)",
            arming=dict(arm),
            candidates=[dict(r) for r in raw_cands if isinstance(r, Mapping)],
            final="NO_FREE_NORMAL",
        )

    distilled = distill_ideas(distill_recipe)
    if distilled.get("status") == "REFUSE":
        return _refuse(
            "distill (D) refused (ideas-only; no GPU finetune; no analog paste): "
            + "; ".join(str(r) for r in distilled.get("reasons") or ["refused"]),
            arming=dict(arm),
            distill=distilled,
            final="DISTILL_REFUSE",
        )

    gated = _wrap_complete(complete, allowed)
    prepared: list[dict[str, Any]] = []
    for case in cases or []:
        if not isinstance(case, Mapping):
            continue
        row = dict(case)
        row["complete"] = _wrap_complete(row.get("complete") or complete, allowed)
        prepared.append(row)
    report = await cot_climb.measure_climb(
        prepared or None,
        corpus=corpus,
        ideas=list(distilled.get("ideas") or []),
        complete=None if prepared else gated,
    )
    this_run = dict(report.get("this_run") or {})
    like = bool(report.get("like_with_like"))
    outcome_ids = [str(row.get("id") or "") for row in (report.get("outcomes") or [])]
    if like != _like_with_like(corpus, outcome_ids):
        return _refuse(
            "BAN: harness like-with-like drifted from cot_climb pin (no invent climb %)",
            arming=dict(arm),
            distill=distilled,
            this_run=this_run,
        )
    this_gen = str(this_run.get("gen") or "0.00%")
    claimed = (claimed_gen or "").strip()
    if claimed and claimed != this_gen:
        return _refuse(
            f"BAN: claimed_gen {claimed!r} is not the measured this_run {this_gen!r}",
            arming=dict(arm),
            distill=distilled,
            this_run=this_run,
        )

    vs = dict(report.get("vs_baseline") or {})
    body = _honesty()
    body.update(
        {
            "ok": True,
            "arming": dict(arm),
            "models": [
                {"model": r.get("model"), "lane": r.get("lane"), "kind": r.get("kind")}
                for r in allowed
            ],
            "distill": distilled,
            "this_run": this_run,
            "vs_baseline": {
                "baseline_gen": "57.69%",
                "baseline_exact": "38.46%",
                "baseline_wrong": 0,
                "this_run_gen": this_gen,
                "like_with_like": like,
                "improved": bool(vs.get("improved")),
                "note": str(
                    vs.get("note")
                    or (
                        "like-with-like vs DMS #180 curated 26 @ d2f116a6"
                        if like
                        else "different corpus; do not replace DMS #180 numbers"
                    )
                ),
            },
            "like_with_like": like,
            "climb_measured": like,
            "outcomes": list(report.get("outcomes") or []),
            "issue_212": "OPEN/INCOMPLETE",
            "values": [],
        }
    )
    # Even a like-with-like improvement does not close #212 from this slice.
    body["complete"] = False
    body["status"] = "INCOMPLETE"
    body["issue_212_complete"] = False
    body["closes_212"] = False
    body["invented_better"] = False
    body["replaces_baseline"] = False
    return body
