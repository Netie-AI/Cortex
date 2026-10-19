"""#412: a model chooses the metric, and a second model checks the meaning.

On main, "how many shipments are not delayed" took the L1 keyword cascade to
``delayed_count`` (1031). The warehouse has 10969 shipments that are not
delayed. These tests install a recorded port that picks ``delayed_count`` for
the seven phrasings and a check that disagrees. The HTTP answer reconfirms.
No answers "not found in the database". With the model step on, the phrasing
is handed to that step. Removing the check serves 1031.

The serve path has no token list and no synonym-equality gate.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest

from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401

SESSION = "exact-412"
GRANT = {
    "inventory": "TRUE",
    "suppliers": "TRUE",
    "locations": "TRUE",
    "shipments": "TRUE",
    "transactions": "TRUE",
    "alerts": "TRUE",
}
DELAYED_COUNT = 1031
NOT_DELAYED_COUNT = 10969
NEAR_MISSES = [
    "How many shipments are not delayed?",
    "What is the number of shipments that are not delayed?",
    "How many shipments were never delayed?",
    "How many shipments aren't delayed?",
    "How many shipments are without delayed status?",
    "How many shipments except delayed ones?",
    "How many shipments are undelayed?",
]
DECLARED = "how many delayed"
REMAIN = "Exclude delayed shipments and tell me how many remain"


@pytest.fixture(autouse=True)
def _recorded_metric_port() -> Any:
    """A bad chooser picks delayed_count. The check refuses the seven phrasings."""
    from CortexOS.dms.answer_engine import _normalize
    from CortexOS.dms.metric_choice import (
        MetricSelection,
        MetricVerdict,
        clear_metric_choice,
        register_metric_choice,
    )

    class _Port:
        def choose(self, question: str) -> MetricSelection:
            return MetricSelection(
                metric_id="delayed_count",
                closest_question=DECLARED,
                why="selected delayed_count",
            )

        def check(self, question: str, selection: MetricSelection) -> MetricVerdict:
            del selection
            if _normalize(question) == DECLARED:
                return MetricVerdict(agrees=True, unsure=False, why="")
            return MetricVerdict(
                agrees=False,
                unsure=False,
                why="the question does not mean delayed_count",
            )

    register_metric_choice(_Port())
    yield
    clear_metric_choice()


def _ask(client: Any, path: str, question: str) -> dict[str, Any]:
    client.bind_session(SESSION, GRANT)
    resp = client.post(path, json={"question": question, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def _badge(body: dict[str, Any]) -> str:
    raw = body.get("badge")
    if raw:
        return str(raw)
    prov = body.get("provenance") or {}
    if isinstance(prov, dict):
        return str(prov.get("badge") or "")
    return ""


def _assert_not_the_delayed_count(body: dict[str, Any]) -> None:
    assert _badge(body).lower() == "reconfirm", body
    assert body.get("rows") in ([], None), body.get("rows")
    assert not body.get("sql_used"), body.get("sql_used")
    assert body.get("metric_id") in (None, ""), body.get("metric_id")
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert "Closest question:" in text
    assert "Yes runs that question" in text
    assert "not found in the database" in text
    assert "1031" not in text and "1,031" not in text
    assert "10969" not in text and "10,969" not in text
    recon = body.get("reconfirm") or {}
    if recon:
        assert recon.get("closest_question")
        assert recon.get("why")


def test_warehouse_counts_are_the_ones_this_bug_mixes_up() -> None:
    """The rule's number and the complement, read from the demo warehouse."""
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms.warehouse_db import DEFAULT_DB

    _ensure_db_loaded()
    con = duckdb.connect(str(DEFAULT_DB), read_only=True)
    try:
        delayed = con.execute(
            "SELECT COUNT(*) FROM shipments WHERE status = 'DELAYED'"
        ).fetchone()
        other = con.execute(
            "SELECT COUNT(*) FROM shipments WHERE status <> 'DELAYED'"
        ).fetchone()
    finally:
        con.close()
    assert delayed is not None and int(delayed[0]) == DELAYED_COUNT
    assert other is not None and int(other[0]) == NOT_DELAYED_COUNT


@pytest.mark.parametrize("question", NEAR_MISSES)
def test_must_fail_near_miss_is_not_served_as_delayed_count(
    dms_http: Any, question: str  # noqa: F811
) -> None:
    for path in ("/dms/query", "/v1/contract/ask"):
        _assert_not_the_delayed_count(_ask(dms_http, path, question))


def test_agreed_metric_runs_governed_sql(dms_http: Any) -> None:  # noqa: F811
    """The recorded port agrees that the declared wording is delayed_count."""
    body = _ask(dms_http, "/dms/query", DECLARED)
    assert body.get("layer") == "governed_metric", body
    assert body.get("metric_id") == "delayed_count"
    rows = body.get("rows") or []
    assert rows and int(rows[0]["delayed_count"]) == DELAYED_COUNT
    assert "1031" in str(body.get("answer") or "").replace(",", "")


def test_must_fail_no_answers_not_found(dms_http: Any) -> None:  # noqa: F811
    dms_http.bind_session(SESSION, GRANT)
    resp = dms_http.post(
        "/v1/contract/ask",
        json={
            "question": NEAR_MISSES[0],
            "session_id": SESSION,
            "plan_sql_confirm": "no",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("answer") == "not found in the database"
    assert body.get("rows") in ([], None)
    assert not body.get("sql_used")
    assert "1031" not in str(body.get("answer"))


def _assert_not_a_delayed_listing(body: dict[str, Any]) -> None:
    """The remain question must not come back as delayed rows or 1031."""
    assert body.get("rows") in ([], None), body.get("rows")
    assert not body.get("sql_used"), body.get("sql_used")
    assert body.get("metric_id") in (None, ""), body.get("metric_id")
    text = str(body.get("answer") or "")
    low = text.lower()
    assert "delayed shipment row" not in low, text
    assert "sorted by days delayed" not in low, text
    assert "1031" not in text.replace(",", "")
    assert "1,031" not in text
    badge = _badge(body).lower()
    route = str(body.get("route") or "").lower()
    assert badge in {"abstain", "reconfirm"} or route in {
        "needs_clarification",
        "reconfirm",
        "abstain",
    }, body


def test_must_fail_exclude_delayed_remain_is_not_a_delayed_listing(
    dms_http: Any,  # noqa: F811
) -> None:
    """'Exclude delayed shipments and tell me how many remain' is not 100 delayed rows."""
    for path in ("/dms/query", "/v1/contract/ask"):
        _assert_not_a_delayed_listing(_ask(dms_http, path, REMAIN))


def test_serve_path_has_no_token_list_or_synonym_gate() -> None:
    """The customer serve path does not match synonyms or list negation words."""
    import ast
    from pathlib import Path

    engine = Path("CortexOS/dms/answer_engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine)
    answer = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "answer"
    )
    names = {node.id for node in ast.walk(answer) if isinstance(node, ast.Name)}
    banned = {
        "route_to_metric",
        "_exact_declared_plan",
        "_route_to_metric_cascade",
        "cascade_match_is_non_exact",
        "_declared_question_keys",
        "INVARIANT_TOKENS",
    }
    assert names.isdisjoint(banned), sorted(names & banned)
    choice = Path("CortexOS/dms/metric_choice.py").read_text(encoding="utf-8")
    decide = choice.split("def decide_metric", 1)[1].split("\ndef ", 1)[0]
    assert "INVARIANT_TOKENS" not in decide
    assert "frozenset" not in decide
    assert "_declared_question_keys" not in decide
    assert "_normalize" not in decide
    for word in ("aren't", "undelayed", "without", "except", "never"):
        assert word not in decide


_REGEX_ATTRS = frozenset(
    {"search", "match", "fullmatch", "findall", "finditer", "compile", "sub", "subn", "split"}
)
_LEGACY_SERVE = frozenset({"_try_generate_ranked_sql", "_answer_question_legacy"})


def _top_function(path: str, name: str) -> Any:
    import ast
    from pathlib import Path

    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _regex_patterns(fn: Any) -> list[str]:
    import ast

    found: list[str] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _REGEX_ATTRS or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append(arg.value)
    return found


def test_serve_path_has_no_lexical_regex() -> None:
    """No lexical regex chooses a serve in ``answer`` or ``answer_question``.

    ``POST /dms/query`` used to search ``\\b(delayed|late)\\b`` after the
    engine abstained and then call ``_try_generate_ranked_sql``. That served
    delayed rows for a complement. ``answer_question`` now has no regex and
    does not call the ranked generator. ``answer`` has no delayed or late
    pattern either.
    """
    import ast

    question_fn = _top_function("CortexOS/dms/query_service.py", "answer_question")
    assert _regex_patterns(question_fn) == []
    names = {node.id for node in ast.walk(question_fn) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(question_fn) if isinstance(node, ast.Attribute)}
    assert names.isdisjoint(_LEGACY_SERVE)
    assert attrs.isdisjoint(_LEGACY_SERVE)
    assert attrs.isdisjoint(_REGEX_ATTRS)

    answer_fn = _top_function("CortexOS/dms/answer_engine.py", "answer")
    answer_names = {node.id for node in ast.walk(answer_fn) if isinstance(node, ast.Name)}
    assert answer_names.isdisjoint(_LEGACY_SERVE)
    for pattern in _regex_patterns(answer_fn):
        low = pattern.lower()
        assert "delayed" not in low, pattern
        assert "late" not in low, pattern


class _RecordingPort:
    def __init__(self) -> None:
        self.questions: list[str] = []

    def is_configured(self) -> bool:
        return True

    def retrieve_schema(self, question: str) -> dict[str, Any]:
        del question
        return {"tables": {"shipments": {"columns": ["status"]}}}

    def generate_candidates(
        self,
        question: str,
        schema: dict[str, Any],
        *,
        prior_violations: list[str] | None = None,
    ) -> list[str]:
        del schema, prior_violations
        self.questions.append(question)
        return []

    def record_validated(self, question: str, sql: str) -> None:
        del question, sql
        raise AssertionError("a near-miss must not be recorded as validated")


def test_must_fail_near_miss_reaches_the_model_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check rejected the metric, so the question is handed to the SQL step.

    The SQL port returns nothing, so the envelope abstains. The wording the
    check accepted does not reach that port.
    """
    from bench.accuracy import _ensure_db_loaded
    from CortexOS.dms import l2_generation
    from CortexOS.dms.answer_engine import answer

    _ensure_db_loaded()
    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    port = _RecordingPort()
    monkeypatch.setattr(l2_generation, "resolve_l2_generation", lambda: port)

    near = answer(NEAR_MISSES[0])
    assert port.questions == [NEAR_MISSES[0]]
    assert near.get("badge") == "abstain", near
    assert near.get("rows") in ([], None)
    assert "1031" not in str(near.get("answer") or "")

    port.questions.clear()
    exact = answer(DECLARED)
    assert port.questions == []
    assert exact.get("layer") == "governed_metric"
    assert int(exact["rows"][0]["delayed_count"]) == DELAYED_COUNT
