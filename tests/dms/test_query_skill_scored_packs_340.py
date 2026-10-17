"""#340a: nothing from a scored pack is ever written to the query_skill store.

Every question (and certified synonym) of every scored pack in this repo is
asked through ``POST /v1/contract/ask`` with ``CORTEX_QUERY_SKILL=1``, either
tagged with its pack's ``scored_pack_id`` or under ``CORTEX_SCORED_ROUND=1``,
and ``dms_query_skills`` must stay empty. The positive control asks the same
questions untagged and must write, so an empty table is not a vacuous pass.

Limit: a scored pack's question asked *untagged* outside a scored round is
indistinguishable from ordinary traffic; with the flag on it is captured like
any other question. The flag is off by default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from CortexOS.memory.space_memory import SCORED_PACK_IDS, SCORED_PACK_PREFIXES
from tests.dms.test_query_skill_gate_340 import (  # noqa: F401
    FLAG,
    REVENUE_Q,
    WAREHOUSE_GRANT,
    _ask,
    _governed_answer,
    _rows,
    ask_http,
    skills_db,
)

ROOT = Path(__file__).resolve().parents[2]


def _load(rel: str) -> object:
    return yaml.safe_load((ROOT / rel).read_text(encoding="utf-8"))


def _pack_questions() -> dict[str, list[str]]:
    golden = _load("bench/golden/dms_golden_v1.yaml")
    paraphrase = _load("bench/golden/dms_paraphrase_v1.yaml")
    adversarial = _load("bench/golden/dms_adversarial_v1.yaml")
    heldout = _load("bench/heldout/c7_heldout_v1.yaml")
    certified = _load("packs/dms/semantic/certified_queries.yaml")
    assert isinstance(golden, dict) and isinstance(paraphrase, dict)
    assert isinstance(adversarial, dict) and isinstance(heldout, dict) and isinstance(certified, dict)
    packs = {
        "dms_golden_v1": [i["question"] for i in golden["items"]],
        "dms_paraphrase_v1": [q for qs in paraphrase["paraphrases"].values() for q in qs],
        "dms_adversarial_v1": [
            i["question"] for items in adversarial["categories"].values() for i in items
        ],
        "c7_heldout_v1": [" ".join(str(i["question"]).split()) for i in heldout["items"]],
        "certified_queries": [
            q for c in certified["certified"] for q in [c["question"], *(c.get("synonyms") or [])]
        ],
    }
    return {pack: list(dict.fromkeys(qs)) for pack, qs in packs.items()}


PACKS = _pack_questions()
SCORED_IDS = sorted(SCORED_PACK_IDS) + [f"{p}x" for p in SCORED_PACK_PREFIXES]


def _ask_all(client, questions: list[str], **extra: str) -> list[dict]:
    return [_ask(client, "alpha", q, **extra) for q in questions]


def _capture_candidates(bodies: list[dict]) -> int:
    return sum(b["provenance"]["layer"] in ("certified", "governed_metric") for b in bodies)


def test_bench_ids_are_registered_scored_packs() -> None:
    from CortexOS.memory.space_memory import is_scored_pack

    for pack in ("dms_golden_v1", "dms_paraphrase_v1", "dms_adversarial_v1", "c7_heldout_v1"):
        assert is_scored_pack(pack), pack
    assert sum(len(qs) for qs in PACKS.values()) >= 200


@pytest.mark.parametrize("pack", sorted(PACKS))
def test_must_fail_scored_pack_questions_never_written(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch, pack: str  # noqa: F811
) -> None:
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha", WAREHOUSE_GRANT)
    bodies = _ask_all(ask_http, PACKS[pack], scored_pack_id=pack)
    assert _capture_candidates(bodies) >= 1, f"{pack}: no certified/governed answer to refuse"
    assert _rows(skills_db) == 0


def test_must_fail_scored_round_env_writes_no_pack_question(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(FLAG, "1")
    monkeypatch.setenv("CORTEX_SCORED_ROUND", "1")
    ask_http.bind_session("alpha", WAREHOUSE_GRANT)
    bodies = _ask_all(ask_http, [q for qs in PACKS.values() for q in qs])
    assert _capture_candidates(bodies) >= 100
    assert _rows(skills_db) == 0


@pytest.mark.parametrize("scored_pack_id", SCORED_IDS)
def test_must_fail_every_registered_scored_pack_id_refuses(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch, scored_pack_id: str  # noqa: F811
) -> None:
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha")
    _governed_answer(_ask(ask_http, "alpha", REVENUE_Q, scored_pack_id=scored_pack_id))
    assert _rows(skills_db) == 0


def test_positive_control_untagged_pack_questions_are_written(
    ask_http, skills_db: Path, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setenv(FLAG, "1")
    ask_http.bind_session("alpha", WAREHOUSE_GRANT)
    bodies = _ask_all(ask_http, PACKS["dms_golden_v1"])
    assert _rows(skills_db) == _capture_candidates(bodies) > 0
