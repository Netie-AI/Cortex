"""#426: no word rule decides a serve in ``answer``.

Three deciders were named. Each assertion is on the envelope a consumer gets
from ``POST /v1/contract/ask`` or ``POST /dms/query``: badge, layer, answer
text and rows.

1. ``is_catalog_intent``. On main a regex ran before the metric choice, so
   "show me the metrics for on-time delivery" came back as the catalog
   listing, and a catalog question in a wording the regex did not know never
   got the catalog. With ``CORTEX_CATALOG_BY_MODEL`` on, the model that
   chooses the metric may choose the catalog instead and a second call checks
   it. The regex is not consulted. With ``CORTEX_CATALOG_BY_MODEL=0`` main's behaviour stands.
2. ``query_skills.find``. With ``CORTEX_QUERY_SKILL`` on, a stored trigger that
   shared enough words with the question served its metric: after "how many
   delayed shipments" was stored, "how many not delayed shipments" served 1031
   under the ``query_skill`` badge. Only the same question replays a skill now.
3. ``_shape_refusal``. It is reached only from the keyword cascade, which
   ``answer`` no longer calls. It did not reproduce on main; the guard below
   keeps it off the serve path.

Env names and the ``catalog`` id are literals, so on a tree without the fix
these fail on their assertions, not on an import.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.dms.test_c7_02_manifest_before_explain import dms_http  # noqa: F401
from tests.dms.test_query_skill_gate_340 import ask_http, skills_db  # noqa: F401

CATALOG_FLAG = "CORTEX_CATALOG_BY_MODEL"
SKILL_FLAG = "CORTEX_QUERY_SKILL"
SESSION = "lexical-426"
SKILL_SESSION = "qs-340"  # the session ``ask_http`` binds
GRANT = {
    "inventory": "TRUE",
    "suppliers": "TRUE",
    "locations": "TRUE",
    "shipments": "TRUE",
    "transactions": "TRUE",
    "alerts": "TRUE",
}
PATHS = ("/dms/query", "/v1/contract/ask")
DELAYED_COUNT = 1031

# Metric questions the catalog regex matched on main.
ON_TIME = "show me the metrics for on-time delivery"
REVENUE_WITH_CATALOG_WORDS = "show me the metric total revenue"
SKU_METADATA = "how many skus have metadata"
# Catalog questions the regex did not match on main.
CATALOG_UNKNOWN_WORDING = "what kinds of things do you know about?"
CATALOG_MALAY = "apa yang boleh saya tanya?"
# A wording the regex does match.
CATALOG_REGEX_WORDING = "what can I ask"

CATALOG_HEAD = "Here is what you can ask from the DMS semantic layer:"


class _ScriptedPort:
    """Fixed replies in place of the model: one choice per question."""

    def __init__(self, choices: dict[str, dict[str, Any]], *, agrees: bool = True) -> None:
        self.choices = choices
        self.agrees = agrees
        self.chose: list[str] = []
        self.checked: list[tuple[str, str]] = []

    def choose(self, question: str) -> Any:
        from CortexOS.dms.metric_choice import MetricSelection

        self.chose.append(question)
        return MetricSelection(**self.choices.get(question, {"why": "no metric means this"}))

    def check(self, question: str, selection: Any) -> Any:
        from CortexOS.dms.metric_choice import MetricVerdict

        self.checked.append((question, selection.metric_id))
        return MetricVerdict(agrees=self.agrees, unsure=False, why="")


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> Any:
    from CortexOS.dms.metric_choice import clear_metric_choice, register_metric_choice

    monkeypatch.delenv(CATALOG_FLAG, raising=False)

    def _install(choices: dict[str, dict[str, Any]], *, agrees: bool = True) -> _ScriptedPort:
        port = _ScriptedPort(choices, agrees=agrees)
        register_metric_choice(port)
        return port

    yield _install
    clear_metric_choice()


def _ask(client: Any, path: str, question: str) -> dict[str, Any]:
    client.bind_session(SESSION, GRANT)
    resp = client.post(path, json={"question": question, "session_id": SESSION})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, dict)
    return body


def _field(body: dict[str, Any], name: str) -> str:
    raw = body.get(name)
    if raw:
        return str(raw)
    prov = body.get("provenance") or {}
    return str(prov.get(name) or "") if isinstance(prov, dict) else ""


def _assert_catalog(body: dict[str, Any]) -> None:
    # /dms/query shows the catalog badge; the contract envelope maps it to abstain.
    assert _field(body, "badge") in ("catalog", "abstain"), body
    assert _field(body, "badge") not in ("session", "certified", "governed_metric")
    assert _field(body, "layer") == "catalog", body
    assert body.get("rows") in ([], None)
    assert not body.get("sql_used")
    text = str(body.get("answer") or "")
    assert text.startswith(CATALOG_HEAD), text
    assert "**Certified questions**" in text and "**Tables covered:**" in text


def _assert_not_a_listing(body: dict[str, Any]) -> None:
    assert _field(body, "badge") != "catalog", body
    assert _field(body, "layer") != "catalog", body
    text = str(body.get("answer") or "")
    assert CATALOG_HEAD not in text, text
    assert "**Certified questions**" not in text, text


def _assert_reconfirm(body: dict[str, Any]) -> None:
    assert _field(body, "badge").lower() == "reconfirm", body
    assert body.get("rows") in ([], None)
    assert not body.get("sql_used")
    text = str(body.get("answer") or "")
    assert text.startswith("Reconfirm:"), text
    assert "not found in the database" in text


# ── 1. is_catalog_intent ─────────────────────────────────────────────────────
@pytest.mark.parametrize("path", PATHS)
def test_must_fail_metric_question_with_catalog_words_gets_its_number(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch, path: str  # noqa: F811
) -> None:
    """The model chose the metric and the check agreed. Main served a listing."""
    monkeypatch.setenv(CATALOG_FLAG, "1")
    port = scripted({REVENUE_WITH_CATALOG_WORDS: {"metric_id": "revenue_total"}})
    body = _ask(dms_http, path, REVENUE_WITH_CATALOG_WORDS)
    assert REVENUE_WITH_CATALOG_WORDS in port.chose
    _assert_not_a_listing(body)
    assert _field(body, "layer") == "governed_metric", body
    rows = body.get("rows") or []
    assert rows and float(rows[0]["revenue_myr"]) > 0, body
    assert any(ch.isdigit() for ch in str(body.get("answer") or "")), body


@pytest.mark.parametrize("question", [ON_TIME, SKU_METADATA])
@pytest.mark.parametrize("path", PATHS)
def test_must_fail_unanswerable_metric_question_reconfirms_not_a_listing(
    dms_http: Any,  # noqa: F811
    scripted: Any,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    question: str,
) -> None:
    """No metric means the question. It reconfirms; it is not a listing served as success."""
    monkeypatch.setenv(CATALOG_FLAG, "1")
    port = scripted({})
    body = _ask(dms_http, path, question)
    assert question in port.chose, "the question never reached the model"
    _assert_not_a_listing(body)
    _assert_reconfirm(body)


@pytest.mark.parametrize("question", [CATALOG_UNKNOWN_WORDING, CATALOG_MALAY])
@pytest.mark.parametrize("path", PATHS)
def test_must_fail_catalog_question_in_any_wording_gets_the_catalog(
    dms_http: Any,  # noqa: F811
    scripted: Any,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    question: str,
) -> None:
    """The model chose the catalog and the check agreed. No regex knew this wording."""
    monkeypatch.setenv(CATALOG_FLAG, "1")
    port = scripted({question: {"metric_id": "catalog"}})
    body = _ask(dms_http, path, question)
    _assert_catalog(body)
    assert (question, "catalog") in port.checked, "the catalog choice was not checked"


@pytest.mark.parametrize("path", PATHS)
def test_must_fail_regex_wording_is_not_a_listing_when_the_model_says_no(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch, path: str  # noqa: F811
) -> None:
    """Flag on, the regex does not get a vote, even on the wording it was written for."""
    monkeypatch.setenv(CATALOG_FLAG, "1")
    scripted({})
    body = _ask(dms_http, path, CATALOG_REGEX_WORDING)
    _assert_not_a_listing(body)
    _assert_reconfirm(body)


@pytest.mark.parametrize("path", PATHS)
def test_catalog_choice_the_check_rejects_is_not_a_listing(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch, path: str  # noqa: F811
) -> None:
    """A catalog choice the second call rejects falls through. It does not serve."""
    monkeypatch.setenv(CATALOG_FLAG, "1")
    port = scripted({ON_TIME: {"metric_id": "catalog"}}, agrees=False)
    body = _ask(dms_http, path, ON_TIME)
    assert (ON_TIME, "catalog") in port.checked
    _assert_not_a_listing(body)
    _assert_reconfirm(body)


def test_flag_on_the_catalog_regex_is_never_called(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(CATALOG_FLAG, "1")
    from packs.dms.semantic import catalog_answer

    class _Boom:
        def search(self, text: str) -> Any:
            raise AssertionError(f"a catalog pattern was read for {text!r}")

    # ``answer`` still calls ``is_catalog_intent`` (answer_engine.py is frozen,
    # #215); with the flag on it asks the model and reads no pattern.
    for name in ("_CAPABILITY", "_INVENTORY", "_AVAILABLE_IN_DATA", "_BROWSE", "_METADATA"):
        monkeypatch.setattr(catalog_answer, name, _Boom())
    scripted({CATALOG_REGEX_WORDING: {"metric_id": "catalog"}})
    for path in PATHS:
        _assert_catalog(_ask(dms_http, path, CATALOG_REGEX_WORDING))


@pytest.mark.parametrize("path", PATHS)
def test_flag_on_unarmed_says_so_and_serves_no_listing(
    dms_http: Any, monkeypatch: pytest.MonkeyPatch, path: str  # noqa: F811
) -> None:
    """No model, no choice. The answer says the chooser is not armed."""
    from CortexOS.dms.metric_choice import clear_metric_choice

    monkeypatch.setenv(CATALOG_FLAG, "1")
    clear_metric_choice()
    body = _ask(dms_http, path, CATALOG_REGEX_WORDING)
    _assert_not_a_listing(body)
    _assert_reconfirm(body)
    assert "not armed" in str(body.get("answer") or "")


@pytest.mark.parametrize("path", PATHS)
def test_flag_off_keeps_main_behaviour(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch, path: str  # noqa: F811
) -> None:
    """``CORTEX_CATALOG_BY_MODEL=0``: the listing main serves, and a model's catalog choice is ignored."""
    port = scripted({CATALOG_UNKNOWN_WORDING: {"metric_id": "catalog"}})
    monkeypatch.setenv(CATALOG_FLAG, "0")
    _assert_catalog(_ask(dms_http, path, CATALOG_REGEX_WORDING))
    assert CATALOG_REGEX_WORDING not in port.chose
    body = _ask(dms_http, path, CATALOG_UNKNOWN_WORDING)
    _assert_not_a_listing(body)
    _assert_reconfirm(body)


# The production adapter, with only FreeRoute scripted.
class _FakeFreeRoute:
    def __init__(self, choose: dict[str, Any], check: dict[str, Any]) -> None:
        self.choose, self.check = choose, check
        self.system: dict[str, str] = {}
        self.user: dict[str, str] = {}

    def complete(self, task: str, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        from CortexOS.dms.metric_choice import CHECK_TASK, CHOOSE_TASK
        from CortexOS.integrations.freeroute import Completion

        del kwargs
        self.system[task] = str(messages[0]["content"])
        self.user[task] = str(messages[-1]["content"])
        if task == CHOOSE_TASK:
            return Completion(ok=True, text=json.dumps(self.choose))
        if task == CHECK_TASK:
            return Completion(ok=True, text=json.dumps(self.check))
        return Completion(ok=False, reason=f"scripted refusal for {task}")


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> Any:
    from CortexOS.dms.metric_choice import clear_metric_choice, register_metric_choice
    from CortexOS.integrations import freeroute
    from packs.dms.metric_choice import OpenVaultMetricChoice

    monkeypatch.delenv(CATALOG_FLAG, raising=False)
    armed = freeroute.Arming(armed=True, reason="scripted", url="http://scripted")
    monkeypatch.setattr(freeroute, "arming", lambda *a, **k: armed)

    def _install(choose: dict[str, Any], check: dict[str, Any]) -> _FakeFreeRoute:
        fake = _FakeFreeRoute(choose, check)
        monkeypatch.setattr(freeroute, "complete", fake.complete)
        register_metric_choice(OpenVaultMetricChoice())
        return fake

    yield _install
    clear_metric_choice()


AGREES = {"agrees": True, "unsure": False}


def test_must_fail_adapter_serves_the_catalog_the_model_chose(
    dms_http: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from CortexOS.dms.metric_choice import CHECK_TASK, CHOOSE_TASK

    monkeypatch.setenv(CATALOG_FLAG, "1")
    fake = adapter({"metric_id": "catalog", "slots": {}, "why": "", "closest_question": ""}, AGREES)
    _assert_catalog(_ask(dms_http, "/v1/contract/ask", CATALOG_MALAY))
    assert "catalog" in fake.system[CHOOSE_TASK]
    # The check is asked the catalog question, not handed a metric definition.
    assert "sql:" not in fake.user[CHECK_TASK]
    assert CATALOG_MALAY in fake.user[CHECK_TASK]


@pytest.mark.parametrize(
    "check",
    [{"agrees": False, "unsure": False}, {"agrees": True, "unsure": True}, {"agrees": "true"}],
)
def test_adapter_catalog_needs_a_real_agreement(
    dms_http: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch, check: dict[str, Any]  # noqa: F811
) -> None:
    monkeypatch.setenv(CATALOG_FLAG, "1")
    adapter({"metric_id": "catalog", "slots": {}}, check)
    body = _ask(dms_http, "/v1/contract/ask", ON_TIME)
    _assert_not_a_listing(body)
    _assert_reconfirm(body)


def test_adapter_flag_off_prompt_is_mains_and_catalog_is_none(
    dms_http: Any, adapter: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Off, the model is sent main's prompt and a ``catalog`` reply is not a choice."""
    from CortexOS.dms.metric_choice import CHOOSE_INSTRUCTIONS, CHOOSE_TASK

    monkeypatch.setenv(CATALOG_FLAG, "0")
    fake = adapter({"metric_id": "catalog", "slots": {}}, AGREES)
    body = _ask(dms_http, "/v1/contract/ask", CATALOG_UNKNOWN_WORDING)
    assert fake.system[CHOOSE_TASK] == CHOOSE_INSTRUCTIONS
    _assert_not_a_listing(body)
    _assert_reconfirm(body)


# ── 2. query_skills.find ─────────────────────────────────────────────────────
STORED = "how many delayed shipments"
NEAR_MISSES = (
    "how many not delayed shipments",
    "how many delayed shipments excluded",
    "how many shipments delayed never",
    "how many delayed shipments not",
)


def _skill_ask(client: Any, question: str) -> dict[str, Any]:
    resp = client.post(
        "/v1/contract/ask",
        json={"question": question, "session_id": SKILL_SESSION, "space_id": "alpha"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _store_delayed_skill(client: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv(SKILL_FLAG, "1")
    port = scripted({STORED: {"metric_id": "delayed_count"}})
    client.bind_session("alpha", GRANT)
    served = _skill_ask(client, STORED)
    assert _field(served, "layer") == "governed_metric", served
    assert int(served["rows"][0]["delayed_count"]) == DELAYED_COUNT
    return port


@pytest.mark.parametrize("question", NEAR_MISSES)
def test_must_fail_stored_skill_is_not_served_for_a_different_question(
    ask_http: Any,  # noqa: F811
    skills_db: Path,  # noqa: F811
    scripted: Any,
    monkeypatch: pytest.MonkeyPatch,
    question: str,
) -> None:
    """The model chose no metric. Shared words must not serve the stored one."""
    port = _store_delayed_skill(ask_http, scripted, monkeypatch)
    body = _skill_ask(ask_http, question)
    assert question in port.chose
    assert _field(body, "layer") != "query_skill", body
    assert _field(body, "badge") != "query_skill", body
    assert body.get("rows") in ([], None), body.get("rows")
    text = str(body.get("answer") or "")
    assert "1031" not in text and "1,031" not in text, text
    _assert_reconfirm(body)


def test_same_question_still_replays_its_skill(
    ask_http: Any,  # noqa: F811
    skills_db: Path,  # noqa: F811
    scripted: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: the store is on and the same question (case and spacing aside) is served."""
    port = _store_delayed_skill(ask_http, scripted, monkeypatch)
    port.choices.clear()  # the model now chooses nothing; only the skill can answer
    body = _skill_ask(ask_http, "  How many DELAYED   shipments ")
    assert _field(body, "layer") == "query_skill", body
    assert int(body["rows"][0]["delayed_count"]) == DELAYED_COUNT
    assert "1031" in str(body.get("answer") or "").replace(",", "")


def test_find_scores_only_the_same_question(
    skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(SKILL_FLAG, "1")
    query_skills.capture(STORED, metric_id="delayed_count", space_id="alpha")
    hit = query_skills.find(STORED.upper(), space_id="alpha")
    assert hit is not None and hit["metric_id"] == "delayed_count" and hit["score"] == 1.0
    for question in NEAR_MISSES:
        assert query_skills.find(question, space_id="alpha") is None, question
        assert query_skills.find(question, space_id="alpha", threshold=0.0) is None, question


# ── 3. _shape_refusal and the cascade ────────────────────────────────────────
SHAPE_QUESTIONS = (
    "what is the total of the top 5 skus by sales value",
    "top 3 suppliers per country by spend",
)


@pytest.mark.parametrize("question", SHAPE_QUESTIONS)
def test_shape_and_cascade_rules_do_not_run_on_an_ask(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch, question: str  # noqa: F811
) -> None:
    """Passes on main: the cascade and its shape pre-gate are not on the serve path."""
    from CortexOS.dms import answer_engine as ae

    def _boom(name: str) -> Any:
        def _raise(*a: Any, **k: Any) -> Any:
            raise AssertionError(f"{name} ran on the serve path")

        return _raise

    for name in ("_shape_refusal", "_route_to_metric_cascade", "route_to_metric"):
        monkeypatch.setattr(ae, name, _boom(name))
    port = scripted({})
    for path in PATHS:
        body = _ask(dms_http, path, question)
        assert question in port.chose
        _assert_reconfirm(body)


def test_answer_names_no_lexical_decider() -> None:
    """``answer`` names no cascade or similarity decider.

    It still calls ``is_catalog_intent``: ``answer_engine.py`` is frozen (#215,
    the dual-write guard), so the catalog decision moved inside that function.
    With the flag on it reads no pattern
    (``test_flag_on_the_catalog_regex_is_never_called``).
    """
    import ast

    tree = ast.parse(Path("CortexOS/dms/answer_engine.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "answer")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    names |= {a.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) for a in n.names}
    banned = {
        "_shape_refusal",
        "_route_to_metric_cascade",
        "route_to_metric",
        "text_embedding",
        "cosine_similarity",
    }
    assert names.isdisjoint(banned), sorted(names & banned)
    find_src = Path("packs/dms/semantic/query_skills.py").read_text(encoding="utf-8")
    find_body = find_src.split("\ndef find(", 1)[1].split("\ndef ", 1)[0]
    for word in ("cosine", "jaccard", "embedding"):
        assert word not in find_body, word


# ── default on, one choice per ask ──────────────────────────────────────────


@pytest.mark.parametrize("path", PATHS)
def test_default_on_catalog_question_gets_the_catalog_without_the_flag(
    dms_http: Any, scripted: Any, path: str  # noqa: F811
) -> None:
    """No env set: the model decides. A wording the regex never knew gets the catalog."""
    scripted({CATALOG_UNKNOWN_WORDING: {"metric_id": "catalog"}})
    _assert_catalog(_ask(dms_http, path, CATALOG_UNKNOWN_WORDING))


@pytest.mark.parametrize("path", PATHS)
def test_default_on_metric_question_is_chosen_once_per_ask(
    dms_http: Any, scripted: Any, path: str  # noqa: F811
) -> None:
    """The catalog decision and the metric decision are one choose-and-check."""
    port = scripted({REVENUE_WITH_CATALOG_WORDS: {"metric_id": "total_revenue"}})
    body = _ask(dms_http, path, REVENUE_WITH_CATALOG_WORDS)
    _assert_not_a_listing(body)
    assert port.chose.count(REVENUE_WITH_CATALOG_WORDS) == 1, port.chose
    assert [q for q, _ in port.checked].count(REVENUE_WITH_CATALOG_WORDS) == 1, port.checked


def test_handoff_is_used_once_and_only_for_the_same_question_and_port(scripted: Any) -> None:
    from CortexOS.dms import metric_choice as mc

    port = scripted({"q one": {"metric_id": "total_revenue"}, "q two": {"metric_id": "total_revenue"}})
    assert mc.catalog_decision("q one") is False
    mc.decide_metric("q one")
    assert port.chose.count("q one") == 1
    mc.decide_metric("q one")  # read once: a second call asks again
    assert port.chose.count("q one") == 2
    assert mc.catalog_decision("q two") is False
    mc.decide_metric("q one")  # another question: not handed over, and cleared
    mc.decide_metric("q two")
    assert port.chose.count("q two") == 2
    assert mc.catalog_decision("q two") is False
    other = scripted({"q two": {"metric_id": "total_revenue"}})  # a new port
    mc.decide_metric("q two")
    assert other.chose == ["q two"]


def test_guard_handoff_removed_the_ask_chooses_twice(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Without the handoff the same ask pays for two rounds of model calls."""
    from CortexOS.dms import metric_choice as mc

    class _NoHandoff:
        def get(self) -> None:
            return None

        def set(self, value: Any) -> None:
            return None

    monkeypatch.setattr(mc, "_HANDOFF", _NoHandoff())
    port = scripted({REVENUE_WITH_CATALOG_WORDS: {"metric_id": "total_revenue"}})
    _ask(dms_http, "/v1/contract/ask", REVENUE_WITH_CATALOG_WORDS)
    assert port.chose.count(REVENUE_WITH_CATALOG_WORDS) == 2


# ── adversary round on d4d7af0 ───────────────────────────────────────────────

CERTIFIED_Q = "How many SKUs do we have in inventory?"  # cq_sku_count


@pytest.mark.parametrize("path", PATHS)
def test_must_fail_certified_question_is_not_put_to_the_model_first(
    dms_http: Any, scripted: Any, path: str  # noqa: F811
) -> None:
    """``answer()`` asks about the catalog before its certified match. A model
    that calls a certified question the catalog must not turn it into the listing."""
    port = scripted({CERTIFIED_Q: {"metric_id": "catalog"}})
    body = _ask(dms_http, path, CERTIFIED_Q)
    _assert_not_a_listing(body)
    assert _field(body, "layer") == "certified", body
    assert CERTIFIED_Q not in port.chose


def test_must_fail_a_failing_model_does_not_take_down_a_certified_serve(
    dms_http: Any, scripted: Any, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from CortexOS.dms import metric_choice as mc

    port = scripted({})

    def boom(question: str) -> Any:
        raise RuntimeError("OpenVault 429")

    monkeypatch.setattr(port, "choose", boom)
    for path in PATHS:
        assert _field(_ask(dms_http, path, CERTIFIED_Q), "layer") == "certified"
    # Not certified: no listing, nothing handed on; the error is the metric choice's.
    assert mc.catalog_decision("what can I ask") is False
    assert mc._HANDOFF.get() is None
    with pytest.raises(RuntimeError):
        mc.decide_metric("what can I ask")


def test_must_fail_catalog_decision_never_reads_a_held_decision(scripted: Any) -> None:
    """A long-lived context (a loop calling answer()) must not reuse the last ask's choice."""
    from CortexOS.dms import metric_choice as mc

    port = scripted({"q held": {"metric_id": "total_revenue"}})
    assert mc.catalog_decision("q held") is False  # held, then never consumed
    port.choices["q held"] = {"metric_id": "catalog"}
    assert mc.catalog_decision("q held") is True
    assert port.chose.count("q held") == 2


def test_adapter_catalog_choice_keeps_the_closest_question(adapter: Any) -> None:
    from CortexOS.dms.metric_choice import CATALOG_CHOICE, resolve_metric_choice

    adapter(
        {"metric_id": "catalog", "slots": {}, "closest_question": "How many SKUs do we have in inventory?"},
        {"agrees": False, "unsure": False},
    )
    selection = resolve_metric_choice().choose("list what you hold")
    assert selection.metric_id == CATALOG_CHOICE
    assert selection.closest_question == "How many SKUs do we have in inventory?"


@pytest.mark.parametrize(
    ("metric_id", "params"),
    [
        ("supplier_ranking", {"limit": 3, "direction": "ASC"}),
        ("stale_restock", {"days": 60}),
        ("free_capacity", {"limit": 1, "direction": "ASC"}),
        ("sales_by_value", {"limit": 5, "direction": "DESC", "offset_clause": 0,
                            "exclude_skus": ["SKU-ALPHA"]}),
    ],
    ids=["bottom-3", "60-days", "least-free", "exclusions"],
)
def test_must_fail_a_skill_whose_params_replay_drops_is_not_replayed(
    skills_db: Path, monkeypatch: pytest.MonkeyPatch, metric_id: str, params: dict[str, Any]  # noqa: F811
) -> None:
    """``answer()`` replays a skill with these params dropped: "bottom 3 suppliers"
    would serve the top 10. A replay that compiles different SQL is not a hit."""
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(SKILL_FLAG, "1")
    query_skills.capture("the stored question", metric_id=metric_id, params=params, space_id="alpha")
    assert query_skills.find("the stored question", space_id="alpha") is None


def test_must_fail_a_sales_rank_skill_is_not_replayed(
    skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    """Sales-rank slots are read again from the question by a word rule on replay:
    the model captured limit 3 and ascending, the replay would read limit 5."""
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(SKILL_FLAG, "1")
    query_skills.capture("the 3 least sold products", metric_id="sales_by_volume",
                         params={"limit": 3, "direction": "ASC", "offset_clause": 0},
                         space_id="alpha")
    assert query_skills.find("the 3 least sold products", space_id="alpha") is None


def test_must_pass_a_skill_whose_replay_compiles_the_same_query_is_replayed(
    skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    from packs.dms.semantic import query_skills

    monkeypatch.setenv(SKILL_FLAG, "1")
    query_skills.capture("top 5 products by sales volume", metric_id="sales_by_volume",
                         params={"limit": 5, "direction": "DESC", "offset_clause": 0},
                         space_id="alpha")
    hit = query_skills.find("top 5 products by sales volume", space_id="alpha")
    assert hit is not None and hit["metric_id"] == "sales_by_volume"
