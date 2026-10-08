"""#340a: with ``CORTEX_QUERY_SKILL`` unset, ``POST /v1/contract/ask`` answers
every bench question byte-identically to main.

The golden (``fixtures/ask_flag_off_golden_main.json``) was recorded by this
module on the base commit it names, with the flag and ``DMS_L2_ENABLED`` unset:
every golden, paraphrase and adversarial question, asked twice in Space alpha
against one skill store (pass 1 cold; pass 2 sees whatever pass 1 wrote, which
on main is a capture per certified / governed answer). Each response body is
hashed as sorted-key JSON after dropping the per-request ids in
``VOLATILE_KEYS``. Two rules, decided by the response's own ``sql_used`` and
applied identically to main and branch, keep the golden from drifting with
things the SQL does not fix:

* ``clock``: SQL reading ``CURRENT_DATE`` / ``NOW()`` returns rows that move
  with the date, so ``rows`` / ``row_count`` / ``answer`` are dropped; route,
  provenance, SQL, sources and every other key are still compared.
* ``unordered``: SQL with no ``ORDER BY`` (``GROUP BY`` / ``DISTINCT``) has no
  defined row order, and main returns them in a different order run to run, so
  rows compare as a multiset and the answer's row-preview bullet lines (which
  follow that order) are dropped; the rest of the answer text is compared.
* ``exact`` (everything else): rows and answer compared in order, except that
  rows tied on every ``ORDER BY`` key are sorted among themselves, and a
  preview bullet for a tied row is reduced to its key values, since SQL leaves
  the order of ties (and so which tied row the preview shows) undefined.

Re-record (on the base tree, never on this branch):
``CORTEX_RECORD_GOLDEN=<path> pytest tests/dms/test_ask_flag_off_golden_340.py``
"""

from __future__ import annotations

import collections
import hashlib
import itertools
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.dms.test_query_skill_gate_340 import (  # noqa: F401
    SESSION,
    WAREHOUSE_GRANT,
    ask_http,
    skills_db,
)

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).with_name("fixtures") / "ask_flag_off_golden_main.json"
VOLATILE_KEYS = frozenset({"answer_id", "audit_id", "drillthrough_token"})
CLOCK_KEYS = frozenset({"rows", "row_count", "answer"})
_CLOCK = re.compile(r"\b(current_date|current_timestamp|now\s*\(|today\s*\()", re.IGNORECASE)
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)
_ROW_LINE = re.compile(r"^\s+·\s")


def _questions() -> list[tuple[str, str]]:
    def load(rel: str) -> Any:
        return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))

    out = [(f"golden:{i['id']}", i["question"]) for i in load("bench/golden/dms_golden_v1.yaml")["items"]]
    for gid, qs in load("bench/golden/dms_paraphrase_v1.yaml")["paraphrases"].items():
        out += [(f"paraphrase:{gid}#{n}", q) for n, q in enumerate(qs)]
    for items in load("bench/golden/dms_adversarial_v1.yaml")["categories"].values():
        out += [(f"adversarial:{i['id']}", i["question"]) for i in items]
    return out


def _mode(body: dict[str, Any]) -> str:
    sql = str(body.get("sql_used") or "")
    if _CLOCK.search(sql):
        return "clock"
    if isinstance(body.get("rows"), list) and len(body["rows"]) > 1 and not _ORDER_BY.search(sql):
        return "unordered"
    return "exact"


def _row_key(row: Any) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _order_keys(sql: str) -> list[str]:
    clause = re.split(r"\border\s+by\b", sql, flags=re.IGNORECASE)[-1]
    clause = re.split(r"\b(limit|offset)\b", clause, flags=re.IGNORECASE)[0]
    return [re.split(r"\s+", item.strip())[0].split(".")[-1].strip('"') for item in clause.split(",")]


def _sort_ties(rows: list[Any], keys: list[str]) -> list[Any]:
    if not keys or not all(isinstance(r, dict) and all(k in r for k in keys) for r in rows):
        return rows
    out: list[Any] = []
    for _, group in itertools.groupby(rows, key=lambda r: [r[k] for k in keys]):
        out += sorted(group, key=_row_key)
    return out


def _tie_preview(body: dict[str, Any]) -> str:
    keys = _order_keys(str(body.get("sql_used") or ""))
    lines = str(body.get("answer") or "").splitlines()
    rows = body["rows"]
    if not keys or not all(isinstance(r, dict) and all(k in r for k in keys) for r in rows):
        return "\n".join(lines)
    counts = collections.Counter(tuple(str(r[k]) for k in keys) for r in rows)
    out: list[str] = []
    for line in lines:
        fields = dict(re.findall(r"(\w+)=([^,]*)", line)) if _ROW_LINE.match(line) else {}
        key = tuple(fields.get(k, "") for k in keys)
        tied = bool(fields) and counts.get(key, 0) > 1
        out.append("  · tie " + ", ".join(f"{k}={v}" for k, v in zip(keys, key, strict=True)) if tied else line)
    return "\n".join(out)


def _canonical(body: dict[str, Any]) -> bytes:
    mode = _mode(body)
    body = {k: v for k, v in body.items() if k not in VOLATILE_KEYS}
    if mode == "clock":
        body = {k: v for k, v in body.items() if k not in CLOCK_KEYS}
    elif mode == "exact" and isinstance(body.get("rows"), list):
        body["rows"] = _sort_ties(body["rows"], _order_keys(str(body.get("sql_used") or "")))
        body["answer"] = _tie_preview(body)
    elif mode == "unordered":
        body["rows"] = sorted(body["rows"], key=_row_key)
        body["answer"] = "\n".join(
            line for line in str(body.get("answer") or "").splitlines() if not _ROW_LINE.match(line)
        )
    body["_mode"] = mode
    return json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")


def _run(client) -> tuple[dict[str, list[str]], dict[str, str]]:
    from CortexOS.dms.answer_engine import clear_session

    client.bind_session("alpha", WAREHOUSE_GRANT)
    seen: dict[str, list[str]] = {}
    modes: dict[str, str] = {}
    for _ in (1, 2):
        for qid, question in _questions():
            clear_session(SESSION, space_id="alpha")
            resp = client.post(
                "/v1/contract/ask",
                json={"question": question, "session_id": SESSION, "space_id": "alpha"},
            )
            body = resp.json() if resp.status_code == 200 else {"_status": resp.status_code}
            canonical = _canonical(body)
            dump = os.environ.get("CORTEX_GOLDEN_DUMP")
            if dump:
                with open(dump, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"qid": qid, "raw": body, "canonical": canonical.decode()}) + "\n")
            seen.setdefault(qid, []).append(hashlib.sha256(canonical).hexdigest())
            modes[qid] = _mode(body)
    return seen, modes


def test_flag_unset_ask_is_byte_identical_to_main(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    for name in ("CORTEX_QUERY_SKILL", "DMS_QUERY_SKILL_CAPTURE", "DMS_L2_ENABLED", "CORTEX_SCORED_ROUND"):
        monkeypatch.delenv(name, raising=False)
    seen, modes = _run(ask_http)
    record = os.environ.get("CORTEX_RECORD_GOLDEN")
    if record:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True
        ).stdout.strip()
        tally = {m: sum(v == m for v in modes.values()) for m in sorted(set(modes.values()))}
        golden = {"recorded_on": head, "n": len(seen), "modes": tally, "sha256": seen}
        Path(record).write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.skip(f"recorded {len(seen)} questions on {head}: {tally}")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["n"] == len(seen) == len(_questions()) > 150
    changed = sorted(qid for qid in golden["sha256"] if golden["sha256"][qid] != seen.get(qid))
    assert changed == [], f"{len(changed)} answers differ from main {golden['recorded_on']}: {changed}"
