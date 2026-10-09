"""VERIFIED-QUERY (#309) library on C-MEM solution memory.

Must-fail tests (each has a guard-removed proof in
test_verified_query_guard_mutations.py):

1. ``test_must_fail_unconfirmed_query_never_reused*`` — proposed, non-steward
   confirmed, or real-result-checked only: never an example.
2. ``test_must_fail_scored_pack_never_writes_or_reads_verified_queries`` — the
   C-MEM (#290) scored-pack and scored-round guards cover every write and read.
3. ``test_must_fail_space_b_never_sees_space_a_verified_query`` — Space isolation.
4. ``test_must_fail_revoked_query_never_reused*`` — revoked: never an example.
5. ``test_must_fail_stored_example_is_not_the_generated_sql`` — the generator
   is called with the example in the prompt and returns its own SQL. The
   stored SQL is not the candidate.

No phrasing lookup. Examples are every live pair in the Space, capped.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from CortexOS.memory import space_memory as sm
from CortexOS.memory import verified_query as vq
from CortexOS.memory.space_memory import (
    NOT_IN_SPACE,
    NOT_STEWARD,
    SCORED_PACK_WRITE,
    SCORED_ROUND_READ,
    SCORED_ROUND_WRITE,
    Actor,
    MemoryRefused,
    SpaceMemory,
)
from CortexOS.memory.verified_query import (
    CONFIRMED,
    EXAMPLE_CAP,
    PENDING,
    REVOKED,
    SQL_INVALID,
    SUPERSEDED,
    VerifiedQueryLibrary,
)

ROOT = Path(__file__).resolve().parents[2]
STEWARD = Actor("steward-alice", "steward")
VIEWER = Actor("viewer-bob", "viewer")
Q = "What was the total quantity_kg moved for SKU-00296 in June 2026?"
SQL = (
    "SELECT SUM(quantity_kg) AS total_kg, COUNT(*) AS txn_count FROM transactions "
    "WHERE sku = 'SKU-00296' AND timestamp >= '2026-06-01' AND timestamp < '2026-07-01'"
)
MODEL_SQL = "SELECT sku FROM inventory LIMIT 5"
EXAMPLE_SQL = "SELECT 1 AS n FROM transactions"


@pytest.fixture(autouse=True)
def _unscored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sm.SCORED_ROUND_ENV, raising=False)
    monkeypatch.delenv(sm.SCORED_PACKS_ENV, raising=False)


@pytest.fixture
def mem() -> SpaceMemory:
    return SpaceMemory()


@pytest.fixture
def lib(mem: SpaceMemory) -> VerifiedQueryLibrary:
    return VerifiedQueryLibrary(mem)


def _confirmed(lib: VerifiedQueryLibrary, space: str = "alpha", question: str = Q, sql: str = SQL):
    proposed = lib.propose(space_id=space, question=question, sql=sql, actor="analyst", source="ask:a1")
    return lib.confirm(space_id=space, query_id=proposed.id, steward=STEWARD)


def _ids(lib: VerifiedQueryLibrary, space: str = "alpha", **kw: Any) -> list[str]:
    return [ex.query.id for ex in lib.examples(space_id=space, actor="ask", **kw)]


# -- store ----------------------------------------------------------------------
def test_propose_stores_the_pair_until_a_steward_confirms(lib: VerifiedQueryLibrary) -> None:
    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    assert proposed.status == PENDING and proposed.entry_id is None
    assert proposed.question == Q and proposed.sql == SQL
    assert _ids(lib) == []


def test_propose_accepts_a_select_that_shares_no_literals_with_the_question(
    lib: VerifiedQueryLibrary,
) -> None:
    """Source-agnostic: no typed parameters have to line up with the question."""
    proposed = lib.propose(
        space_id="alpha",
        question="how many open orders",
        sql="SELECT COUNT(*) AS n FROM orders",
        actor="analyst",
        source="ask:a1",
    )
    assert proposed.status == PENDING


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("DROP TABLE transactions", SQL_INVALID),
        ("SELECT 1; SELECT 2", SQL_INVALID),
        ("", SQL_INVALID),
    ],
)
def test_propose_refuses_sql_that_cannot_be_a_verified_query(
    lib: VerifiedQueryLibrary, sql: str, code: str
) -> None:
    with pytest.raises(MemoryRefused) as ei:
        lib.propose(space_id="alpha", question=Q, sql=sql, actor="analyst", source="ask:a1")
    assert ei.value.code == code
    assert lib.memory.stamps(space_id="alpha")[-1].served_reason == code


def test_examples_are_not_chosen_by_phrasing(lib: VerifiedQueryLibrary) -> None:
    """Every live pair in the Space is an example. The question is not a lookup key."""
    query = _confirmed(lib)
    assert _ids(lib) == [query.id]
    other = _confirmed(
        lib,
        question="list cold rooms",
        sql="SELECT location_code FROM locations WHERE is_cold_storage = true",
    )
    assert _ids(lib) == [query.id, other.id]


def test_example_cap_bounds_the_prompt(lib: VerifiedQueryLibrary) -> None:
    made = [
        _confirmed(lib, question=f"question {i}", sql=f"SELECT {i} AS n FROM transactions")
        for i in range(EXAMPLE_CAP + 1)
    ]
    found = _ids(lib)
    assert len(found) == EXAMPLE_CAP
    assert set(found) < {q.id for q in made}


def test_confirmed_example_points_at_the_steward_solution(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    entry, _ = lib.memory.get(space_id="alpha", entry_id=query.entry_id or "", actor="t")
    assert set(entry.body) == {"question", "sql", "confirmed_by"}
    assert entry.validation == "steward_confirm"
    assert entry.source == f"verified_query:{query.id};ask:a1"
    (example,) = lib.examples(space_id="alpha", actor="ask")
    assert example.entry.id == entry.id
    assert example.query.sql == SQL


def test_reconfirming_a_question_supersedes_the_older_query(lib: VerifiedQueryLibrary) -> None:
    first = _confirmed(lib)
    second = _confirmed(lib, sql=SQL + " AND txn_type = 'OUT'")
    assert second.entry_id == first.entry_id
    by_id = {q.id: q.status for q in lib.list_queries(space_id="alpha", steward=STEWARD)}
    assert by_id == {first.id: SUPERSEDED, second.id: CONFIRMED}
    assert _ids(lib) == [second.id]


def test_steward_flows_are_steward_only_and_stamped(lib: VerifiedQueryLibrary) -> None:
    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    for call in (
        lambda: lib.confirm(space_id="alpha", query_id=proposed.id, steward=VIEWER),
        lambda: lib.revoke(space_id="alpha", query_id=proposed.id, steward=VIEWER),
        lambda: lib.list_queries(space_id="alpha", steward=VIEWER),
    ):
        with pytest.raises(MemoryRefused) as ei:
            call()
        assert ei.value.code == NOT_STEWARD
    confirmed = lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD)
    assert confirmed.confirmed_by == STEWARD.actor_id and confirmed.confirmed_at
    with pytest.raises(MemoryRefused) as ei:
        lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD)
    assert ei.value.code == vq.NOT_PENDING
    assert [q.id for q in lib.list_queries(space_id="alpha", steward=STEWARD, status=CONFIRMED)] == [
        proposed.id
    ]
    ops = [s.served_op for s in lib.memory.stamps(space_id="alpha")]
    assert ops == [
        "vq_propose", "refuse", "refuse", "refuse", "write", "vq_confirm", "refuse", "vq_list",
    ]


def test_library_has_no_hand_coded_phrasing_rules() -> None:
    source = (ROOT / "CortexOS" / "memory" / "verified_query.py").read_text(encoding="utf-8")
    for token in ("_STOPWORDS", "PARAM_PATTERNS", "intent_signature", "SKU-", "LOC-", "warehouse_code"):
        assert token not in source


# -- must-fail 1: unconfirmed ---------------------------------------------------
def test_must_fail_unconfirmed_query_never_reused(lib: VerifiedQueryLibrary) -> None:
    lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    assert _ids(lib) == []
    assert _ids(lib) == []
    assert lib.memory.read(space_id="alpha", kind="solution", actor="t")[0] == []


def test_must_fail_unconfirmed_query_never_reused_after_non_steward_confirm(
    lib: VerifiedQueryLibrary,
) -> None:
    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    with pytest.raises(MemoryRefused):
        lib.confirm(space_id="alpha", query_id=proposed.id, steward=VIEWER)
    assert _ids(lib) == []


def test_must_fail_unconfirmed_query_never_reused_on_a_real_result_check(
    lib: VerifiedQueryLibrary,
) -> None:
    """A real-result-checked C-MEM solution is not a steward confirm of the query."""
    lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    lib.memory.check_solution(
        space_id="alpha",
        question=Q,
        sql=SQL,
        expected_rows=[{"total_kg": 1.0, "txn_count": 1}],
        execute=lambda sql: [{"total_kg": 1.0, "txn_count": 1}],
        source="ask:a1",
        actor="analyst",
    )
    assert _ids(lib) == []


# -- must-fail 2: scored packs --------------------------------------------------
def test_must_fail_scored_pack_never_writes_or_reads_verified_queries(
    lib: VerifiedQueryLibrary, monkeypatch: pytest.MonkeyPatch
) -> None:
    for kwargs in (
        {"pack_id": "curated_ceo", "source": "ask:a1"},
        {"source": "bird_minidev:q12"},
        {"source": "ask:a1", "scored_pack_id": "pack_d"},
    ):
        with pytest.raises(MemoryRefused) as ei:
            lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", **kwargs)
        assert ei.value.code == SCORED_PACK_WRITE, kwargs

    proposed = lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a1")
    with pytest.raises(MemoryRefused) as ei:
        lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD, scored_pack_id="curated_ceo")
    assert ei.value.code == SCORED_PACK_WRITE
    lib.confirm(space_id="alpha", query_id=proposed.id, steward=STEWARD)

    assert _ids(lib, scored_pack_id="curated_ceo") == []
    assert lib.memory.stamps(space_id="alpha")[-1].served_reason == SCORED_ROUND_READ
    monkeypatch.setenv(sm.SCORED_ROUND_ENV, "1")
    assert _ids(lib) == []
    with pytest.raises(MemoryRefused) as ei:
        lib.propose(space_id="alpha", question=Q, sql=SQL, actor="analyst", source="ask:a2")
    assert ei.value.code == SCORED_ROUND_WRITE


# -- must-fail 3: Space isolation ----------------------------------------------
def test_must_fail_space_b_never_sees_space_a_verified_query(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib, space="alpha")
    pending = lib.propose(
        space_id="alpha",
        question="kg moved at LOC-003",
        sql="SELECT 1 FROM transactions WHERE location_id = 'LOC-003'",
        actor="analyst",
        source="ask:a2",
    )
    assert _ids(lib, space="beta") == []
    assert lib.list_queries(space_id="beta", steward=STEWARD) == []
    for call in (
        lambda: lib.confirm(space_id="beta", query_id=pending.id, steward=STEWARD),
        lambda: lib.revoke(space_id="beta", query_id=query.id, steward=STEWARD),
    ):
        with pytest.raises(MemoryRefused) as ei:
            call()
        assert ei.value.code == NOT_IN_SPACE
    assert _ids(lib, space="alpha") == [query.id]


# -- must-fail 4: revoked ---------------------------------------------------------
def test_must_fail_revoked_query_never_reused(lib: VerifiedQueryLibrary) -> None:
    query = _confirmed(lib)
    assert _ids(lib) == [query.id]
    revoked = lib.revoke(space_id="alpha", query_id=query.id, steward=STEWARD)
    assert revoked.status == REVOKED and revoked.revoked_by == STEWARD.actor_id
    assert _ids(lib) == []
    entries, _ = lib.memory.steward_view(space_id="alpha", steward=STEWARD, kind="solution")
    assert entries == [], "revoke removes the solution from memory"


def test_must_fail_revoked_query_never_reused_after_plain_solution_confirm(
    lib: VerifiedQueryLibrary,
) -> None:
    """A later plain C-MEM confirm of the same question does not revive the query."""
    query = _confirmed(lib)
    lib.revoke(space_id="alpha", query_id=query.id, steward=STEWARD)
    lib.memory.confirm_solution(space_id="alpha", question=Q, sql=SQL, steward=STEWARD, source="ask:a9")
    assert _ids(lib) == []


# -- must-fail 5: the generator writes the SQL -----------------------------------
def test_prompt_carries_examples_for_an_unrelated_question() -> None:
    from CortexOS.dms.verified_query_ask import question_with_examples
    from packs.dms.generative.sql_generator import _build_prompt

    asked = question_with_examples(
        "something else entirely",
        [{"id": "vq_1", "question": Q, "sql": SQL}],
    )
    prompt = _build_prompt(asked, {"tables": {}}, prior_violations=None)
    assert SQL in prompt and Q in prompt and "vq_1" in prompt
    assert "something else entirely" in prompt
    assert "not the query to execute" in prompt


def test_must_fail_stored_example_is_not_the_generated_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model is called. Its SQL is the candidate. The stored example is not."""
    from CortexOS.dms.verified_query_ask import question_with_examples
    from CortexOS.integrations import freeroute
    from packs.dms.generative import sql_generator

    monkeypatch.setenv("DMS_L2_ENABLED", "1")
    monkeypatch.setattr(sql_generator, "is_configured", lambda: True)
    sent: list[dict[str, Any]] = []

    def mocked_complete(task: str, messages: list[dict[str, Any]], **kwargs: Any) -> freeroute.Completion:
        sent.append({"task": task, "messages": messages})
        return freeroute.Completion(ok=True, text=MODEL_SQL)

    monkeypatch.setattr(freeroute, "complete", mocked_complete)
    examples = [{"id": "vq_x", "question": "q", "sql": EXAMPLE_SQL}]
    cands = sql_generator.generate_candidates(
        question_with_examples("unrelated question", examples),
        {
            "tables": {"inventory": {"columns": ["sku"]}},
            "verified_examples": examples,
        },
    )
    assert cands, "generator returned no SQL"
    assert "inventory" in cands[0].lower()
    assert "transactions" not in cands[0].lower()
    assert sent and sent[0]["task"] == "gen-ask-sql"
    assert EXAMPLE_SQL in json.dumps(sent)


# -- module boundary (AST: function-level imports too) -------------------------
FORBIDDEN = (
    "packs", "duckdb", "litellm", "httpx", "CortexOS.integrations", "CortexOS.crew",
    "CortexOS.nlp", "CortexOS.execution", "CortexOS.dms", "CortexOS.api",
)


def _forbidden_imports(source: str) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        names = (
            [a.name for a in node.names] if isinstance(node, ast.Import)
            else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
        )
        hits += [n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN)]
    return hits


def test_library_never_imports_a_model_pack_db_driver_or_answer_plane() -> None:
    source = (ROOT / "CortexOS" / "memory" / "verified_query.py").read_text(encoding="utf-8")
    assert _forbidden_imports(source) == []


@pytest.mark.parametrize(
    "line",
    ["import duckdb", "from packs.dms import x", "from CortexOS.integrations import freeroute"],
)
def test_must_fail_library_boundary_check_catches_function_level_imports(line: str) -> None:
    assert _forbidden_imports(f"def f():\n    {line}\n") != []
