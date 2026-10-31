"""LANES-V2 frozen labels: lanes_v1 with its label review applied (plan section 6.1).

Every lanes_v1 bar is kept: the v1 checks in ``tests/dms/test_lanes_v1_set.py`` are
called here on the v2 set, so a bar cannot drift between the two files. On top of
them v2 pins what the review changed: the same 535 items, a ruling for every
disagreement, labels and gold changed only by a ruling, the site dual-coding rule,
and the old and new gold values re-run against data/samples.
"""

from __future__ import annotations

import re

import pytest

from bench.lanes import (
    DOCS_DIR,
    LANES_V2_PATH,
    LANES_V2_SHA_PATH,
    LaneItem,
    LaneSet,
    file_sha256,
    load_lanes,
)
from tests.dms import test_lanes_v1_set as v1_bars
from tests.dms.test_lanes_v1_set import KEY_SHAPED
from tests.invariants.test_model_egress_chokepoint import PROVIDER_HOST

LABEL_FIELDS = ("lane", "kind", "accept")
FOUNDER = "founder (2026-10-31)"
DELEGATED = "claude (founder-delegated 2026-10-31)"


@pytest.fixture(scope="module")
def lanes() -> LaneSet:
    return load_lanes(LANES_V2_PATH)


@pytest.fixture(scope="module")
def lanes_v1() -> LaneSet:
    return load_lanes()


@pytest.fixture(scope="module")
def review(lanes: LaneSet) -> dict:
    return lanes.header["review"]


def _label(item: LaneItem, field: str):
    value = getattr(item, field)
    return sorted(value) if field == "accept" else value


# ── every lanes_v1 bar, on the v2 set ────────────────────────────────────────


@pytest.mark.parametrize(
    "bar",
    [
        v1_bars.test_held_out_counts,
        v1_bars.test_questions_are_distinct,
        v1_bars.test_every_c7_heldout_item_is_pinned_held_out,
        v1_bars.test_destructive_intent_lists_are_present,
        v1_bars.test_every_gold_doc_exists_and_holds_its_facts,
        v1_bars.test_labels_are_consistent,
        v1_bars.test_no_paraphrase_group_straddles_the_splits,
        v1_bars.test_review_block_is_well_formed,
        v1_bars.test_label_review_is_recorded,
        v1_bars.test_held_out_data_gold_runs_and_returns_rows,
    ],
    ids=lambda f: f.__name__,
)
def test_v1_bar_holds_on_v2(bar, lanes: LaneSet) -> None:
    bar(lanes)


def test_sha256_matches_the_frozen_file() -> None:
    recorded = LANES_V2_SHA_PATH.read_text(encoding="utf-8").split()
    assert recorded[1:] == ["lanes_v2.yaml"]
    assert recorded[0] == file_sha256(LANES_V2_PATH)


def test_no_key_shaped_strings_or_provider_hosts() -> None:
    for path in [LANES_V2_PATH, LANES_V2_SHA_PATH, *sorted(DOCS_DIR.glob("*.txt"))]:
        text = path.read_text(encoding="utf-8")
        assert not KEY_SHAPED.search(text), path.name
        assert not PROVIDER_HOST.search(text), path.name


# ── what the review changed ──────────────────────────────────────────────────


def test_v2_keeps_every_v1_item(lanes: LaneSet, lanes_v1: LaneSet, review: dict) -> None:
    assert review["dropped"] == []
    assert [i.id for i in lanes.items] == [i.id for i in lanes_v1.items]
    for new, old in zip(lanes.items, lanes_v1.items, strict=True):
        for field in ("split", "question", "group", "source", "must_block", "doc", "facts"):
            assert getattr(new, field) == getattr(old, field), (new.id, field)


def test_review_records_a_different_family_labeler_and_kappa(review: dict) -> None:
    assert review["labeler_family"] == "minimax" != review["writer"]["family"]
    assert review["kappa"] == review["kappa_detail"]["all"]["lane"]
    for split in ("all", "heldout", "train"):
        for field in LABEL_FIELDS:
            assert -1 <= review["kappa_detail"][split][field] <= 1, (split, field)


def test_spot_check_is_owed_and_status_stays_pending(review: dict) -> None:
    """review_problems() needs the founder's spot check for complete, so v2 is pending."""
    if review["founder_spot_check"]:
        pytest.skip("founder spot check recorded")
    assert review["status"] == "pending"
    assert len(set(review["founder_spot_check_sample"]["ids"])) == 54
    assert any("founder_spot_check" in owed for owed in review["owed"])


def test_every_disagreement_has_a_ruling(lanes: LaneSet, review: dict) -> None:
    by_id = {i.id: i for i in lanes.items}
    entries = review["adjudication"]
    assert len({e["id"] for e in entries}) == len(entries) == 124
    assert sum(review["ruling_counts"].values()) == len(entries)
    for e in entries:
        assert e["adjudicator"] in (FOUNDER, DELEGATED), e["id"]
        assert e["reason"], e["id"]
        if e["ruling_no"] in ("4", "5"):
            assert e["adjudicator"] == DELEGATED, e["id"]
        item = by_id[e["id"]]
        for field in LABEL_FIELDS:
            ruled = sorted(e["ruling"][field]) if field == "accept" else e["ruling"][field]
            assert ruled == _label(item, field), (e["id"], field)


def test_ambiguous_rulings_carry_their_evidence(review: dict) -> None:
    for e in review["adjudication"]:
        if "evidence" not in e:
            continue
        ev = e["evidence"]
        assert ev["disambiguate_by_asking"], e["id"]
        assert ev["all_readings_agree"] is False, e["id"]
        # Readings disagree, so the item is ambiguous: reconfirm only.
        assert e["ruling"]["kind"] == "ambiguous" and e["ruling"]["accept"] == ["abstain"], e["id"]


def test_labels_and_gold_change_only_by_a_ruling(
    lanes: LaneSet, lanes_v1: LaneSet, review: dict
) -> None:
    changed = {e["id"]: e["changed_from_v1"] or {} for e in review["adjudication"]}
    gold_ids = {g["id"] for g in review["gold_changes"]}
    for new, old in zip(lanes.items, lanes_v1.items, strict=True):
        for field in LABEL_FIELDS:
            if _label(new, field) != _label(old, field):
                assert field in changed.get(new.id, {}), (new.id, field)
        if (new.canonical_sql, new.key_columns, new.match) != (
            old.canonical_sql,
            old.key_columns,
            old.match,
        ):
            assert new.id in gold_ids, new.id


def test_doc_miss_lane_is_documents(lanes: LaneSet) -> None:
    """Ruling 2: a question about a document routes to documents; a miss falls through."""
    assert {i.lane for i in lanes.items if i.kind == "doc_miss"} == {"documents"}


def test_gold_changes_rerun_to_their_recorded_values(lanes: LaneSet, review: dict) -> None:
    from bench.accuracy import _ensure_db_loaded, _run_canonical

    _ensure_db_loaded()
    by_id = {i.id: i for i in lanes.items}
    v1 = {i.id: i for i in load_lanes().items}
    assert len(review["gold_changes"]) == 20
    for change in review["gold_changes"]:
        item = by_id[change["id"]]
        assert _run_canonical(item.canonical_sql) == change["new"], item.id
        assert _run_canonical(v1[item.id].canonical_sql) == change["old"], item.id
        assert change["adjudicator"] in (FOUNDER, DELEGATED) and change["reason"], item.id
        assert (change["old"] == change["new"]) is (change["note"] == "value unchanged"), item.id


def test_every_site_scoped_inventory_gold_uses_the_site_rule(lanes: LaneSet, review: dict) -> None:
    """The scan of every gold that names a location: inventory joined per site takes the rule."""
    scanned = {s["id"]: s for s in review["site_rule_scan"]}
    for item in lanes.items:
        sql = (item.canonical_sql or "").lower()
        if "location" not in sql:
            continue
        assert item.id in scanned, item.id
        if "join locations l on (i.location_id = l.location_id or" in sql:
            assert scanned[item.id]["result"] == "rule applied", item.id
    rule_items = [i for i in lanes.items if "regexp_replace" in (i.canonical_sql or "")]
    assert len(rule_items) == 19


def test_rows_only_items_have_data_to_answer_from(lanes: LaneSet, review: dict) -> None:
    """Ruling 1 covers answerable questions; the ones with no data are abstain."""
    by_id = {i.id: i for i in lanes.items}
    no_data = review["ruling1_no_data"]
    assert len(no_data) == 7
    for iid in no_data:
        assert by_id[iid].kind == "data" and by_id[iid].accept == ("abstain",), iid
        assert not by_id[iid].has_data_gold, iid


# ── the site dual-coding rule (ruling 3) ─────────────────────────────────────


def _site_on(lanes: LaneSet) -> str:
    item = next(i for i in lanes.items if i.id == "data_loc_total_kg_wh-a")
    match = re.search(r" ON (\(.*\)) WHERE ", item.canonical_sql or "")
    assert match, "site rule not found in data_loc_total_kg_wh-a"
    return match.group(1)


def _sites_for(lanes: LaneSet, location_id: str) -> int:
    from bench.accuracy import _ensure_db_loaded, _run_canonical

    _ensure_db_loaded()
    safe = location_id.replace("'", "''")
    on = _site_on(lanes).replace("i.location_id", f"'{safe}'")
    return _run_canonical(f"SELECT COUNT(*) AS n FROM locations l WHERE {on}")[0]["n"]


def test_site_rule_maps_every_inventory_row_to_exactly_one_site(lanes: LaneSet) -> None:
    from bench.accuracy import _ensure_db_loaded, _run_canonical

    _ensure_db_loaded()
    on = _site_on(lanes)
    rows = _run_canonical(
        f"SELECT n, COUNT(*) AS rows FROM (SELECT i.rowid AS r, COUNT(l.location_id) AS n "
        f"FROM inventory i LEFT JOIN locations l ON {on} GROUP BY i.rowid) GROUP BY n"
    )
    assert [r["n"] for r in rows] == [1], rows


def test_site_rule_must_fail_on_a_spelling_it_cannot_place(lanes: LaneSet) -> None:
    assert _sites_for(lanes, "Warehouse A") == 1
    assert _sites_for(lanes, "wh_o") == 1
    assert _sites_for(lanes, "whse o") == 1
    assert _sites_for(lanes, "Shah Alam Central") == 1
    assert _sites_for(lanes, "Warehouse Z") == 0
    assert _sites_for(lanes, "LOC-999") == 0
    assert _sites_for(lanes, "WH-AB") == 0
