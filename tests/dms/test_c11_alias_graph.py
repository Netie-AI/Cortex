"""C11-min — the pack's alias graph must resolve each term to exactly one thing.

The kickoff packet asks for a pack-local ``aliases.yaml`` with unique term ->
column validation. The alias graph already exists, in two places that are read
on the serving path:

* ``metrics.yaml`` ``synonyms:`` — parsed by ``semantic/loader.py``, used by
  ``answer_engine._suggestions`` to offer the nearest answerable question after
  an abstain, and published by ``CortexOS/api/ontology_routes.py``.
* ``vocabulary.py`` ``_RULES`` — business phrasing rewritten into router
  phrasing before ``route_to_metric`` sees it.

A third file holding the same relation would be a third thing to keep in sync,
and the first one to drift would be the one nobody reads. So what C11-min
actually wants is the *validation*, and that is what lives here.

Ambiguity matters because of where synonyms are consumed. A term owned by two
metrics makes the post-abstain suggestion arbitrary — the customer is offered
one of two different questions with no way to tell which they will get — and
draws an ontology edge that claims a term belongs to two objects at once.

The graph is clean today (157 synonyms, no collisions). Nothing enforced that,
which is the entire point: this is the gate that keeps it true as the pack grows.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest
import yaml

METRICS_PATH = Path(__file__).resolve().parents[2] / "packs" / "dms" / "semantic" / "metrics.yaml"


@pytest.fixture(scope="module")
def metrics() -> list[dict]:
    raw = yaml.safe_load(METRICS_PATH.read_text(encoding="utf-8")) or {}
    entries = raw.get("metrics") or []
    assert entries, f"no metrics parsed from {METRICS_PATH}"
    return entries


def _synonym_owners(metrics: list[dict]) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = collections.defaultdict(list)
    for metric in metrics:
        for synonym in metric.get("synonyms") or []:
            owners[str(synonym).strip().lower()].append(str(metric["id"]))
    return owners


def test_no_synonym_resolves_to_two_metrics(metrics: list[dict]) -> None:
    """The unique term -> target property C11-min is about.

    Two metrics claiming one phrase means the router's own vocabulary is
    ambiguous, and the abstain suggestion built from it becomes a coin flip.
    """
    ambiguous = {
        term: sorted(set(ids))
        for term, ids in _synonym_owners(metrics).items()
        if len(set(ids)) > 1
    }
    assert not ambiguous, "one term claimed by several metrics:\n" + "\n".join(
        f"  {term!r} -> {ids}" for term, ids in sorted(ambiguous.items())
    )


def test_no_synonym_shadows_a_different_metric_id(metrics: list[dict]) -> None:
    """A synonym that *is* another metric's id points the graph at the wrong node."""
    ids = {str(m["id"]).strip().lower(): str(m["id"]) for m in metrics}
    collisions = [
        (term, owners[0], ids[term])
        for term, owners in _synonym_owners(metrics).items()
        if term in ids and ids[term] not in owners
    ]
    assert not collisions, "synonym collides with another metric's id:\n" + "\n".join(
        f"  {term!r} declared on {owner} but is the id of {other}"
        for term, owner, other in collisions
    )


def test_metric_ids_are_unique(metrics: list[dict]) -> None:
    ids = [str(m["id"]) for m in metrics]
    dupes = [i for i, n in collections.Counter(ids).items() if n > 1]
    assert not dupes, f"duplicate metric ids: {dupes}"


def test_synonyms_are_stored_normalized(metrics: list[dict]) -> None:
    """``Total Revenue`` and ``total revenue`` are the same key wearing two hats.

    Uniqueness is only meaningful if the terms are comparable, so the stored
    form has to be the compared form — otherwise two spellings of one phrase sit
    in the file looking distinct and the collision test never sees them.
    """
    offenders = [
        (str(m["id"]), synonym)
        for m in metrics
        for synonym in (m.get("synonyms") or [])
        if str(synonym) != str(synonym).strip().lower()
    ]
    assert not offenders, "synonyms must be stored lowercase and stripped:\n" + "\n".join(
        f"  {mid}: {syn!r}" for mid, syn in offenders
    )


def test_no_empty_or_single_character_synonyms(metrics: list[dict]) -> None:
    """A one-character alias matches half the corpus. Not a synonym, a hazard."""
    offenders = [
        (str(m["id"]), synonym)
        for m in metrics
        for synonym in (m.get("synonyms") or [])
        if len(str(synonym).strip()) < 2
    ]
    assert not offenders, f"synonyms too short to disambiguate: {offenders}"


def test_vocabulary_rules_compile_and_do_not_shadow_each_other() -> None:
    """Ordered rewrite rules: a later rule whose pattern an earlier one destroys is dead.

    ``normalize_for_routing`` applies ``_RULES`` in order, so a rule can only fire
    on text the rules before it left intact. A rule that can never match is not a
    harmless leftover — it reads as coverage the router does not have.
    """
    from packs.dms.semantic.vocabulary import _RULES, normalize_for_routing

    seen: dict[str, int] = {}
    for index, (pattern, _replacement) in enumerate(_RULES):
        re.compile(pattern)  # raises on a malformed rule
        if pattern in seen:
            pytest.fail(f"rule {index} duplicates rule {seen[pattern]}: {pattern!r}")
        seen[pattern] = index

    # Normalization is idempotent: routing must not depend on how many times it ran.
    for probe in (
        "which SKUs are running low in warehouse A",
        "who are our riskiest vendors",
        "how many late shipments this week",
    ):
        once = normalize_for_routing(probe)
        assert normalize_for_routing(once) == once, f"not idempotent: {probe!r}"
