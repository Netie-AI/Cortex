#!/usr/bin/env python3
"""KEV-CALIB (#247): calibration report over the tier-decision log.

Reads the KEV-LOG JSONL (``CORTEX_DECISION_LOG_PATH`` or
``data/engine/tier_decisions.jsonl``), joins it with ledger status and
scoreboard predicates through ``CortexOS.decision.labels`` and prints:

- n labelled rows, exclusions by reason, class balance;
- below n=300 labelled rows or below 30 negatives: ``INSUFFICIENT: ... no
  threshold claim`` and exit code 2, with no ECE, Brier, T or threshold
  printed;
- otherwise: fitted T (on ``log p`` pseudo-logits, stated as such), ECE, Brier,
  and serve_automation at error budgets 0.02, 0.05 and 0.10: the share of
  served decisions that could skip escalation, with the exact raw
  P(sufficient) threshold that reproduces it (the raw scale is the source of
  truth) and the T-scaled one, or ``inexact`` when float rounding in the
  scaling means no scaled threshold serves exactly the same rows.

This script reports. It never writes config and never touches
``CORTEX_DECISION_ABSTAIN_THRESHOLD``. Moving the threshold is a founder
decision taken on this report, not by it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Run as `python scripts/kev_calibration_report.py`, sys.path[0] is scripts/.
if str(ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(ROOT))

from CortexOS.decision import decision_log  # noqa: E402
from CortexOS.decision.calibration import (  # noqa: E402
    brier,
    ece,
    fit_temperature,
    serve_automation,
    softmax,
)
from CortexOS.decision.labels import LabelSet, build_labels, scoreboard_predicates  # noqa: E402

MIN_N = 300
MIN_NEGATIVES = 30
ERROR_BUDGETS: tuple[float, ...] = (0.02, 0.05, 0.10)
EXIT_OK = 0
EXIT_INSUFFICIENT = 2
EXIT_USAGE = 3


def _read_ledger_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except ValueError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def print_header(labelled: LabelSet, log_path: Path, out=sys.stdout) -> None:
    print(f"kev calibration report  log={log_path}", file=out)
    print(f"entries_read={labelled.total_entries}", file=out)
    print(f"n={labelled.n}", file=out)
    if labelled.excluded:
        for reason, count in sorted(labelled.excluded.items()):
            print(f"excluded {reason}={count}", file=out)
    else:
        print("excluded none=0", file=out)
    print(
        f"class_balance sufficient={labelled.positives} insufficient={labelled.negatives}",
        file=out,
    )


def report(labelled: LabelSet, log_path: Path, out=sys.stdout) -> int:
    print_header(labelled, log_path, out)
    if labelled.n < MIN_N:
        print(
            f"INSUFFICIENT: n={labelled.n} < {MIN_N} labelled rows; no threshold claim",
            file=out,
        )
        return EXIT_INSUFFICIENT
    if labelled.negatives < MIN_NEGATIVES:
        print(
            f"INSUFFICIENT: negatives={labelled.negatives} < {MIN_NEGATIVES}; "
            "no threshold claim",
            file=out,
        )
        return EXIT_INSUFFICIENT

    labels = labelled.labels
    raw_rows = [list(r) for r in labelled.prob_rows]
    logit_rows = [list(r) for r in labelled.pseudo_logit_rows]
    temperature = fit_temperature(logit_rows, labels)
    scaled_rows = [softmax(row, temperature) for row in logit_rows]

    print("temperature_note=fitted on log(p) pseudo-logits; the backend returns probabilities, not logits", file=out)
    print(f"T={temperature!r}", file=out)
    print(f"ece_raw={ece(raw_rows, labels):.4f}", file=out)
    print(f"ece_scaled={ece(scaled_rows, labels):.4f}", file=out)
    print(f"brier_raw={brier(raw_rows, labels):.4f}", file=out)
    print(f"brier_scaled={brier(scaled_rows, labels):.4f}", file=out)
    # The raw scale is the source of truth: the served prefix is computed once,
    # on the backend's own P(sufficient). Temperature scaling is monotone in
    # exact arithmetic but not in floats: at small T distinct raw values can
    # saturate to the same scaled value (or a tie order can flip), so the
    # scaled scale is never re-ranked. Its threshold is derived from prefix
    # membership and printed only when serving scaled >= it reproduces exactly
    # the raw prefix; otherwise it is reported ``inexact``, never guessed.
    # Thresholds are printed exactly (repr), because rounding can drop a whole
    # tie group at the threshold and break the budget.
    p_raw = [row[1] for row in raw_rows]
    p_scaled = [row[1] for row in scaled_rows]
    print(
        "serve_automation_note=share of served decisions that could skip escalation; "
        "a served tier that proved insufficient is always an error; "
        "the prefix is computed on the raw scale (source of truth): "
        f"serve when raw P(sufficient) >= p_raw_threshold (backend scale); "
        f"T-scaled P(sufficient) >= p_scaled_threshold (T={temperature!r}) serves the same rows "
        "unless it reads inexact; "
        "neither is on CORTEX_DECISION_ABSTAIN_THRESHOLD's scale",
        file=out,
    )
    for budget in ERROR_BUDGETS:
        share, raw_thr = serve_automation(p_raw, labels, budget)
        inexact_reason = None
        if raw_thr is None:
            raw_s = scaled_s = "none"
        else:
            raw_s = repr(raw_thr)
            prefix = {i for i, p in enumerate(p_raw) if p >= raw_thr}
            scaled_thr = min(p_scaled[i] for i in prefix)
            served_scaled = {i for i, p in enumerate(p_scaled) if p >= scaled_thr}
            if served_scaled == prefix:
                scaled_s = repr(scaled_thr)
            else:
                scaled_s = "inexact"
                inexact_reason = (
                    f"serving T-scaled >= {scaled_thr!r} would serve {len(served_scaled)} rows, "
                    f"not the {len(prefix)} rows of the raw prefix: T-scaling collapsed or "
                    "reordered distinct raw values in floating point; use p_raw_threshold"
                )
        print(
            f"serve_automation budget={budget:.2f} share={share:.4f} "
            f"p_raw_threshold={raw_s} p_scaled_threshold={scaled_s}",
            file=out,
        )
        if inexact_reason is not None:
            print(f"serve_automation_scaled_note budget={budget:.2f} {inexact_reason}", file=out)
    print("config_written=none (CORTEX_DECISION_ABSTAIN_THRESHOLD untouched)", file=out)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", type=Path, default=None, help="decision log JSONL (default: KEV-LOG path)")
    parser.add_argument(
        "--ledger-jsonl",
        type=Path,
        default=None,
        help="optional node_executions rows as JSONL (run_id, node_id, status, error)",
    )
    parser.add_argument(
        "--scoreboard-db",
        type=Path,
        default=None,
        help="optional scoreboard SQLite file (arch_runs.predicates_pass joined by run_id)",
    )
    args = parser.parse_args(argv)

    log_path = args.log if args.log is not None else decision_log.log_path()
    if not log_path.exists():
        print(f"kev calibration report  log={log_path}")
        print("n=0")
        print("excluded none=0")
        print("class_balance sufficient=0 insufficient=0")
        print(f"INSUFFICIENT: decision log not found at {log_path}; no threshold claim")
        return EXIT_INSUFFICIENT

    ledger_records = None
    if args.ledger_jsonl is not None:
        if not args.ledger_jsonl.exists():
            print(f"error: ledger file not found: {args.ledger_jsonl}", file=sys.stderr)
            return EXIT_USAGE
        ledger_records = _read_ledger_jsonl(args.ledger_jsonl)
    predicates = scoreboard_predicates(args.scoreboard_db) if args.scoreboard_db is not None else None

    entries = decision_log.read_entries(log_path)
    labelled = build_labels(entries, ledger_records=ledger_records, predicates=predicates)
    return report(labelled, log_path)


if __name__ == "__main__":
    sys.exit(main())
