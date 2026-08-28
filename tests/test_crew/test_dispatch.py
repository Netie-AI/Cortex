from __future__ import annotations

import pytest

from CortexOS.crew.dispatch import (
    DEFAULT_MAX_PARALLEL,
    DispatchRefused,
    WorkItem,
    as_error,
    parse_items,
    plan,
)


def _item(item_id: str, *deps: str, cost: int = 1) -> WorkItem:
    return WorkItem(id=item_id, description=item_id, depends_on=tuple(deps), cost=cost)


def test_independent_items_fill_waves_up_to_the_cap() -> None:
    built = plan([_item(f"t{i}") for i in range(7)], max_parallel=3)
    assert [w.ids for w in built.waves] == [
        ("t0", "t1", "t2"),
        ("t3", "t4", "t5"),
        ("t6",),
    ]
    assert built.width == 3
    assert built.item_count == 7


def test_no_wave_is_ever_wider_than_the_cap() -> None:
    for cap in (1, 2, 3, 5, 9):
        built = plan([_item(f"t{i}") for i in range(11)], max_parallel=cap)
        assert built.width <= cap
        assert built.item_count == 11
        assert sum(len(w.placements) for w in built.waves) == 11


def test_dependency_pushes_an_item_into_a_later_wave_and_says_so() -> None:
    built = plan([_item("build"), _item("ship", "build")], max_parallel=4)
    assert [w.ids for w in built.waves] == [("build",), ("ship",)]
    assert built.wave_of("ship") == 1
    ship = built.waves[1].placements[0]
    assert "after build (wave 0)" in ship.reason
    assert "no declared dependencies" in built.waves[0].placements[0].reason


def test_ranking_seats_the_critical_path_then_the_costlier_item() -> None:
    """spec unblocks two later items, so it takes a seat before either leaf;
    of the two leaves the expensive one starts first, and the cheap one is
    told it was held by the cap rather than silently reordered."""
    built = plan(
        [
            _item("spec"),
            _item("big", cost=9),
            _item("small", cost=1),
            _item("ship", "spec"),
            _item("docs", "ship"),
        ],
        max_parallel=2,
    )
    assert [w.ids for w in built.waves] == [("spec", "big"), ("ship", "small"), ("docs",)]
    held = built.waves[1].placements[1]
    assert held.id == "small"
    assert "held 1 wave(s) by the cap of 2" in held.reason
    assert built.waves[0].placements[0].unblocks == 2


def test_same_input_gives_the_same_plan() -> None:
    items = [
        _item("a"),
        _item("b", "a"),
        _item("c", "a"),
        _item("d", "b", "c"),
        _item("e", cost=4),
    ]
    first = plan(items, max_parallel=2)
    second = plan(list(items), max_parallel=2)
    assert first.as_dict() == second.as_dict()
    assert first.render() == second.render()


def test_equal_rank_items_keep_declaration_order() -> None:
    built = plan([_item("zeta"), _item("alpha"), _item("mid")], max_parallel=1)
    assert [w.ids for w in built.waves] == [("zeta",), ("alpha",), ("mid",)]


def test_cycle_is_refused_with_the_cycle_named() -> None:
    with pytest.raises(DispatchRefused) as exc:
        plan([_item("a", "c"), _item("b", "a"), _item("c", "b")], max_parallel=4)
    text = str(exc.value)
    assert "cycle" in text
    # Printed along declared dependencies: a waits on c, c waits on b, b waits
    # on a. The refusal must say which direction the arrow means, or the path
    # reads as an execution order the planner never emitted.
    assert "each item waits on the next" in text
    assert "a -> c -> b -> a" in text


def test_self_dependency_is_a_cycle_not_a_free_pass() -> None:
    with pytest.raises(DispatchRefused) as exc:
        plan([_item("loop", "loop")], max_parallel=2)
    assert "loop -> loop" in str(exc.value)


def test_a_cycle_refuses_the_whole_plan_not_just_the_cyclic_part() -> None:
    """A partial plan would be read as a working plan for the items that
    parsed, and the clean half would run against inputs that never arrive."""
    with pytest.raises(DispatchRefused):
        plan([_item("clean"), _item("x", "y"), _item("y", "x")], max_parallel=4)


def test_unknown_dependency_names_the_id_and_the_item_that_declared_it() -> None:
    with pytest.raises(DispatchRefused) as exc:
        plan([_item("ship", "ghost")], max_parallel=2)
    text = str(exc.value)
    assert "ghost" in text
    assert "ship" in text


def test_duplicate_ids_are_refused() -> None:
    with pytest.raises(DispatchRefused) as exc:
        plan([_item("twin"), _item("twin")], max_parallel=2)
    assert "twin" in str(exc.value)


def test_cap_below_one_is_refused_with_the_value() -> None:
    with pytest.raises(DispatchRefused) as exc:
        plan([_item("a")], max_parallel=0)
    assert "0" in str(exc.value)


def test_negative_cost_hint_is_refused() -> None:
    with pytest.raises(DispatchRefused) as exc:
        plan([_item("a", cost=-3)], max_parallel=2)
    assert "-3" in str(exc.value)


def test_empty_input_is_an_empty_plan_not_a_refusal() -> None:
    built = plan([], max_parallel=DEFAULT_MAX_PARALLEL)
    assert built.waves == ()
    assert built.item_count == 0
    assert built.width == 0


def test_long_chain_is_planned_serially_without_blowing_the_stack() -> None:
    items = [_item("n0")] + [_item(f"n{i}", f"n{i - 1}") for i in range(1, 1200)]
    built = plan(items, max_parallel=8)
    assert len(built.waves) == 1200
    assert built.width == 1
    assert built.waves[-1].ids == ("n1199",)


def test_plan_declares_that_it_does_not_decide_work_shape() -> None:
    blob = plan([_item("a"), _item("b", "a")], max_parallel=2).as_dict()
    assert blob["kind"] == "plan"
    assert blob["decides_work_shape"] is False
    assert blob["max_parallel"] == 2
    assert [w["ids"] for w in blob["waves"]] == [["a"], ["b"]]


def test_render_states_the_cap_and_that_nothing_was_spawned() -> None:
    text = plan([_item("a"), _item("b")], max_parallel=1).render()
    assert "cap 1" in text
    assert "nothing was spawned" in text
    assert "wave 0 (1/1): a" in text


def test_wave_of_returns_none_for_an_unplanned_id() -> None:
    built = plan([_item("a")], max_parallel=1)
    assert built.wave_of("a") == 0
    assert built.wave_of("nope") is None


def test_parse_items_reads_loose_dicts_and_dedupes_deps() -> None:
    items = parse_items(
        [
            {"id": " build ", "description": "compile", "cost": "3"},
            {"id": "ship", "depends_on": ["build", "build", " "], "description": "release"},
            {"id": "docs", "deps": "ship, build"},
        ]
    )
    assert [i.id for i in items] == ["build", "ship", "docs"]
    assert items[0].cost == 3
    assert items[1].depends_on == ("build",)
    assert items[2].depends_on == ("ship", "build")
    assert items[0].as_dict()["description"] == "compile"


def test_parse_items_refuses_a_row_without_an_id_and_names_the_index() -> None:
    with pytest.raises(DispatchRefused) as exc:
        parse_items([{"id": "ok"}, {"description": "nameless"}])
    assert "item 1" in str(exc.value)


def test_parse_items_refuses_a_non_object_row() -> None:
    with pytest.raises(DispatchRefused) as exc:
        parse_items([{"id": "ok"}, "just a string"])
    assert "item 1" in str(exc.value)


def test_parse_items_refuses_a_non_integer_cost() -> None:
    with pytest.raises(DispatchRefused) as exc:
        parse_items([{"id": "ok", "cost": "cheap"}])
    assert "ok" in str(exc.value)


def test_parse_items_refuses_a_non_list_depends_on() -> None:
    with pytest.raises(DispatchRefused) as exc:
        parse_items([{"id": "ok", "depends_on": 7}])
    assert "ok" in str(exc.value)


def test_as_error_carries_the_reason_into_a_tool_result() -> None:
    try:
        plan([_item("ship", "ghost")], max_parallel=2)
    except DispatchRefused as exc:
        text = as_error(exc)
    assert text.startswith("DENIED: ")
    assert "ghost" in text


def test_dispatch_does_not_spawn_execute_or_open_duckdb() -> None:
    """The ticket's whole point: this is a planner. If it ever grows a spawn
    path, the module is a second scheduler and the board ceiling stops meaning
    anything."""
    from pathlib import Path

    source = Path(__file__).resolve().parents[2] / "CortexOS" / "crew" / "dispatch.py"
    body = source.read_text(encoding="utf-8")
    for banned in ("duckdb", "asyncio", "create_task", "subprocess", "packs."):
        assert banned not in body, f"dispatch.py must not reference {banned}"
