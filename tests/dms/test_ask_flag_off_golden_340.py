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

The page tie (``apply_page_tiebreak``) appends the table's ``row_key`` when a
LIMIT drops rows. That suffix is removed before the digest, so a page whose
rows did not change stays on the recorded SQL. The raw ``sql_used`` still
carries the key; ``test_low_stock_must_fail_if_clock_or_tie_drift_returns``
locks it for the low-stock page.

Re-record (on the base tree, never on this branch):
``CORTEX_RECORD_GOLDEN=<path> pytest tests/dms/test_ask_flag_off_golden_340.py``
"""

from __future__ import annotations

import collections
import functools
import hashlib
import itertools
import json
import os
import re
import subprocess
from datetime import date, datetime
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
_DATE_LITERAL = re.compile(r"\d{4}-\d{2}-\d{2}")
_ORDER_BY = re.compile(r"\border\s+by\b", re.IGNORECASE)
_ROW_LINE = re.compile(r"^\s+·\s")
# Total order quantity_kg, sku, location_id, storage_bin. The digest ignores
# the appended row_key suffix; these bytes are the page itself. Rewriting the
# golden file does not silence the must-fail: the digests are copied below
# and the served SQL must still name the key.
_LOW_STOCK_QIDS = (
    "adversarial:low_stock_followup",
    "golden:low_stock_all",
    "paraphrase:low_stock_all#1",
    "paraphrase:low_stock_all#2",
)
_LOW_STOCK_RECORDED = {
    "adversarial:low_stock_followup": "91b8ac62159bd1a56c5b7825ec2e759d1314e7fcbfc5fb74e7ed011c8e7b96b0",
    "golden:low_stock_all": "91b8ac62159bd1a56c5b7825ec2e759d1314e7fcbfc5fb74e7ed011c8e7b96b0",
    "paraphrase:low_stock_all#1": "802482d2c774b1df9f8f5bb21c0fe54f24e84e9c96a06a59aa6511d0b03796ae",
    "paraphrase:low_stock_all#2": "802482d2c774b1df9f8f5bb21c0fe54f24e84e9c96a06a59aa6511d0b03796ae",
}
_LOW_STOCK_KEY = "i.sku ASC, i.location_id ASC, i.storage_bin ASC"
_CLOCK_DAYS = (date(2026, 10, 17), date(2026, 10, 22), date(2026, 10, 28))


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


@functools.cache
def _row_keys() -> dict[str, list[str]]:
    """Pack row keys. A trailing copy of one is the page tie the gate appends."""
    layer = yaml.safe_load((ROOT / "packs" / "dms" / "semantic_layer.yaml").read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    for name, spec in (layer.get("tables") or {}).items():
        key = [str(col) for col in (spec.get("row_key") or [])]
        if key:
            out[str(name)] = key
    return out


def _order_items(sql: str) -> tuple[re.Match[str] | None, list[str]]:
    match = re.search(r"\border\s+by\b(?P<body>.*?)(?:\blimit\b|\boffset\b|$)", sql, re.IGNORECASE | re.DOTALL)
    if match is None:
        return None, []
    items = [part.strip() for part in match.group("body").split(",") if part.strip()]
    return match, items


def _item_column(item: str) -> str:
    token = item.split()[0].strip('"')
    return token.split(".")[-1]


def _strip_page_tiebreak(sql: str) -> str:
    """Drop a trailing row-key suffix the gate appended after the question's own order.

    The suffix is not part of the order the question asked for. Digests of a
    page whose rows did not change stay on the recorded SQL. A page whose rows
    did change still mismatches. The raw ``sql_used`` is not rewritten.
    """
    match, items = _order_items(sql)
    if match is None or len(items) < 2:
        return sql
    names = [_item_column(item) for item in items]
    keys = sorted(_row_keys().values(), key=len, reverse=True)
    # A join appends one row_key per table. Peel each trailing key. A key that
    # is the whole ORDER BY stays: that is the question's own order.
    while len(names) >= 2:
        drop = 0
        for key in keys:
            if len(key) < len(names) and names[-len(key) :] == key:
                drop = len(key)
                break
        if not drop:
            break
        names = names[:-drop]
        items = items[:-drop]
    original = [_item_column(part.strip()) for part in match.group("body").split(",") if part.strip()]
    if names == original:
        return sql
    return sql[: match.start("body")] + " " + ", ".join(items) + " " + sql[match.end("body") :]


def _canonical(body: dict[str, Any]) -> bytes:
    mode = _mode(body)
    body = {k: v for k, v in body.items() if k not in VOLATILE_KEYS}
    if body.get("sql_used"):
        body["sql_used"] = _strip_page_tiebreak(str(body["sql_used"]))
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


def _run(client) -> tuple[dict[str, list[str]], dict[str, str], dict[str, dict[str, Any]]]:
    from CortexOS.dms.answer_engine import clear_session

    client.bind_session("alpha", WAREHOUSE_GRANT)
    seen: dict[str, list[str]] = {}
    modes: dict[str, str] = {}
    bodies: dict[str, dict[str, Any]] = {}
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
            bodies[qid] = body if isinstance(body, dict) else {"_status": body}
    return seen, modes, bodies


def test_flag_unset_ask_is_byte_identical_to_main(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    for name in ("CORTEX_QUERY_SKILL", "DMS_QUERY_SKILL_CAPTURE", "DMS_L2_ENABLED", "CORTEX_SCORED_ROUND"):
        monkeypatch.delenv(name, raising=False)
    from CortexOS.dms.answer_engine import match_certified

    seen, modes, bodies = _run(ask_http)
    # A question the metric check does not serve reconfirms. Certified
    # questions, and a metric the check accepts, stay byte-identical to the
    # recorded main golden.
    certified = {qid for qid, question in _questions() if match_certified(question) is not None}
    non_exact = set()
    for qid, body in bodies.items():
        badge = str((body.get("provenance") or {}).get("badge") or body.get("badge") or "")
        if badge == "reconfirm":
            assert qid not in certified, qid
            non_exact.add(qid)
    for qid in sorted(non_exact):
        body = bodies[qid]
        badge = str((body.get("provenance") or {}).get("badge") or body.get("badge") or "")
        assert badge == "reconfirm", (qid, body.get("answer"))
        assert body.get("rows") in ([], None)
        assert not body.get("sql_used")
        text = str(body.get("answer") or "")
        assert "Yes runs that question" in text
        assert "not found in the database" in text
        assert "1031" not in text
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
    changed = sorted(
        qid
        for qid in golden["sha256"]
        if qid not in non_exact and golden["sha256"][qid] != seen.get(qid)
    )
    assert changed == [], f"{len(changed)} answers differ from main {golden['recorded_on']}: {changed}"


def _pin_clock(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    """Point module-level ``date`` / ``datetime`` at one civil day.

    ``datetime.date`` itself is immutable, so this replaces the names the
    answer path imported. A later ``from datetime import date`` inside a
    function still sees the wall clock; the SQL assertion below catches that
    if it lands in ``sql_used``.
    """

    class _Date(date):
        @classmethod
        def today(cls) -> date:
            return day

    class _DateTime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(day.year, day.month, day.day, 12, 0, tzinfo=tz)

    for mod in (
        "CortexOS.dms.query_service",
        "CortexOS.dms.generate_sample",
        "CortexOS.dms.sql_guardrail",
    ):
        monkeypatch.setattr(f"{mod}.date", _Date, raising=False)
        monkeypatch.setattr(f"{mod}.datetime", _DateTime, raising=False)


def _serve_low_stock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A recorded metric port that chooses ``low_stock`` for the four pages.

    Since #412 a model chooses the metric (``dms-metric-choose``) and a second
    call checks it. The suite double serves only a declared wording, and none
    of these four is one, so with the double they reconfirm and no page is
    fetched. This port is not an armed measurement. It picks the metric main's
    cascade picked, with the same assumption text, so the page a customer
    receives when ``low_stock`` is served stays locked to the total order.
    """
    from CortexOS.dms import metric_choice
    from CortexOS.dms.metric_choice import MetricSelection, MetricVerdict

    asked = {question for qid, question in _questions() if qid in _LOW_STOCK_QIDS}

    class _LowStockChoice:
        def choose(self, question: str) -> MetricSelection:
            if question not in asked:
                return MetricSelection(why="not one of the four low-stock pages")
            return MetricSelection(metric_id="low_stock", why="items below reorder level")

        def check(self, question: str, selection: MetricSelection) -> MetricVerdict:
            agrees = question in asked and selection.metric_id == "low_stock"
            return MetricVerdict(agrees=agrees, unsure=not agrees, why="")

    monkeypatch.setattr(metric_choice, "_port", _LowStockChoice())


def _ask_low_stock(client) -> dict[str, dict[str, Any]]:
    from CortexOS.dms.answer_engine import clear_session

    questions = dict(_questions())
    client.bind_session("alpha", WAREHOUSE_GRANT)
    out: dict[str, dict[str, Any]] = {}
    for qid in _LOW_STOCK_QIDS:
        clear_session(SESSION, space_id="alpha")
        resp = client.post(
            "/v1/contract/ask",
            json={"question": questions[qid], "session_id": SESSION, "space_id": "alpha"},
        )
        if resp.status_code == 200:
            out[qid] = resp.json()
        else:
            out[qid] = {"_status": resp.status_code, "answer": resp.text, "sql_used": "", "rows": []}
    return out


def _clock_view(body: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    rows = body.get("rows") if isinstance(body.get("rows"), list) else []
    keys = _order_keys(str(body.get("sql_used") or "")) or ["quantity_kg"]
    ordered = _sort_ties(list(rows), keys)
    return (
        str(body.get("sql_used") or ""),
        str(body.get("answer") or ""),
        tuple(_row_key(row) for row in ordered),
    )


def test_low_stock_must_fail_if_clock_or_tie_drift_returns(
    ask_http, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """#434 must-fail.

    Goes red if a date returns to these four answers, if the page tie leaves
    the ORDER BY, or if the page leaves the total-order recording. Rewriting
    ``fixtures/ask_flag_off_golden_main.json`` does not silence it: the digests
    are copied here, the served SQL must contain no clock call and no date
    literal and must name the inventory lot key in the ORDER BY, and the answer
    text plus the row multiset must match on 2026-10-17, 2026-10-22 and
    2026-10-28. The metric is chosen by a recorded port (``_serve_low_stock``),
    so the page is fetched.
    """
    for name in ("CORTEX_QUERY_SKILL", "DMS_QUERY_SKILL_CAPTURE", "DMS_L2_ENABLED", "CORTEX_SCORED_ROUND"):
        monkeypatch.delenv(name, raising=False)
    _serve_low_stock(monkeypatch)
    views: dict[date, dict[str, tuple[str, str, tuple[str, ...]]]] = {}
    digests: dict[date, dict[str, str]] = {}
    for day in _CLOCK_DAYS:
        with monkeypatch.context() as pinned:
            _pin_clock(pinned, day)
            bodies = _ask_low_stock(ask_http)
        views[day] = {}
        digests[day] = {}
        for qid, body in bodies.items():
            sql = str(body.get("sql_used") or "")
            assert _CLOCK.search(sql) is None, f"{qid} on {day} date dependency returned in SQL: {sql}"
            assert _DATE_LITERAL.search(sql) is None, f"{qid} on {day} date literal in SQL: {sql}"
            assert _LOW_STOCK_KEY in sql, f"{qid} on {day} page tie left the ORDER BY: {sql}"
            views[day][qid] = _clock_view(body)
            digests[day][qid] = hashlib.sha256(_canonical(body)).hexdigest()
    first = _CLOCK_DAYS[0]
    for qid in _LOW_STOCK_QIDS:
        got = digests[first][qid]
        assert got == _LOW_STOCK_RECORDED[qid], (
            f"{qid} left the total-order low-stock page\n"
            f"sql: {views[first][qid][0]}\n"
            f"answer:\n{views[first][qid][1]}"
        )
        for day in _CLOCK_DAYS[1:]:
            assert views[day][qid] == views[first][qid], (
                f"{qid} date dependency returned between {first} and {day}\n"
                f"--- {first}\n{views[first][qid][1]}\n--- {day}\n{views[day][qid][1]}"
            )
