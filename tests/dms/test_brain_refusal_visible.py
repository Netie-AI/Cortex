"""REFUSAL-VISIBLE-01: demo UI shows model_route_unavailable.

The demo has no component runner. These tests load demo/dms-ui/lib/brain-refusal.js
(the module the brain and chat pages render through) with stubbed JSON.

(a) a stubbed refusal renders the named state and the success line is absent.
(b) a stubbed success still renders the success line.
The guard-removed test deletes the marked render block and asserts (a) fails.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "demo" / "dms-ui" / "lib" / "brain-refusal.js"
BRAIN_PAGE = ROOT / "demo" / "dms-ui" / "app" / "brain" / "page.jsx"
CHAT_PAGE = ROOT / "demo" / "dms-ui" / "app" / "chat" / "page.jsx"
UI_SUFFIXES = {".js", ".jsx", ".ts", ".tsx", ".html", ".vue"}
SKIP_PARTS = {"node_modules", ".next", "dist", "build"}
HEADLINE = "Model route unavailable: AI step skipped"
CODE = "model_route_unavailable"
EXPORT_FALLBACK = "Deterministic CSV kept; AI summary skipped."
GUARD_START = "// REFUSAL_VISIBLE_GUARD_START"
GUARD_END = "// REFUSAL_VISIBLE_GUARD_END"
MUST_FAIL = "tests/dms/test_brain_refusal_visible.py::test_must_fail_stubbed_refusal_renders_named_state"

SUCCESS_MARKERS = (
    "Chart generated:",
    "CSV ready:",
    "Email drafted",
    "WhatsApp message drafted",
    "Analysis complete for ",
    "Executive summary generated.",
    "Report organized.",
    "Chart ready",
    "CSV: ",
    "Message drafted.",
    "Executive summary ready.",
    "task suggestions.",
    "Analysis complete.",
)

_DRIVER = r"""
const { presentBrain, suggestionRefusalLine } = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]);
const out = cases.map((c) => {
  if (c.mode === "item") {
    const text = suggestionRefusalLine(c.item);
    return { id: c.id, text: text || "", refused: text != null, dataType: null };
  }
  const view = presentBrain({
    kind: c.kind,
    payload: c.payload,
    successText: c.successText,
  });
  return {
    id: c.id,
    text: view.text,
    refused: view.refused,
    dataType: view.dataType,
    code: view.code,
  };
});
process.stdout.write(JSON.stringify(out));
"""


def _module() -> Path:
    override = os.environ.get("BRAIN_REFUSAL_JS")
    if override:
        return Path(override)
    return MODULE


def _render(cases: list[dict]) -> list[dict]:
    proc = subprocess.run(
        ["node", "-e", _DRIVER, str(_module()), json.dumps(cases)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _refusal_body(**extra: object) -> dict:
    body = {
        "ok": False,
        "refusal": CODE,
        "llm_used": False,
        "served_provider": None,
        "served_model": None,
        "title": 'Chart generated: "Inventory"',
        "query": "show me a chart",
        "summary": "CSV ready: decoy.csv",
    }
    body.update(extra)
    return body


def _suggest_item(**extra: object) -> dict:
    item = {
        "task_id": "t1",
        "title": "Count stock",
        "description": "deterministic rank",
        "priority": "high",
        "confidence": 0.4,
        "refusal": CODE,
        "llm_used": False,
        "served_provider": None,
        "served_model": None,
    }
    item.update(extra)
    return item


def _refusal_cases() -> list[dict]:
    export_csv = _refusal_body(
        filename="dms_export.csv",
        csv_content="sku,qty\nA,1\n",
        row_count=1,
        columns=["sku", "qty"],
        summary="1 rows x 2 columns",
    )
    suggest = {"suggestions": [_suggest_item()]}
    cases = [
        ("chart-quick", "chart", 'Chart generated: "Inventory"', _refusal_body()),
        ("export-quick", "export", "CSV ready: dms_export.csv (1 rows). 1 rows x 2 columns", export_csv),
        ("email", "email", "Email drafted \u2014 review before sending.", _refusal_body()),
        ("whatsapp-quick", "whatsapp", "WhatsApp message drafted \u2014 review before sending.", _refusal_body()),
        ("analyze-quick", "analyze", "Analysis complete for last_7_days.", _refusal_body()),
        ("auto-quick", "auto-analysis", "Executive summary generated.", _refusal_body()),
        ("report", "report", "Report organized.", _refusal_body()),
        ("suggest", "suggest", "1 task suggestions.", suggest),
        ("chart-custom", "chart", "Chart ready", _refusal_body()),
        ("export-custom", "export", "CSV: dms_export.csv (1 rows)", export_csv),
        ("whatsapp-custom", "whatsapp", "Message drafted.", _refusal_body()),
        ("auto-custom", "auto-analysis", "Executive summary ready.", _refusal_body()),
        ("analyze-custom", "analyze", "Analysis complete.", _refusal_body()),
        ("llm-used-false", "chart", 'Chart generated: "Inventory"', {
            "llm_used": False,
            "served_provider": None,
            "served_model": None,
            "title": 'Chart generated: "Inventory"',
        }),
        ("refusal-wins", "email", "Email drafted \u2014 review before sending.", {
            "refusal": CODE,
            "llm_used": True,
            "title": "Email drafted",
        }),
    ]
    rendered = [
        {"id": name, "kind": kind, "successText": success, "payload": payload}
        for name, kind, success, payload in cases
    ]
    rendered.append({"id": "suggest-item", "mode": "item", "item": _suggest_item(), "successText": "Count stock"})
    return rendered


def _success_cases() -> list[dict]:
    served = {
        "llm_used": True,
        "served_provider": "openvault",
        "served_model": "stub",
        "title": HEADLINE,
        "query": CODE,
        "summary": HEADLINE,
    }
    export = {
        **served,
        "filename": "dms_export.csv",
        "csv_content": "sku,qty\nA,1\n",
        "row_count": 1,
        "summary": "1 rows x 2 columns",
    }
    suggest = {"suggestions": [{"task_id": "t1", "title": "Count stock", "description": "rule"}]}
    cases = [
        ("chart", "chart", 'Chart generated: "Inventory"', served, "chart"),
        ("export", "export", "CSV ready: dms_export.csv (1 rows). 1 rows x 2 columns", export, "csv"),
        ("email", "email", "Email drafted \u2014 review before sending.", served, "email"),
        ("whatsapp", "whatsapp", "Message drafted.", served, "whatsapp"),
        ("analyze", "analyze", "Analysis complete.", served, "analysis"),
        ("auto-analysis", "auto-analysis", "Executive summary generated.", served, "analysis"),
        ("report", "report", "Report organized.", served, "report"),
        ("suggest", "suggest", "1 task suggestions.", suggest, "suggestions"),
    ]
    rendered = [
        {
            "id": name,
            "kind": kind,
            "successText": success,
            "payload": payload,
            "dataType": data_type,
        }
        for name, kind, success, payload, data_type in cases
    ]
    rendered.append({
        "id": "suggest-item",
        "mode": "item",
        "item": {"task_id": "t1", "title": "Count stock"},
        "successText": "Count stock",
    })
    return rendered


def _assert_refusal(case: dict, row: dict) -> None:
    assert HEADLINE in row["text"], f"named refusal missing [{case['id']}]: {row['text']}"
    assert CODE in row["text"], row["text"]
    assert case["successText"] not in row["text"], f"success line still present [{case['id']}]: {row['text']}"
    assert row["refused"] is True
    if case.get("mode") == "item":
        return
    if case["kind"] == "export" and case["payload"].get("csv_content"):
        assert EXPORT_FALLBACK in row["text"], row["text"]
        assert row["dataType"] == "csv"
    elif case["kind"] == "suggest":
        assert row["dataType"] == "suggestions"
    elif case["kind"] == "export":
        assert row["dataType"] == "csv"
    else:
        assert row["dataType"] == "refusal"


def _ui_sources() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in ROOT.rglob("*"):
        if path.suffix not in UI_SUFFIXES or not path.is_file():
            continue
        if any(part in SKIP_PARTS for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if "/dms/brain/" in text or "fetchTaskSuggestions(" in text:
            found.append((path.relative_to(ROOT).as_posix(), text))
    return found


def _assert_pages_wire_the_renderer() -> None:
    page = BRAIN_PAGE.read_text(encoding="utf-8")
    chat = CHAT_PAGE.read_text(encoding="utf-8")
    assert 'from "../../lib/brain-refusal"' in page
    assert "presentBrain(" in page
    assert "suggestionRefusalLine(" in page
    assert "EXPORT_FALLBACK" in page
    assert 'data-testid="brain-export-fallback"' in page
    assert "brain-refusal" in page
    for marker in SUCCESS_MARKERS:
        hits = [line for line in page.splitlines() if marker in line]
        assert hits, marker
        for line in hits:
            assert "publish(" in line, line
    for line in page.splitlines():
        if "addMessage(" not in line:
            continue
        for marker in SUCCESS_MARKERS:
            assert marker not in line, line
    assert 'from "../../lib/brain-refusal"' in chat
    assert "suggestionRefusalLine(" in chat
    assert 'data-testid="brain-refusal"' in chat
    callers = {rel for rel, _text in _ui_sources()}
    assert callers == {
        "demo/dms-ui/app/brain/page.jsx",
        "demo/dms-ui/app/chat/page.jsx",
        "demo/dms-ui/lib/api.js",
    }
    wired = {
        "demo/dms-ui/app/brain/page.jsx": "presentBrain(",
        "demo/dms-ui/app/chat/page.jsx": "suggestionRefusalLine(",
    }
    for rel, text in _ui_sources():
        if rel in wired:
            assert wired[rel] in text


def test_must_fail_stubbed_refusal_renders_named_state() -> None:
    cases = _refusal_cases()
    rendered = {row["id"]: row for row in _render(cases)}
    assert set(rendered) == {case["id"] for case in cases}
    for case in cases:
        _assert_refusal(case, rendered[case["id"]])
    _assert_pages_wire_the_renderer()


def test_must_fail_stubbed_success_still_renders_success_line() -> None:
    cases = _success_cases()
    rendered = {row["id"]: row for row in _render(cases)}
    for case in cases:
        row = rendered[case["id"]]
        if case.get("mode") == "item":
            assert row["text"] == ""
            assert row["refused"] is False
            continue
        assert row["text"] == case["successText"], row
        assert row["refused"] is False
        assert row["dataType"] == case["dataType"]
        assert HEADLINE not in row["text"]
        assert EXPORT_FALLBACK not in row["text"]


def _pytest(node_ids: list[str], module: Path | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("BRAIN_REFUSAL_JS", None)
    if module is not None:
        env["BRAIN_REFUSAL_JS"] = str(module)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", *node_ids],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )


def _strip_guard(src: str) -> str:
    start = src.index(GUARD_START)
    end = src.index(GUARD_END)
    assert end > start
    stripped = src[:start] + src[end + len(GUARD_END) :]
    guard_fn = stripped.split("function applyRefusalGuard", 1)[1].split("function presentBrain", 1)[0]
    assert "namedRefusal(" not in guard_fn
    return stripped


def test_guard_removed_refusal_render_fails(tmp_path: Path) -> None:
    """(a) fails once the refusal render block is deleted, and passes with it present."""
    mutated = tmp_path / "brain-refusal-guard-off.js"
    mutated.write_text(_strip_guard(MODULE.read_text(encoding="utf-8")), encoding="utf-8")
    off = _pytest([MUST_FAIL], mutated)
    combined = off.stdout + off.stderr
    assert off.returncode != 0, combined
    assert "named refusal missing" in combined, combined
    on = _pytest([MUST_FAIL], None)
    assert on.returncode == 0, on.stdout + on.stderr


@pytest.mark.parametrize(
    "kind",
    ["chart", "export", "email", "whatsapp", "analyze", "auto-analysis", "report", "suggest"],
)
def test_callers_cover_each_brain_route(kind: str) -> None:
    page = BRAIN_PAGE.read_text(encoding="utf-8")
    assert f'publish("{kind}"' in page
