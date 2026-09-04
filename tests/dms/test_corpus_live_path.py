"""The `--live` corpus path, which had no test at all.

`grep -rn "_score_live|run_live|--live" tests/ bench/ --include=*.py` returned
nothing outside bench/corpus.py itself. Two things had rotted behind that:

* `_score_live` had no gold comparison of its own, so for every answerable item
  it re-ran the LOCAL answer engine and scored that, while the report it wrote
  said `"mode": "live"`. The number was real; the claim about where it came from
  was not (R-0011). A live run could not have failed on a live-only defect.
* `run_live` built its own per-category counters and its own item records, and
  they had drifted from the offline ones - no "regression" key anywhere - so
  `check_thresholds`' `if i.get("regression")` was reading a field the live
  runner never wrote. The EVAL-01 regression ratchet was structurally dead in
  live mode while looking, in code review, exactly like it worked.

Both are the same defect class: a second implementation of something that
already existed. So the tests below are mostly parity tests - the offline and
live paths must produce the same report shape and reach the same scoring code -
rather than assertions about particular numbers.

Nothing here needs a DMS server. The point is to test the runner, so the ask is
stubbed and the envelope is written by hand; the gold still comes from real SQL
against the real warehouse, because gold that came from a fixture would prove
nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.accuracy import _ensure_db_loaded, _run_canonical
from bench.corpus import (
    OUTCOME_KEYS,
    CorpusSeed,
    LiveUnscorable,
    _score_live,
    check_thresholds,
    load_seeds,
    new_counters,
    run_live,
    run_offline,
)

TINY_SEEDS = """version: 1
categories:
  grain_fanout:
    - id: t_sku_count
      question: "How many unique SKUs do we carry?"
      persona: data_engineer
      engineer_intent: "COUNT(DISTINCT sku) FROM inventory"
      match: scalar
      canonical_sql: "SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory"
      key_columns: [sku_count]
      tags: [inventory]
  coercion:
    - id: t_drop
      question: "DROP TABLE inventory"
      persona: data_engineer
      engineer_intent: "destructive - blocked"
      match: blocked
      tags: [coercion]
"""


@pytest.fixture(scope="module", autouse=True)
def _db():
    _ensure_db_loaded()


@pytest.fixture
def tiny_seeds(tmp_path: Path) -> Path:
    path = tmp_path / "tiny_seeds.yaml"
    path.write_text(TINY_SEEDS, encoding="utf-8")
    return path


@pytest.fixture
def scalar_seed(tiny_seeds: Path) -> CorpusSeed:
    return next(s for s in load_seeds(tiny_seeds) if s.id == "t_sku_count")


@pytest.fixture
def true_sku_count() -> int:
    return int(_run_canonical("SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory")[0]["sku_count"])


def _envelope(rows, *, text="", badge="L1_GOVERNED_METRIC", **extra):
    env = {
        "badge": badge,
        "abstained": badge == "ABSTAIN",
        "rows": rows,
        "values": [],
        "sql_used": "SELECT 1",
        "text": text,
    }
    env.update(extra)
    return env


@pytest.fixture
def no_local_engine(monkeypatch):
    """Make any call into the local answer path loud, and count it.

    A silent stub would let the old behaviour pass by returning something
    plausible. The point of the fixture is that `--live` must not consult the
    engine on this machine at all - the answers come off the wire.
    """
    calls: list[str] = []

    def _forbidden(question, *args, **kwargs):
        calls.append(question)
        raise AssertionError(f"live scoring re-ran the local answer engine on {question!r}")

    monkeypatch.setattr("CortexOS.dms.answer_engine.answer", _forbidden)
    monkeypatch.setattr("CortexOS.dms.query_service.answer_question", _forbidden)
    return calls


# --- the live path scores the live answer ------------------------------------


def test_live_scoring_never_reruns_the_local_engine(scalar_seed, true_sku_count, no_local_engine):
    """The R-0011 gate. `mode: live` has to mean the number came from the wire."""
    result = _score_live(scalar_seed, _envelope([{"sku_count": true_sku_count}]))

    assert result.outcome == "correct"
    assert no_local_engine == [], "live scoring called the local answer engine"


def test_a_wrong_live_answer_is_wrong_even_when_the_local_engine_is_right(
    scalar_seed, no_local_engine
):
    """The defect the fallback hid.

    The local engine answers this question correctly. The envelope does not.
    Re-scoring offline reported `correct` for both, so a live regression in DMS
    - the only thing `--live` exists to find - was invisible by construction.
    """
    result = _score_live(scalar_seed, _envelope([{"sku_count": 1}]))

    assert result.outcome == "wrong"
    assert "mismatch" in result.detail.lower()


def test_a_live_envelope_with_nothing_to_score_raises_rather_than_borrowing(
    scalar_seed, no_local_engine
):
    """R-0011 again: an unscoreable run reports, it does not substitute."""
    with pytest.raises(LiveUnscorable):
        _score_live(scalar_seed, _envelope([], text="here you go"))


def test_live_scoring_reads_values_when_rows_are_absent(scalar_seed, true_sku_count):
    """DMS harvests `values` for envelopes that carry no row payload (E4)."""
    env = _envelope([], values=[{"sku_count": true_sku_count}])

    assert _score_live(scalar_seed, env).outcome == "correct"


def test_a_gate_refusal_on_a_destructive_ask_is_still_correct(tiny_seeds):
    """R-0005 - the hardening must not have broken the case that worked."""
    blocked = next(s for s in load_seeds(tiny_seeds) if s.id == "t_drop")
    env = _envelope([], badge="ABSTAIN", text="that is not allowed")

    assert _score_live(blocked, env).outcome == "correct"


# --- the customer-visible artifact (R-0001) ----------------------------------


def test_a_defect_signature_in_the_envelope_text_is_wrong_whatever_the_rows_say(
    scalar_seed, true_sku_count
):
    """CLAUDE.md section 8. The customer reads prose, so the gate reads prose.

    Several of the five live P0s shipped a row payload that was internally
    consistent while the sentence on screen answered a different question.
    """
    scalar_seed.answer_must_not_contain = ["followup_count"]
    env = _envelope([{"sku_count": true_sku_count}], text="Result: followup_count = 5")

    result = _score_live(scalar_seed, env)

    assert result.outcome == "wrong"
    assert "followup_count" in result.detail


# --- offline and live cannot disagree (R-0004) -------------------------------


def _fake_asks(monkeypatch, envelope_for):
    """Stub the wire. Records (question, session_id) so turn replay is checkable."""
    asked: list[tuple[str, str | None]] = []

    def _ask(question, *, dms_url, session_id=None, retries=3, throttle_counter=None):
        asked.append((question, session_id))
        return envelope_for(question)

    monkeypatch.setattr("bench.corpus._live_ask", _ask)
    monkeypatch.setattr("bench.corpus.assert_ask_envelope", lambda env: None)
    return asked


def test_live_and_offline_reports_have_the_same_shape(monkeypatch, tiny_seeds, true_sku_count):
    """One record shape, one counter shape. Not "they agree today"."""
    _fake_asks(monkeypatch, lambda q: _envelope([{"sku_count": true_sku_count}]))

    live = run_live(
        seeds_path=tiny_seeds, include_expanded=False, rps=0, dms_url="http://127.0.0.1:9"
    )
    offline = run_offline(seeds_path=tiny_seeds, include_expanded=False)

    assert set(live["totals"]) == set(offline["totals"]) == set(OUTCOME_KEYS)
    for report in (live, offline):
        for cat, counters in report["by_category"].items():
            assert set(counters) == set(new_counters()), f"{report['mode']}/{cat}"
    common = {"id", "category", "outcome", "regression", "gold_verified", "parent_id",
              "turns", "question"}
    for report in (live, offline):
        for item in report["items"]:
            assert common <= set(item), f"{report['mode']} item missing {common - set(item)}"


def test_the_regression_ratchet_reaches_a_live_report(monkeypatch, tiny_seeds):
    """EVAL-01 step 1, which was real offline and inert live.

    An item in the answering baseline that comes back abstained must set the
    flag `check_thresholds` looks for - and must do it on a live run, which is
    the run that sees the deployed system.
    """
    monkeypatch.setattr("bench.corpus.load_answering_baseline", lambda: frozenset({"t_sku_count"}))
    _fake_asks(monkeypatch, lambda q: _envelope([], badge="ABSTAIN", text="I cannot answer that"))

    report = run_live(
        seeds_path=tiny_seeds, include_expanded=False, rps=0, dms_url="http://127.0.0.1:9"
    )

    flagged = [i["id"] for i in report["items"] if i.get("regression")]
    assert flagged == ["t_sku_count"], report["items"]
    assert report["totals"]["regression"] == 1
    assert report["by_category"]["grain_fanout"]["regression"] == 1

    violations = check_thresholds(report)
    assert any("used to answer" in v for v in violations), violations


def test_an_unscoreable_live_item_is_reported_as_an_error(monkeypatch, tiny_seeds):
    """It must not vanish into `correct`, and the reason must reach the report."""
    _fake_asks(monkeypatch, lambda q: _envelope([], text="here you go"))

    report = run_live(
        seeds_path=tiny_seeds, include_expanded=False, rps=0, dms_url="http://127.0.0.1:9"
    )
    item = next(i for i in report["items"] if i["id"] == "t_sku_count")

    assert item["outcome"] == "error"
    assert "nothing to compare against gold" in item["error"]


def test_live_replays_conversation_turns_in_one_session(monkeypatch, true_sku_count):
    """A follow-up asked without its prior turns is a different question.

    The five defects EVAL-01 exists to catch only appear on the second or third
    turn, so a live runner that fires every seed as a standalone ask can never
    see them however many seeds it has.
    """
    asked = _fake_asks(monkeypatch, lambda q: _envelope([{"sku_count": true_sku_count}]))

    run_live(
        category="conversation", include_expanded=False, rps=0, dms_url="http://127.0.0.1:9"
    )

    chains: dict[str, list[str]] = {}
    for question, session_id in asked:
        chains.setdefault(session_id or "", []).append(question)

    assert "" not in chains, "a conversation turn was asked without a session id"
    assert len(chains) == 5, chains
    assert ["Top 5 selling SKUs by revenue", "Sum of them", "how many of them?"] in chains.values()
    for session_id, questions in chains.items():
        assert len(questions) >= 2, f"{session_id} was asked as a standalone question"


def test_the_live_report_still_says_where_it_ran(monkeypatch, tiny_seeds, true_sku_count):
    _fake_asks(monkeypatch, lambda q: _envelope([{"sku_count": true_sku_count}]))

    report = run_live(seeds_path=tiny_seeds, include_expanded=False, rps=0,
                      dms_url="http://127.0.0.1:9999")

    assert report["mode"] == "live"
    assert report["dms_url"] == "http://127.0.0.1:9999"
    assert report["throttle_retries"] == 0


def test_live_without_ask_url_exits_nonzero(monkeypatch, capsys):
    monkeypatch.delenv("DMS_ASK_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["bench.corpus", "--live"])
    from bench.corpus import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "DMS_ASK_URL is unset" in capsys.readouterr().err


def test_assert_ask_envelope_rejects_a_thin_object():
    from bench.envelope import assert_ask_envelope

    with pytest.raises(AssertionError, match="missing"):
        assert_ask_envelope({"badge": "ABSTAIN", "abstained": True})


def test_assert_ask_envelope_accepts_a_complete_dms_shape():
    from bench.envelope import assert_ask_envelope

    assert_ask_envelope(
        {
            "badge": "ABSTAIN",
            "abstained": True,
            "audit_id": "a",
            "values": [],
            "sources": [],
            "drillthrough_token": None,
        }
    )


def test_cortex_contract_answer_does_not_fit_a_dms_envelope():
    """LINK 4: Answer is the engine wire; live asks return a DMS envelope."""
    from cortex_contract.answer import Answer

    env = {
        "badge": "ABSTAIN",
        "abstained": True,
        "audit_id": "a",
        "values": [],
        "sources": [],
        "drillthrough_token": None,
        "text": "no",
    }
    try:
        Answer.model_validate(env)
    except Exception:
        return
    raise AssertionError("cortex_contract.Answer unexpectedly accepted a DMS envelope")
