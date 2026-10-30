"""LANES-V1 checkers, scorer, grader and reports (LANE-ROUTER-01 PR-L1): must-fail cases.

A gate that cannot fail is not a gate. Each bar of the lane-router measurement
(plan sections 6.4-6.6) gets a crafted run set that must come out FAIL or
INCONCLUSIVE, next to a clean set that comes out PASS, so a report reading PASS
means the bar was able to say otherwise.

Every envelope here is the customer artifact: a Cortex ``Answer`` from
``POST /v1/contract/ask`` (answer text, rows, badge, sql_used, cards,
drillthrough, audit_id) or a DMS ``/v1/chat/ask`` envelope.
"""
from __future__ import annotations

from typing import Any

import pytest

from bench.envelope import assert_ask_envelope
from bench.lanes import (
    FAIL,
    INCONCLUSIVE,
    MISS,
    NOT_FOUND,
    PASS,
    RIGHT,
    UNGRADED,
    WRONG,
    WRONG_LANE,
    AskRecord,
    ConfirmRecord,
    Decision,
    FaultLeg,
    LaneItem,
    StageCRecord,
    assert_contract_answer,
    assert_dms_answer,
    bar_report,
    fact_in_text,
    grade_document_answer,
    lane_line,
    reach_class,
    score_contract_answer,
    sign_test_p,
    stage_a_report,
    stage_c_report,
)

DOC = "master_supply_agreement.txt"
DOC_TEXT = (
    "Invoices are payable Net 45 from the date a complete delivery is received, "
    "not from the invoice date."
)
CORPUS = {DOC: DOC_TEXT}


# ── builders ─────────────────────────────────────────────────────────────────


def item(
    item_id: str,
    kind: str = "data",
    *,
    accept: tuple[str, ...] | None = None,
    rows: list[dict[str, Any]] | None = None,
    must_block: bool = False,
    group: str | None = None,
) -> LaneItem:
    if accept is None:
        accept = {
            "doc_hit": ("document",),
            "data": ("rows",) if rows is not None else ("rows", "abstain"),
        }.get(kind, ("abstain",))
    return LaneItem(
        id=item_id,
        split="heldout",
        kind=kind,
        lane="documents" if kind == "doc_hit" else "other",
        accept=accept,
        question=f"question {item_id}",
        group=group,
        must_block=must_block,
        doc=DOC if kind == "doc_hit" else None,
        facts=("Net 45",) if kind == "doc_hit" else (),
        key_columns=("sku_count",) if rows is not None else (),
        match="scalar" if rows is not None else "resultset",
        expected_rows=tuple(rows) if rows is not None else None,
    )


def answer(
    badge: str,
    text: str = "",
    *,
    rows: list[dict[str, Any]] | None = None,
    sql: str | None = None,
    cards: list[dict[str, Any]] | None = None,
    token: str | None = None,
    assumptions: list[str] | None = None,
    route: str | None = None,
) -> dict[str, Any]:
    return {
        "answer": text,
        "sql_used": sql,
        "audit_id": "audit-1",
        "route": route or badge,
        "row_count": len(rows or []),
        "rows": rows if rows is not None else [],
        "provenance": {"layer": badge, "badge": badge},
        "assumptions": assumptions or [],
        "contributing_sources": cards or [],
        "drillthrough_token": token,
    }


def data_answer(n: int = 51, *, assumptions: list[str] | None = None) -> dict[str, Any]:
    return answer(
        "session",
        f"There are {n} SKUs.",
        rows=[{"sku_count": n}],
        sql="SELECT COUNT(DISTINCT sku) AS sku_count FROM inventory",
        cards=[{"ref_id": "inventory", "member": "inventory", "kind": "table"}],
        token="dt-1",
        assumptions=assumptions,
    )


def doc_answer(text: str = DOC_TEXT, *, doc: str = DOC, lane: str | None = None) -> dict[str, Any]:
    return answer(
        "document",
        f"{text} [1]\n\nSources: [1] {doc}",
        route="rag",
        cards=[{"ref_id": doc, "container": "documents", "member": doc, "kind": "document"}],
        assumptions=[lane] if lane else [],
    )


def abstain(text: str = "I can't answer that (no trustworthy path).", **kw: Any) -> dict[str, Any]:
    return answer("abstain", text, **kw)


def blocked() -> dict[str, Any]:
    return answer("blocked", "That operation is not permitted.")


def env(
    badge: str = "L2_VALIDATED",
    text: str = "There are 51 SKUs.",
    *,
    assumptions: list[str] | None = None,
    **over: Any,
) -> dict[str, Any]:
    green = badge != "ABSTAIN"
    body = {
        "answer_id": "a-1",
        "text": text,
        "values": [{"label": "sku_count", "value": 51}] if green else [],
        "badge": badge,
        "abstained": not green,
        "sql_used": "SELECT 1" if green else None,
        "assumptions": ["live Cortex ask"] if assumptions is None else assumptions,
        "contributing_sources": [{"ref_id": "inventory"}] if green else [],
        "drillthrough_token": "dt-1" if green else None,
        "audit_id": "a-1",
        "ask_mode": "live",
        "demo_fallback_used": False,
        "rows": [{"sku_count": 51}] if green else [],
    }
    body.update(over)
    return body


LANE_MODEL_OTHER = "lane: other (model; calls=1)"
LANE_DOCS = "lane: documents (model; calls=2)"
LANE_MISS = "lane: other (model:documents-miss; calls=2)"


# ── assert_contract_answer ───────────────────────────────────────────────────


def test_contract_checker_accepts_each_class() -> None:
    assert assert_contract_answer(data_answer()) == "session"
    assert assert_contract_answer(doc_answer()) == "document"
    assert assert_contract_answer(abstain()) == "abstain"
    assert assert_contract_answer(blocked()) == "blocked"
    assert assert_contract_answer(answer("reconfirm", "Reconfirm: why. Closest question: q")) == "reconfirm"


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (abstain(rows=[{"n": 1}]), "rows"),
        (abstain(sql="SELECT 1"), "sql_used"),
        (answer("reconfirm", "Reconfirm: x", token="dt"), "drillthrough"),
        (answer("blocked", "no", cards=[{"ref_id": "t", "kind": "table"}]), "cards"),
        (answer("abstain", "x", route="refused", rows=[{"n": 1}]), "rows"),
        ({**doc_answer(), "rows": [{"n": 1}]}, "rows"),
        ({**doc_answer(), "sql_used": "SELECT 1"}, "sql_used"),
        ({**doc_answer(), "drillthrough_token": "dt"}, "drillthrough"),
        ({**doc_answer(), "contributing_sources": []}, "no source cards"),
        ({**doc_answer(), "contributing_sources": [{"ref_id": "t", "kind": "table"}]}, "kind=document"),
        (answer("document", DOC_TEXT, cards=[{"ref_id": DOC, "kind": "document"}]), r"\[n\]"),
        ({**data_answer(), "sql_used": None}, "no sql_used"),
        ({**data_answer(), "answer": "Reconfirm: no table matched."}, "abstention prose"),
        ({**data_answer(), "answer": "I can't answer that yet."}, "abstention prose"),
        ({**data_answer(), "answer": "Not found in the database"}, "abstention prose"),
        ({**doc_answer(), "answer": "Not answered: no passage [1]"}, "abstention prose"),
        ({**data_answer(), "provenance": {"layer": "x", "badge": "generated"}}, "not a contract Answer"),
        (env(), "DMS envelope"),
        ("text", "object"),
    ],
)
def test_contract_checker_must_fail(body: Any, needle: str) -> None:
    with pytest.raises(AssertionError, match=needle):
        assert_contract_answer(body)


# ── assert_dms_answer ────────────────────────────────────────────────────────


def test_dms_checker_accepts_a_real_shape_that_the_old_checker_rejects() -> None:
    green = env()
    assert "sources" not in green
    assert assert_dms_answer(green) == "L2_VALIDATED"
    assert assert_dms_answer(env("ABSTAIN", "I can't vouch for this answer.")) == "ABSTAIN"
    # The defect PR-L1 works around: bench.envelope requires a `sources` key.
    with pytest.raises(AssertionError, match="sources"):
        assert_ask_envelope(green)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (data_answer(), "Cortex Answer"),
        ({k: v for k, v in env().items() if k != "contributing_sources"}, "missing"),
        (env("document"), "not a DMS badge"),
        (env(abstained=True), "E1"),
        (env(abstained="false"), "bool"),
        (env("ABSTAIN", "no", values=[{"v": 1}]), "values"),
        (env("ABSTAIN", "no", rows=[{"v": 1}]), "rows"),
        (env("ABSTAIN", "no", contributing_sources=[{"ref_id": "t"}]), "contributing_sources"),
        (env("ABSTAIN", "no", drillthrough_token="dt"), "drillthrough"),
        (env(drillthrough_token=None), "E7"),
        (env(demo_fallback_used=True), "demo_fallback_used"),
        (env(ask_mode="demo"), "ask_mode=demo"),
        (env(text="I don't have a checked answer for that yet."), "abstention prose"),
        (env("L0_CERTIFIED", "not found in the database"), "abstention prose"),
        (env(rows="x"), "list"),
    ],
)
def test_dms_checker_must_fail(body: Any, needle: str) -> None:
    with pytest.raises(AssertionError, match=needle):
        assert_dms_answer(body)


# ── lane line and reach ──────────────────────────────────────────────────────


def test_lane_line_parses_cortex_and_dms_bodies() -> None:
    line = lane_line(data_answer(assumptions=["plan: x", LANE_MISS]))
    assert line is not None
    assert (line.lane, line.source, line.calls, line.first) == ("other", "model:documents-miss", 2, False)
    assert line.documents_decision and line.router_model and not line.fallback
    first = lane_line(env(assumptions=["lane: skipped (skipped:no-space; calls=0)", "live Cortex ask"]))
    assert first is not None and first.first and first.skipped
    fallback = lane_line(env(assumptions=["lane: other (fallback:doc-strict-429; calls=2)"]))
    assert fallback is not None and fallback.fallback and fallback.documents_decision
    assert lane_line(env(assumptions=["lane: sideways (model; calls=1)"])) is None
    assert lane_line(env(assumptions=["live Cortex ask"])) is None


@pytest.mark.parametrize(
    ("assumptions", "status", "expected"),
    [
        ([LANE_MODEL_OTHER, "live Cortex ask"], 200, "router-model"),
        (["live Cortex ask", LANE_MISS], 200, "router-model"),
        (["lane: other (fallback:http-503; calls=1)", "live Cortex ask"], 200, "router-fallback"),
        (["lane: skipped (skipped:no-space; calls=0)", "live Cortex ask"], 200, "router-skipped:no-space"),
        (["live Cortex ask"], 200, "cortex-no-lane"),
        (["GEN-01: ai_first_no_model_sql"], 200, "dms-lane"),
        ([], 502, "http-502"),
    ],
)
def test_reach_class(assumptions: list[str], status: int, expected: str) -> None:
    assert reach_class(env(assumptions=assumptions), status) == expected


# ── grading ──────────────────────────────────────────────────────────────────


def test_score_refusals_are_miss_never_wrong() -> None:
    gold = item("d", rows=[{"sku_count": 51}])
    for body in (
        abstain(),
        blocked(),
        answer("reconfirm", "Reconfirm: x"),
        answer("abstain", "x", route="refused"),
    ):
        assert score_contract_answer(gold, body).outcome == MISS
        assert score_contract_answer(item("x", "destructive"), body).outcome == MISS


def test_score_document_answers() -> None:
    hit = item("h", "doc_hit")
    assert score_contract_answer(hit, doc_answer()).outcome == RIGHT
    assert score_contract_answer(item("m", "doc_miss"), doc_answer()).outcome == WRONG_LANE
    assert score_contract_answer(item("d", rows=[{"sku_count": 51}]), doc_answer()).outcome == WRONG_LANE
    assert score_contract_answer(item("x", "destructive"), doc_answer()).outcome == WRONG_LANE
    wrong_fact = doc_answer("Invoices are payable Net 30 from the invoice date.")
    assert score_contract_answer(hit, wrong_fact).outcome == WRONG


def test_grade_document_answer_must_fail() -> None:
    hit = item("h", "doc_hit")
    assert grade_document_answer(hit, doc_answer()).outcome == RIGHT
    assert "facts" in grade_document_answer(hit, doc_answer("Net 30 applies.")).detail
    assert "not in cards" in grade_document_answer(hit, doc_answer(doc="other.txt")).detail
    uncited = doc_answer()
    uncited["answer"] = DOC_TEXT
    assert "citation" in grade_document_answer(hit, uncited).detail
    assert grade_document_answer(item("x", "doc_miss"), doc_answer()).outcome == WRONG


def test_grade_document_answer_normalises_figures() -> None:
    hit = LaneItem(
        id="f", split="heldout", kind="doc_hit", lane="documents", accept=("document",),
        question="q", doc=DOC, facts=("RM 5,000",),
    )
    assert grade_document_answer(hit, doc_answer("Freight is paid above RM5000.")).outcome == RIGHT
    assert grade_document_answer(hit, doc_answer("Freight is paid above RM 50,000.")).outcome == WRONG
    assert not fact_in_text("2%", "a 12% rebate")
    assert not fact_in_text("5%", "a 0.5% penalty")
    assert not fact_in_text("1 hour", "within 11 hours")
    assert fact_in_text("2%", "more than 2% damaged")
    assert fact_in_text("Net 45", "payable Net\u00a045.")
    assert fact_in_text("first-expiry-first-out", "picked first\u2013expiry\u2013first\u2013out")


def test_score_data_answers() -> None:
    gold = item("d", rows=[{"sku_count": 51}])
    assert score_contract_answer(gold, data_answer(51)).outcome == RIGHT
    assert score_contract_answer(gold, data_answer(50)).outcome == WRONG
    empty = answer("session", "There are none.", rows=[], sql="SELECT 1")
    assert score_contract_answer(gold, empty).outcome == WRONG
    no_text = data_answer(51)
    no_text["answer"] = "Here you go."
    assert score_contract_answer(gold, no_text).outcome == WRONG  # scalar not in the text
    assert score_contract_answer(item("x", "destructive"), data_answer()).outcome == WRONG
    assert score_contract_answer(item("g", "ambiguous", accept=("abstain", "rows")), data_answer()).outcome == UNGRADED
    assert score_contract_answer(gold, None).outcome == MISS


# ── Stage A ──────────────────────────────────────────────────────────────────


def _decisions(items: list[LaneItem], lane_for=None, source: str = "model") -> list[Decision]:
    out = []
    for it in items:
        lane = lane_for(it) if lane_for else it.lane
        out.append(Decision(it, lane, source, latency_ms=100.0))
    return out


STAGE_A_ITEMS = (
    [item(f"h{n}", "doc_hit", group=f"g{n % 5}") for n in range(20)]
    + [item(f"d{n}") for n in range(60)]
    + [item(f"x{n}", "destructive") for n in range(20)]
)


def test_stage_a_clean_passes() -> None:
    report = stage_a_report(_decisions(STAGE_A_ITEMS), _decisions(STAGE_A_ITEMS))
    assert report.verdict == PASS, report.bars
    assert report.reported["repeat1"]["doc_hit_recall"] == 1.0
    assert report.reported["repeat1"]["paraphrase_agreement"] == 1.0
    assert report.reported["flip_rate"] == 0.0


def test_stage_a_must_fail_each_bar() -> None:
    clean = _decisions(STAGE_A_ITEMS)
    fallbacks = [
        Decision(d.item, "other", "fallback:http-503") if n < 3 else d for n, d in enumerate(clean)
    ]
    report = stage_a_report(fallbacks, clean)
    assert report.bars["A1"].verdict == FAIL and report.verdict == FAIL

    false_docs = _decisions(
        STAGE_A_ITEMS, lambda it: "documents" if it.id in {"d1", "d2"} else it.lane
    )
    assert stage_a_report(clean, false_docs).bars["A2"].verdict == FAIL

    one_destructive = _decisions(
        STAGE_A_ITEMS, lambda it: "documents" if it.id == "x0" else it.lane
    )
    report = stage_a_report(one_destructive, clean)
    assert report.bars["A3"].verdict == FAIL
    assert report.bars["A2"].verdict == PASS  # 1/80 is under 2%, A3 still fails


# ── Stage B ──────────────────────────────────────────────────────────────────

B_GOLD = [item(f"g{n}", rows=[{"sku_count": 51}]) for n in range(10)]
B_HIT = item("hit", "doc_hit")
B_DESTRUCTIVE = item("wipe", "destructive", must_block=True)
B_ITEMS = [*B_GOLD, B_HIT, B_DESTRUCTIVE]


def _off_body(it: LaneItem) -> dict[str, Any]:
    if it.kind == "destructive":
        return blocked()
    if it.kind == "doc_hit":
        return abstain()
    return data_answer(51)


def _on_body(it: LaneItem) -> dict[str, Any]:
    body = _off_body(it)
    if it.kind == "doc_hit":
        return doc_answer(lane=LANE_DOCS)
    body["assumptions"] = [*body["assumptions"], LANE_MODEL_OTHER]
    return body


def _run(body_for, items=B_ITEMS, status: int = 200) -> list[AskRecord]:
    return [AskRecord(it, status, body_for(it)) for it in items]


def _confirm() -> list[ConfirmRecord]:
    no_payload = answer(
        "abstain",
        NOT_FOUND,
        assumptions=[
            "user answered no to the closest question",
            "step ticket: served_by=cortex:pipeline-failure-ticket served_provider=none "
            "served_model=none served_local=false (plan_sql.pipeline_failure recorded)",
        ],
    )
    engine_no = abstain("I can't answer that (no metric).")
    return [
        ConfirmRecord(B_GOLD[0], "no", "payload", 200, no_payload),
        ConfirmRecord(B_GOLD[1], "no", "engine", 200, engine_no, off_body=abstain("I can't answer that (no metric).")),
        ConfirmRecord(B_GOLD[2], "yes", "payload", 200, data_answer()),
    ]


def _fault() -> FaultLeg:
    doc_down = [AskRecord(B_HIT, 200, abstain(assumptions=["lane: other (fallback:doc-error-RuntimeError; calls=2)"]))]
    lane_down = [
        AskRecord(it, 200, {**_off_body(it), "assumptions": ["lane: other (fallback:http-503; calls=1)"]})
        for it in B_GOLD
    ]
    return FaultLeg(doc_down=doc_down, lane_down=lane_down, corpus=CORPUS)


def _report(on1=None, on2=None, off1=None, off2=None, **kw: Any):
    kw.setdefault("confirm", _confirm())
    kw.setdefault("fault", _fault())
    return bar_report(
        off1 or _run(_off_body),
        off2 or _run(_off_body),
        on1 or _run(_on_body),
        on2 or _run(_on_body),
        **kw,
    )


def test_stage_b_clean_passes() -> None:
    report = _report(mode="engine")
    assert report.verdict == PASS, {k: v for k, v in report.bars.items() if v.verdict != PASS}
    assert report.reported["B6:on1"]["false_documents"] == "0/11"


def test_stage_b_legs_not_run_are_inconclusive() -> None:
    report = _report(confirm=None)
    assert report.bars["B8"].verdict == "NOT_RUN" and report.verdict == INCONCLUSIVE
    assert _report(fault=None).verdict == INCONCLUSIVE


def test_stage_b_must_fail_lane_caused_wrong() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        return doc_answer(lane=LANE_DOCS) if it.id == "g0" else _on_body(it)

    report = _report(on1=_run(on))
    assert report.bars["B2"].verdict == FAIL and report.verdict == FAIL
    assert "g0" in report.bars["B2"].detail[0]


def test_stage_b_must_fail_wrong_document_on_doc_hit() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        if it.kind == "doc_hit":
            return doc_answer("Invoices are payable Net 30.", lane=LANE_DOCS)
        return _on_body(it)

    assert _report(on2=_run(on)).bars["B2"].verdict == FAIL


def test_stage_b_must_fail_lost_right() -> None:
    def off(it: LaneItem) -> dict[str, Any]:
        return doc_answer() if it.kind == "doc_hit" else _off_body(it)

    def on(it: LaneItem) -> dict[str, Any]:
        if it.kind == "doc_hit":
            return doc_answer("The terms are in clause 3.", lane=LANE_DOCS)
        return _on_body(it)

    report = _report(off1=_run(off), off2=_run(off), on1=_run(on))
    assert report.bars["B3"].verdict == FAIL
    assert report.bars["B2"].verdict == PASS  # not lane-caused: off served a document too


def test_stage_b_must_fail_added_block_and_lane_documents_without_document() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        if it.id == "g1":
            return {**blocked(), "assumptions": [LANE_MODEL_OTHER]}
        if it.id == "g2":
            return {**abstain(), "assumptions": [LANE_DOCS]}
        return _on_body(it)

    report = _report(on1=_run(on))
    assert report.bars["B5"].verdict == FAIL
    assert len(report.bars["B5"].detail) == 2


def test_stage_b_must_fail_destructive() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        if it.kind == "destructive":
            return {**abstain(), "assumptions": [LANE_MODEL_OTHER]}
        return _on_body(it)

    assert _report(on1=_run(on), mode="engine").bars["B4"].verdict == FAIL
    assert _report(on1=_run(on), mode="payload").bars["B4"].verdict == PASS

    def on_doc(it: LaneItem) -> dict[str, Any]:
        return doc_answer(lane=LANE_DOCS) if it.kind == "destructive" else _on_body(it)

    assert _report(on1=_run(on_doc), mode="payload").bars["B4"].verdict == FAIL


def test_stage_b_must_fail_fallback_over_two_percent() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        body = _on_body(it)
        if it.id == "g3":
            body["assumptions"] = ["lane: other (fallback:unusable; calls=1)"]
        return body

    report = _report(on2=_run(on))
    assert report.bars["B6"].verdict == FAIL
    assert report.reported["B6:on2"]["fallback"] == "1/12"


def test_stage_b_must_fail_false_documents_decision() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        body = _on_body(it)
        if it.id == "g4":
            body["assumptions"] = [LANE_MISS]
        return body

    assert _report(on1=_run(on)).bars["B6"].verdict == FAIL


def test_stage_b_skips_leave_the_denominator() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        body = _on_body(it)
        if it.id != "hit":
            body["assumptions"] = ["lane: skipped (skipped:certified; calls=0)"]
        return body

    report = _report(on1=_run(on), on2=_run(on))
    assert report.reported["B6:on1"]["skipped"] == {"skipped:certified": 11}
    assert report.reported["B6:on1"]["false_documents"] == "0/0"
    assert report.bars["B6"].verdict == PASS


def test_stage_b_must_fail_http_and_invalid_bodies() -> None:
    on = _run(_on_body)
    on[0] = AskRecord(on[0].item, 500, {"detail": "boom"})
    assert _report(on1=on).bars["B1"].verdict == FAIL
    on = _run(_on_body)
    on[1] = AskRecord(on[1].item, 200, {**data_answer(), "sql_used": None})
    assert _report(on1=on).bars["B1"].verdict == FAIL
    # The same mapped 4xx in both arms is not a router failure.
    off = [AskRecord(r.item, 403, {"detail": "x"}) if r.item.id == "g0" else r for r in _run(_off_body)]
    on = [AskRecord(r.item, 403, {"detail": "x"}) if r.item.id == "g0" else r for r in _run(_on_body)]
    assert _report(off1=off, on1=on).bars["B1"].verdict == PASS


def test_stage_b_inconclusive_when_noise_is_over_threshold() -> None:
    def noisy(it: LaneItem) -> dict[str, Any]:
        return abstain() if it.id.startswith("g") else _off_body(it)

    report = _report(off2=_run(noisy))
    assert report.reported["noise"]["delta_right"] == 10
    assert report.bars["noise"].verdict == INCONCLUSIVE
    assert report.verdict == INCONCLUSIVE


def test_stage_b_bird_thresholds_and_document_rule() -> None:
    bird = [item(f"b{n}", rows=[{"sku_count": 51}]) for n in range(20)]
    off = [AskRecord(it, 200, data_answer(), outcome=RIGHT) for it in bird]
    lost = [
        AskRecord(it, 200, abstain(), outcome=MISS) if n < 12 else AskRecord(it, 200, data_answer(), outcome=RIGHT)
        for n, it in enumerate(bird)
    ]
    report = bar_report(off, lost, off, off, bird=True, confirm=[], fault=_fault())
    assert report.bars["noise"].verdict == PASS  # 12 <= 15 on BIRD

    on = [
        AskRecord(it, 200, {**doc_answer(lane=LANE_DOCS)}) if n == 0 else
        AskRecord(it, 200, {**data_answer(), "assumptions": [LANE_MODEL_OTHER]}, outcome=RIGHT)
        for n, it in enumerate(bird)
    ]
    report = bar_report(off, off, on, on, bird=True, confirm=[], fault=_fault())
    assert report.bars["B2"].verdict == FAIL


def test_stage_b_aggregate_backstop_must_fail() -> None:
    def worse(it: LaneItem) -> dict[str, Any]:
        if it.id in {"g0", "g1"}:
            return {**data_answer(40), "assumptions": [LANE_MODEL_OTHER]}
        return _on_body(it)

    report = _report(on1=_run(worse))
    assert report.bars["B7"].verdict == FAIL  # WRONG rose by 2 over a 0 noise floor
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 0) == pytest.approx(1 / 32)
    assert sign_test_p(3, 3) > 0.05


def test_stage_b_backstop_must_fail_wrong_rise_alone() -> None:
    """WRONG rises past δ_W while RIGHT holds and nothing is lost: only the WRONG delta fires."""
    missed = {"g0", "g1"}

    def off(it: LaneItem) -> dict[str, Any]:
        return abstain() if it.id in missed else _off_body(it)

    def on(it: LaneItem) -> dict[str, Any]:
        if it.id in missed:
            return {**data_answer(40), "assumptions": [LANE_MODEL_OTHER]}  # MISS -> WRONG
        return _on_body(it)

    off_run = _run(off)
    report = _report(off1=off_run, off2=off_run, on1=_run(on), on2=_run(on))
    assert report.reported["noise"] == {"delta_right": 0, "delta_wrong": 0, "flips": 0}
    assert report.reported["B7"]["losses"] == 0
    assert report.bars["B7"].verdict == FAIL
    assert [d.split()[0] for d in report.bars["B7"].detail] == ["WRONG", "WRONG"]


def test_stage_b_backstop_must_fail_right_drop_alone() -> None:
    """One RIGHT lost to a MISS per on run: no WRONG rise, sign test p = 0.25, only the RIGHT delta fires."""

    def on(it: LaneItem) -> dict[str, Any]:
        if it.id == "g0":
            return {**abstain(), "assumptions": [LANE_MODEL_OTHER]}  # RIGHT -> MISS
        if it.kind == "doc_hit":
            # The doc_hit stays a MISS so no gain offsets the loss.
            return {**abstain(), "assumptions": [LANE_MISS]}
        return _on_body(it)

    report = _report(on1=_run(on), on2=_run(on))
    assert report.reported["noise"]["delta_right"] == 0
    assert report.reported["B7"] == {"losses": 2, "gains": 0, "p": 0.25}
    assert report.bars["B7"].verdict == FAIL
    assert [d.split()[0] for d in report.bars["B7"].detail] == ["RIGHT", "RIGHT"]


def test_stage_b_must_fail_confirm_leg() -> None:
    confirm = _confirm()
    lane_called = [
        ConfirmRecord(c.item, c.answer, c.mode, c.status, {**c.body, "assumptions": [*c.body["assumptions"], LANE_MODEL_OTHER]})
        if n == 2 else c
        for n, c in enumerate(confirm)
    ]
    assert _report(confirm=lane_called).bars["B8"].verdict == FAIL

    no_ticket = list(confirm)
    body = dict(confirm[0].body)
    body["assumptions"] = ["user answered no to the closest question"]
    no_ticket[0] = ConfirmRecord(confirm[0].item, "no", "payload", 200, body)
    assert _report(confirm=no_ticket).bars["B8"].verdict == FAIL

    session_no = list(confirm)
    session_no[0] = ConfirmRecord(confirm[0].item, "no", "payload", 200, {**confirm[0].body, "provenance": {"layer": "x", "badge": "session"}, "sql_used": "SELECT 1"})
    assert _report(confirm=session_no).bars["B8"].verdict == FAIL

    drifted = list(confirm)
    drifted[1] = ConfirmRecord(confirm[1].item, "no", "engine", 200, confirm[1].body, off_body=abstain("Different text."))
    assert _report(confirm=drifted).bars["B8"].verdict == FAIL


def test_stage_b_must_fail_fault_leg() -> None:
    fault = _fault()
    extractive = FaultLeg(
        doc_down=[AskRecord(B_HIT, 200, abstain(f"I can't answer that. {DOC_TEXT}"))],
        lane_down=fault.lane_down,
        corpus=CORPUS,
    )
    assert _report(fault=extractive).bars["B9"].verdict == FAIL

    served = FaultLeg(doc_down=[AskRecord(B_HIT, 200, doc_answer())], lane_down=fault.lane_down, corpus=CORPUS)
    assert _report(fault=served).bars["B9"].verdict == FAIL

    hidden = FaultLeg(
        doc_down=fault.doc_down,
        lane_down=[AskRecord(it, 200, {**_off_body(it), "assumptions": [LANE_MODEL_OTHER]}) for it in B_GOLD],
        corpus=CORPUS,
    )
    report = _report(fault=hidden)
    assert report.bars["B9"].verdict == FAIL
    assert "did not report FAIL" in report.bars["B9"].detail[-1]


def test_stage_b_must_fail_fault_leg_document_served_with_non_corpus_text() -> None:
    """A document answer whose text is not corpus text: only the served-document check sees it."""
    fault = _fault()
    paraphrased = doc_answer("The supplier gets paid about six weeks after goods arrive.")
    served = FaultLeg(doc_down=[AskRecord(B_HIT, 200, paraphrased)], lane_down=fault.lane_down, corpus=CORPUS)
    report = _report(fault=served)
    assert report.bars["B9"].verdict == FAIL
    assert report.bars["B9"].detail == ("doc_down hit: document served with the doc call down",)


@pytest.mark.parametrize("run", ["doc_down", "lane_down"])
def test_stage_b_must_fail_fault_leg_5xx(run: str) -> None:
    """A fault run has no off arm, so a 5xx is never excused: the call down must still answer."""
    fault = _fault()
    if run == "doc_down":
        leg = FaultLeg(
            doc_down=[AskRecord(B_HIT, 500, {"detail": "boom"})], lane_down=fault.lane_down, corpus=CORPUS
        )
        rid = "hit"
    else:
        lane_down = list(fault.lane_down)
        lane_down[0] = AskRecord(lane_down[0].item, 500, {"detail": "boom"})
        leg = FaultLeg(doc_down=fault.doc_down, lane_down=lane_down, corpus=CORPUS)
        rid = "g0"
    report = _report(fault=leg)
    assert report.bars["B9"].verdict == FAIL
    assert report.bars["B9"].detail == (f"{run} {rid}: HTTP 500, no answer served",)


def test_stage_b_refuses_mismatched_runs() -> None:
    with pytest.raises(ValueError, match="different items"):
        _report(on1=_run(_on_body, items=B_GOLD))


# ── Stage C ──────────────────────────────────────────────────────────────────

C_HITS = [item(f"ch{n}", "doc_hit") for n in range(40)]
C_DATA = [item(f"cd{n}") for n in range(100)]
C_RISK = item("cx", "destructive")
C_MISS = item("cm", "doc_miss")
C_ITEMS = [*C_HITS, *C_DATA, C_RISK, C_MISS]


def _c_off(it: LaneItem) -> dict[str, Any]:
    if it.kind == "data":
        return env()
    return env("ABSTAIN", "I can't vouch for this answer.")


def _c_on(it: LaneItem) -> dict[str, Any]:
    body = _c_off(it)
    line = LANE_MISS if it.kind == "doc_hit" else LANE_MODEL_OTHER
    body["assumptions"] = [*body["assumptions"], line]
    return body


def _c_run(arm: str, body_for, items=C_ITEMS, leg: str = "product", status_for=None) -> list[StageCRecord]:
    return [
        StageCRecord(it, leg, arm, (status_for(it) if status_for else 200), body_for(it))
        for it in items
    ]


def _c_report(off=None, on=None, reasks=()):
    return stage_c_report(off or _c_run("off", _c_off), on or _c_run("on", _c_on), reasks)


def test_stage_c_clean_with_reach_passes() -> None:
    report = _c_report()
    assert report.verdict == PASS, report.bars
    assert report.reported["router_model:product"] == {"doc_hit": 40, "non_document": 101}


def test_stage_c_inconclusive_when_the_router_was_never_reached() -> None:
    """The defect the plan checker found: Stage C passing on asks the router never saw."""

    def dms_only(it: LaneItem) -> dict[str, Any]:
        return _c_off(it) | {"assumptions": ["GEN-01: ai_first_no_model_sql"]}

    def skipped(it: LaneItem) -> dict[str, Any]:
        return _c_off(it) | {"assumptions": ["lane: skipped (skipped:no-space; calls=0)", "live Cortex ask"]}

    for body_for in (dms_only, skipped):
        report = _c_report(on=_c_run("on", body_for))
        assert report.bars["C1"].verdict == PASS
        assert report.bars["reach"].verdict == INCONCLUSIVE
        assert report.verdict == INCONCLUSIVE
    reach = _c_report(on=_c_run("on", skipped)).reported["reach:product"]
    assert reach["on:doc_hit"]["reach"] == {"router-skipped:no-space": 40}

    # One short of the minimum on either count is still INCONCLUSIVE.
    short = _c_run("on", _c_on, items=[*C_HITS[:39], *C_DATA, C_RISK, C_MISS])
    off_short = _c_run("off", _c_off, items=[*C_HITS[:39], *C_DATA, C_RISK, C_MISS])
    assert _c_report(off=off_short, on=short).verdict == INCONCLUSIVE


def test_stage_c_must_fail_on_only_green_on_destructive() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        return env(assumptions=["live Cortex ask", LANE_MODEL_OTHER]) if it is C_RISK else _c_on(it)

    report = _c_report(on=_c_run("on", on))
    assert report.bars["C2"].verdict == FAIL and report.verdict == FAIL

    # Pre-existing nondeterminism: an off-arm re-ask also gives green.
    reask = [StageCRecord(C_RISK, "product", "off", 200, env())]
    report = _c_report(on=_c_run("on", on), reasks=reask)
    assert report.bars["C2"].verdict == PASS
    assert report.reported["nondeterministic"] == ["product cx"]

    # Only a green off-arm re-ask of the same item in the same leg excuses it.
    for wrong_reask in (
        StageCRecord(C_RISK, "product", "on", 200, env()),  # on arm
        StageCRecord(C_RISK, "grounded", "off", 200, env()),  # other leg
        StageCRecord(C_RISK, "product", "off", 200, env("ABSTAIN", "I can't vouch for this answer.")),
        StageCRecord(C_MISS, "product", "off", 200, env()),  # other item
    ):
        report = _c_report(on=_c_run("on", on), reasks=[wrong_reask])
        assert report.bars["C2"].verdict == FAIL, wrong_reask
        assert report.reported["nondeterministic"] == []

    # A green that never reached Cortex came from a DMS lane, not the router.
    def dms_green(it: LaneItem) -> dict[str, Any]:
        return env(assumptions=["verified query"]) if it is C_RISK else _c_on(it)

    assert _c_report(on=_c_run("on", dms_green)).bars["C2"].verdict == PASS


def test_stage_c_green_in_both_arms_is_listed_not_failed() -> None:
    def both(it: LaneItem) -> dict[str, Any]:
        return env("L2_VALIDATED", "Reconfirm: nothing matched.") if it is C_RISK else _c_off(it)

    def both_on(it: LaneItem) -> dict[str, Any]:
        return both(it) if it is C_RISK else _c_on(it)

    report = _c_report(off=_c_run("off", both), on=_c_run("on", both_on))
    assert report.bars["C2"].verdict == PASS
    assert report.reported["both_green"][0]["id"] == "cx"
    assert report.reported["both_green"][0]["abstention_prose_p0"] is True
    assert report.bars["C1"].verdict == FAIL  # and the checker refuses that envelope


def test_stage_c_must_fail_on_502_where_off_was_200() -> None:
    def status(it: LaneItem) -> int:
        return 502 if it is C_MISS else 200

    def body(it: LaneItem) -> Any:
        if it is C_MISS:
            return {"detail": {"code": "submit_failed", "message": "'document' is not a valid Badge"}}
        return _c_on(it)

    report = _c_report(on=_c_run("on", body, status_for=status))
    assert report.bars["C1"].verdict == FAIL
    assert report.bars["C3"].verdict == FAIL  # a router-served document on a doc_miss
    assert report.reported["reach:product"]["on:doc_miss"]["errors"] == {"502 submit_failed": 1}


def test_stage_c_same_error_in_both_arms_is_reported() -> None:
    def status(it: LaneItem) -> int:
        return 403 if it is C_RISK else 200

    def err(arm_body):
        def body(it: LaneItem) -> Any:
            return {"detail": {"code": "compliance_block", "message": "x"}} if it is C_RISK else arm_body(it)

        return body

    report = _c_report(
        off=_c_run("off", err(_c_off), status_for=status),
        on=_c_run("on", err(_c_on), status_for=status),
    )
    assert report.bars["C1"].verdict == PASS
    assert report.reported["same_error"] == ["product cx: HTTP 403 compliance_block"]


def test_stage_c_must_fail_demo_fallback() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        return _c_on(it) | {"demo_fallback_used": True} if it.id == "cd0" else _c_on(it)

    assert _c_report(on=_c_run("on", on)).bars["C1"].verdict == FAIL


def test_stage_c_must_fail_documents_on_doc_miss() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        if it is C_MISS:
            return env("ABSTAIN", "I can't vouch for this answer.", assumptions=["live Cortex ask", LANE_DOCS])
        return _c_on(it)

    report = _c_report(on=_c_run("on", on))
    assert report.bars["C3"].verdict == FAIL
    assert "doc_miss" in report.bars["C3"].detail[0]


def test_stage_c_must_fail_green_lane_documents_envelope() -> None:
    def on(it: LaneItem) -> dict[str, Any]:
        if it is C_HITS[0]:
            return env("L2_VALIDATED", "Net 45 [1]", assumptions=["live Cortex ask", LANE_DOCS])
        return _c_on(it)

    report = _c_report(on=_c_run("on", on))
    assert report.bars["C3"].verdict == FAIL
    assert "green or carries rows" in report.bars["C3"].detail[0]


def test_stage_c_grounded_leg_can_carry_the_reach() -> None:
    def dms_only(it: LaneItem) -> dict[str, Any]:
        return _c_off(it) | {"assumptions": ["GEN-01: ai_first_no_model_sql"]}

    off = [*_c_run("off", _c_off), *_c_run("off", _c_off, leg="grounded")]
    on = [*_c_run("on", dms_only), *_c_run("on", _c_on, leg="grounded")]
    report = stage_c_report(off, on)
    assert report.verdict == PASS
    assert report.reported["router_model:product"] == {"doc_hit": 0, "non_document": 0}


def test_stage_c_must_fail_one_arm_only() -> None:
    report = _c_report(on=_c_run("on", _c_on, items=C_ITEMS[:-1]))
    assert report.bars["C1"].verdict == FAIL


def test_stage_b_sign_test_must_fail_inside_the_noise_floor() -> None:
    """Five lost, none gained: within δ_R = 5, but one-sided p = 1/32 < 0.05."""
    lost = {f"g{n}" for n in range(5)}

    def off2(it: LaneItem) -> dict[str, Any]:
        return abstain() if it.id in lost else _off_body(it)

    def on(it: LaneItem) -> dict[str, Any]:
        if it.id in lost or it.kind == "doc_hit":
            # The doc_hit stays a MISS so no gain offsets the losses.
            return {**abstain(), "assumptions": [LANE_MISS]}
        return _on_body(it)

    report = _report(off2=_run(off2), on1=_run(on), on2=_run(on))
    assert report.reported["noise"]["delta_right"] == 5
    assert report.reported["B7"] == {"losses": 5, "gains": 0, "p": 1 / 32}
    assert report.bars["B7"].verdict == FAIL
    assert "sign test" in report.bars["B7"].detail[-1]
