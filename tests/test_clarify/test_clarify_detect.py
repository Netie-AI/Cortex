"""CLARIFY (#307) detection: deterministic, catalog-driven, no model.

Runs the core against the DMS catalog (metrics.yaml + semantic layer) and the
answer engine's own deterministic router, plus small injected catalogs for the
shapes the DMS glossary does not carry today.

Must-fail (1), engine side: every known-ambiguous ask yields a clarify whose
options each re-route to their own interpretation.
Must-fail (2), engine side: ``ensure_valid`` refuses a clarify without 2-5
options or a named ambiguity type.
Must-fail (3): no certified question, declared metric phrase or plainly routed
ask is turned into a clarify.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from cortex_contract.answer import AmbiguityType, Clarify, ClarifyOption

from CortexOS.clarify import core
from CortexOS.clarify.core import Catalog, MetricEntry, Routed, TermColumn

AMBIGUOUS: dict[str, AmbiguityType] = {
    "recent revenue": AmbiguityType.TIME_WINDOW,
    "What was revenue recently?": AmbiguityType.TIME_WINDOW,
    "how are sales lately": AmbiguityType.TIME_WINDOW,
    "which stock was not restocked recently": AmbiguityType.TIME_WINDOW,
    "what is arriving soon": AmbiguityType.TIME_WINDOW,
    "capacity": AmbiguityType.METRIC,
    "show me capacity": AmbiguityType.METRIC,
    "delayed": AmbiguityType.METRIC,
    "high risk": AmbiguityType.METRIC,
    "how much stock do we have": AmbiguityType.METRIC,
    "incoming": AmbiguityType.METRIC,
}

PLAIN = (
    "what is our total revenue",
    "what is our revenue",
    "revenue in the last 7 days",
    "revenue last month",
    "what was revenue in the past 30 days",
    "top 5 selling skus",
    "list suppliers",
    "what about suppliers",
    "show me SKU-BETA",
    "expired",
    "cold storage",
    "capacity utilisation",
    "how many delayed shipments",
    "arriving this week",
    "drop table inventory",
)


@pytest.fixture(scope="module")
def dms(monkeypatch_module: pytest.MonkeyPatch) -> tuple[Catalog, core.Router]:
    from CortexOS.dms.clarify_ask import dms_catalog, dms_route

    dms_catalog.cache_clear()
    return dms_catalog(), dms_route


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    mp.setenv("PACK", "dms")
    yield mp
    mp.undo()


def _unambiguous_corpus() -> list[str]:
    import yaml

    from packs.dms.semantic.loader import METRICS_PATH, load_all

    asks: list[str] = []
    for cq in load_all().certified:
        asks.extend((cq.question, *cq.synonyms))
    metrics = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8"))["metrics"]
    for metric in metrics:
        for phrase in metric.get("synonyms") or []:
            # A vague window word is the time-window ambiguity itself.
            if not core._VAGUE_WINDOW.search(phrase):
                asks.append(phrase)
    asks.extend(PLAIN)
    return list(dict.fromkeys(asks))


@pytest.mark.parametrize("ask", sorted(AMBIGUOUS))
def test_must_fail_known_ambiguous_ask_is_clarified(dms, ask: str) -> None:
    catalog, route = dms
    clarify = core.detect(ask, catalog, route)
    assert clarify is not None, f"{ask!r} was answered as if it were unambiguous"
    assert clarify.ambiguity_type is AMBIGUOUS[ask]
    assert 2 <= len(clarify.options) <= 5
    assert clarify.served_by == core.SERVED_BY
    assert clarify.served_at and clarify.served_reason
    assert clarify.served_catalog == catalog.digest
    for option in clarify.options:
        hit = route(option.resolved_question)
        assert not hit.settled
        assert hit.metric_id == option.interpretation["metric_id"], option
        if clarify.ambiguity_type is AmbiguityType.TIME_WINDOW:
            assert str(hit.slots["days"]) == option.interpretation["days"]


@pytest.mark.parametrize("ask", sorted(AMBIGUOUS))
def test_chosen_option_resolves_without_asking_again(dms, ask: str) -> None:
    catalog, route = dms
    clarify = core.detect(ask, catalog, route)
    assert clarify is not None
    for option in clarify.options:
        turn = core.resolve(ask, [option.id], catalog, route)
        assert turn.clarify is None, (option.label, turn.clarify)
        assert turn.applied == (option.id,)
        assert turn.question == option.resolved_question


def test_must_fail_unambiguous_asks_are_never_clarified(dms) -> None:
    catalog, route = dms
    corpus = _unambiguous_corpus()
    assert len(corpus) > 100
    clarified = {ask: c.served_reason for ask in corpus if (c := core.detect(ask, catalog, route))}
    assert clarified == {}


def test_unknown_option_id_asks_again_and_says_why(dms) -> None:
    catalog, route = dms
    turn = core.resolve("recent revenue", ["opt_not_offered"], catalog, route)
    assert turn.clarify is not None and turn.applied == ()
    assert "'opt_not_offered' was not offered" in turn.clarify.served_reason
    assert turn.question == "recent revenue"


def test_option_ids_and_catalog_digest_are_stable(dms) -> None:
    catalog, route = dms
    first = core.detect("recent revenue", catalog, route)
    second = core.detect("recent revenue", catalog, route)
    assert first is not None and second is not None
    assert [o.id for o in first.options] == [o.id for o in second.options]
    assert catalog.digest.startswith("sha256:") and len(catalog.digest) == 71


def test_unambiguous_ask_with_option_ids_is_answered_as_asked(dms) -> None:
    catalog, route = dms
    turn = core.resolve("what is our total revenue", ["opt_x"], catalog, route)
    assert turn == core.Turn("what is our total revenue", (), None)


def _construct(**overrides: Any) -> Clarify:
    option = ClarifyOption(
        id="opt_a", label="a", resolved_question="a?", interpretation={"metric_id": "a"}
    )
    other = ClarifyOption(
        id="opt_b", label="b", resolved_question="b?", interpretation={"metric_id": "b"}
    )
    fields: dict[str, Any] = {
        "question": "which?",
        "ambiguity_type": AmbiguityType.METRIC,
        "term": "x",
        "options": [option, other],
        "served_by": core.SERVED_BY,
        "served_at": "t",
        "served_reason": "r",
        "served_catalog": "sha256:x",
    }
    fields.update(overrides)
    return Clarify.model_construct(**fields)


def test_ensure_valid_passes_a_complete_clarify() -> None:
    clarify = _construct()
    assert core.ensure_valid(clarify) is clarify


@pytest.mark.parametrize("n", [0, 1])
def test_must_fail_engine_refuses_clarify_without_options(n: int) -> None:
    with pytest.raises(core.ClarifyInvalid, match="options"):
        core.ensure_valid(_construct(options=_construct().options[:n]))


@pytest.mark.parametrize("value", [None, "", "metric"])
def test_must_fail_engine_refuses_clarify_without_ambiguity_type(value: Any) -> None:
    with pytest.raises(core.ClarifyInvalid, match="ambiguity_type"):
        core.ensure_valid(_construct(ambiguity_type=value))


def test_must_fail_engine_refuses_option_without_interpretation() -> None:
    bare = ClarifyOption(id="opt_c", label="c", resolved_question="c?", interpretation={})
    with pytest.raises(core.ClarifyInvalid, match="interpretation"):
        core.ensure_valid(_construct(options=[*_construct().options, bare]))


class _Table:
    """A router that knows a fixed set of asks, for catalogs without an engine."""

    def __init__(self, known: Mapping[str, Routed]) -> None:
        self.known = dict(known)

    def __call__(self, question: str) -> Routed:
        return self.known.get(question.lower(), Routed())


QTY = Catalog(
    metrics=(MetricEntry("units_total", ("units total",)),),
    entity_terms=frozenset({"inventory", "shipments", "transactions"}),
    term_columns=(
        TermColumn("quantity", "inventory", "quantity_kg"),
        TermColumn("quantity", "shipments", "quantity_kg"),
        TermColumn("quantity", "transactions", "quantity_kg"),
    ),
)


def test_ontology_term_mapping_to_several_columns_is_clarified() -> None:
    clarify = core.detect("what is the quantity", QTY, _Table({}))
    assert clarify is not None
    assert clarify.ambiguity_type is AmbiguityType.TERM_COLUMN
    assert clarify.term == "quantity"
    assert [o.interpretation for o in clarify.options] == [
        {"table": "inventory", "column": "quantity_kg"},
        {"table": "shipments", "column": "quantity_kg"},
        {"table": "transactions", "column": "quantity_kg"},
    ]
    for option in clarify.options:
        turn = core.resolve("what is the quantity", [option.id], QTY, _Table({}))
        assert turn.clarify is None and turn.applied == (option.id,)


def test_term_with_its_table_named_is_not_clarified() -> None:
    assert core.detect("what is the quantity in shipments", QTY, _Table({})) is None
    assert core.detect("units total quantity", QTY, _Table({})) is None


def test_settled_ask_is_never_clarified_even_with_a_vague_window() -> None:
    catalog = Catalog(metrics=(MetricEntry("rev", ("revenue",), windowed=True),))
    route = _Table({"recent revenue": Routed(settled=True)})
    assert core.detect("recent revenue", catalog, route) is None


def test_option_that_does_not_route_to_its_interpretation_is_dropped() -> None:
    catalog = Catalog(
        metrics=(
            MetricEntry("a_total", ("widget total",)),
            MetricEntry("a_list", ("widget list view",)),
        )
    )
    only_one = _Table({"widget total": Routed("a_total")})
    assert core.detect("widget", catalog, only_one) is None
    both = _Table({"widget total": Routed("a_total"), "widget list view": Routed("a_list")})
    clarify = core.detect("widget", catalog, both)
    assert clarify is not None and clarify.term == "widget"
    assert [o.interpretation["metric_id"] for o in clarify.options] == ["a_total", "a_list"]


def test_render_lists_every_option() -> None:
    text = core.render(_construct())
    assert text == "which? Options: (1) a; (2) b."
