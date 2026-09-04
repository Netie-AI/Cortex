"""Live persona probe — real ask envelopes with E1-style checks.

Exercises the full stack: OpenVault keys → Cortex contract ask → customer envelope.
Each probe carries engineer_intent (skill pass-through) separate from raw_question.

CLI:  python -m bench.live_probe [--persona warehouse_clerk] [--json PATH]
Requires DMS_ASK_URL. There is no default host or port.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from bench.envelope import (
    ASK_URL_REQUIRED,
    ask_endpoint,
    assert_ask_envelope,
    resolve_ask_url,
)

ROOT = Path(__file__).resolve().parents[1]
PERSONAS_PATH = ROOT / "bench" / "live_personas.yaml"
RESULTS_DIR = ROOT / "bench" / "results"


def _live_ask(question: str, dms_url: str) -> dict[str, Any]:
    import time
    import urllib.error
    import urllib.request

    last_err: Exception | None = None
    for attempt in range(4):
        req = urllib.request.Request(
            ask_endpoint(dms_url),
            data=json.dumps({"question": question}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_err = RuntimeError(f"DMS ask HTTP {exc.code}: {body[:400]}")
            if exc.code in {429, 503} and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise last_err from exc
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_err or RuntimeError("live ask failed")


def _classify(envelope: dict[str, Any], expect: str) -> str:
    abstained = bool(envelope.get("abstained"))
    badge = str(envelope.get("badge") or "")
    text = str(envelope.get("text") or envelope.get("answer") or "").lower()
    has_values = bool(envelope.get("values") or envelope.get("rows"))
    has_sql = bool(envelope.get("sql_used"))
    is_abstain = badge.upper() in {"ABSTAIN", "REFUSED", "BLOCKED"}

    if expect == "abstain":
        if abstained or is_abstain:
            return "pass"
        if has_values and not abstained:
            return "fail_confident_on_abstain"
        return "fail_no_abstain"

    if expect == "blocked":
        if abstained or "not allowed" in text or is_abstain:
            return "pass"
        if has_sql and "DROP" in str(envelope.get("sql_used") or "").upper():
            return "fail_executed_destructive"
        return "pass"

    if abstained or is_abstain:
        return "fail_abstained_on_answerable"
    if not has_sql:
        return "fail_no_sql"
    if badge not in {
        "L0_CERTIFIED",
        "L1_GOVERNED_METRIC",
        "L2_VALIDATED",
        "L2_ANOMALOUS",
        "certified",
        "governed_metric",
        "query_skill",
        "session",
    }:
        return "fail_bad_badge"
    if abstained != is_abstain:
        return "fail_e1_badge_abstain_mismatch"
    return "pass"


def load_personas(path: Path = PERSONAS_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def run_probe(
    *,
    persona: str | None = None,
    dms_url: str,
    personas_path: Path = PERSONAS_PATH,
) -> dict[str, Any]:
    data = load_personas(path=personas_path)

    results: list[dict[str, Any]] = []
    passes = fails = envelope_errors = 0

    for persona_id, block in (data.get("personas") or {}).items():
        if persona and persona_id != persona:
            continue
        for probe in block.get("probes") or []:
            raw = probe["raw_question"]
            try:
                env = _live_ask(raw, dms_url)
                assert_ask_envelope(env)
                outcome = _classify(env, probe.get("expect", "correct"))
                env_err = ""
            except AssertionError as exc:
                outcome = "fail_envelope"
                env = {}
                env_err = str(exc)[:400]
                envelope_errors += 1
            except Exception as exc:  # noqa: BLE001
                outcome = "error"
                env = {}
                env_err = str(exc)[:400]

            if outcome == "pass":
                passes += 1
            else:
                fails += 1

            results.append(
                {
                    "persona": persona_id,
                    "id": probe["id"],
                    "raw_question": raw,
                    "engineer_intent": probe.get("engineer_intent"),
                    "expect": probe.get("expect"),
                    "tool_surface": probe.get("tool_surface"),
                    "outcome": outcome,
                    "badge": env.get("badge"),
                    "abstained": env.get("abstained"),
                    "text": (env.get("text") or env.get("answer") or "")[:120],
                    "envelope_error": env_err,
                }
            )

    return {
        "dms_url": dms_url,
        "totals": {
            "total": passes + fails,
            "pass": passes,
            "fail": fails,
            "envelope_errors": envelope_errors,
        },
        "items": results,
        "passed": fails == 0 and envelope_errors == 0,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Live persona probe with envelope E1–E8")
    p.add_argument("--persona", default=None)
    p.add_argument("--dms-url", default=None, help="Override DMS_ASK_URL")
    p.add_argument("--json", type=Path, default=RESULTS_DIR / "live_probe_last_run.json")
    args = p.parse_args()

    ask_url = resolve_ask_url(args.dms_url)
    if not ask_url:
        print(ASK_URL_REQUIRED, file=sys.stderr)
        raise SystemExit(1)

    report = run_probe(persona=args.persona, dms_url=ask_url)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(json.dumps(report["totals"], indent=2))
    for item in report["items"]:
        if item["outcome"] != "pass":
            print(f"FAIL {item['persona']}/{item['id']}: {item['outcome']} badge={item.get('badge')}")

    if not report["passed"]:
        raise SystemExit(1)
    print("PASS")


if __name__ == "__main__":
    main()
