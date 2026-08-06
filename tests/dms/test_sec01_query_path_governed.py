"""SEC-01 — POST /dms/query runs under a manifest, like /v1/contract/* always did.

``CortexOS/execution/manifest.py`` is a rigorous fail-closed control — 34 raise
sites and a hostile corpus — but it was optional on the path customers actually
use. ``answer_engine.answer`` took ``verified: VerifiedManifest | None``, and
``query_service.answer_question`` never passed one, so the product path executed
SQL on a bare connection with no enforcement at all.

``verified=None`` no longer means "skip the manifest". It means "no session
bound a grant", and the local warehouse mints a narrow self-issued one, so every
statement reaches ``enforce_manifest`` either way.

Assertions are on the envelope the caller receives (CLAUDE.md §8, R-0001), not
on generated SQL.
"""

from __future__ import annotations

import pytest

from CortexOS.dms import answer_engine
from CortexOS.dms.answer_engine import answer, clear_session
from CortexOS.execution.manifest import LOCAL_ISSUER_KID, local_manifest

GRANTED_QUESTION = "Top 5 selling SKUs by revenue"


@pytest.fixture
def session(request) -> str:
    sid = f"sec01-{request.node.name}"
    clear_session(sid)
    return sid


# ── the mint itself ──────────────────────────────────────────────────────────
def test_local_manifest_grants_exactly_the_tables_it_was_given() -> None:
    """It can never authorise a relation the caller did not name."""
    verified = local_manifest(
        session_id="s", tables={"inventory", "alerts"}, allowed_paths=["/tmp/x"]
    )

    assert set(verified.manifest.row_predicates) == {"inventory", "alerts"}
    assert verified.issuer_kid == LOCAL_ISSUER_KID


def test_local_manifest_refuses_an_empty_grant() -> None:
    """A grant of nothing reads like a no-op and is really a total refusal."""
    with pytest.raises(ValueError, match="at least one table"):
        local_manifest(session_id="s", tables=[], allowed_paths=["/tmp/x"])


def test_local_grant_is_marked_so_it_cannot_pass_as_a_signed_one() -> None:
    """A weaker claim has to be visibly weaker (R-0011)."""
    verified = local_manifest(
        session_id="s", tables={"inventory"}, allowed_paths=["/tmp/x"]
    )

    assert verified.manifest.issuer_key_id == LOCAL_ISSUER_KID
    assert verified.manifest.signature == ""


# ── the product path ─────────────────────────────────────────────────────────
def test_unbound_query_still_answers_and_discloses_the_local_grant(
    session: str,
) -> None:
    """R-0005 control: governing the path must not refuse legitimate work.

    And the degradation is visible — a self-issued grant that looked identical
    to a signed one on the wire would be a silent fallback (R-0011).
    """
    result = answer(GRANTED_QUESTION, session_id=session)

    assert result["rows"], "a governed demo question must still answer"
    assert result["query_plan"]["grant_kind"] == "local-self-issued"


def test_bound_session_is_not_overridden_by_the_local_grant(
    session: str,
) -> None:
    """A real manifest always wins; the mint is only for callers that have none."""
    verified = local_manifest(
        session_id=session, tables={"transactions"}, allowed_paths=["/tmp/x"]
    )

    result = answer(GRANTED_QUESTION, session_id=session, verified=verified)

    assert result["query_plan"]["grant_kind"] == "session"


def test_query_outside_the_grant_is_refused_on_the_envelope(
    session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrow the grant and the same question must stop returning a number.

    This is the assertion that proves enforcement is live on the unbound path:
    before SEC-01 the grant was irrelevant because nothing consulted it.
    """
    monkeypatch.setattr(answer_engine, "_local_grant_tables", lambda: frozenset({"alerts"}))

    result = answer(GRANTED_QUESTION, session_id=session)

    assert result["rows"] == []
    assert result.get("sql_used") is None
    assert result["badge"] in ("abstain", "refused")


def test_refusal_names_a_manifest_refusal_type(
    session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same refusal vocabulary as /v1/contract/submit, visible to the caller."""
    monkeypatch.setattr(answer_engine, "_local_grant_tables", lambda: frozenset({"alerts"}))

    result = answer(GRANTED_QUESTION, session_id=session)

    surfaced = (result.get("assumptions") or "") + " ".join(
        result.get("violations_blocked") or []
    )
    assert "transactions" in surfaced.lower() or "PathNotAllowed" in surfaced


# ── the executor seam ────────────────────────────────────────────────────────
def test_no_answer_reaches_the_caller_without_passing_the_enforcer(
    session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of SEC-01: enforce_manifest is on the path, not beside it.

    Spying on the enforcer is a structural claim, so it sits alongside the
    envelope assertions above rather than replacing them — but it is the one
    that fails loudly if someone reintroduces a bare-connection shortcut.
    """
    import CortexOS.execution.submit as submit_mod

    seen: list[str] = []
    real = submit_mod.enforce_manifest

    def _spy(sql: str, verified):
        seen.append(sql)
        return real(sql, verified)

    monkeypatch.setattr(submit_mod, "enforce_manifest", _spy)

    result = answer(GRANTED_QUESTION, session_id=session)

    assert result["rows"]
    assert seen, "the answer executed without going through enforce_manifest"
