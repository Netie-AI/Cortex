"""Cortex must reproduce every canonicalisation vector, byte for byte.

``contract/testvectors/manifest_canonical.jsonl`` is the shared artifact: DMS
signs the bytes ``canonical_manifest_bytes()`` produces and Cortex verifies them,
so the rule has to be one implementation with a proof of agreement rather than
two readings of a document. A one-byte divergence makes every manifest DMS signs
fail verification, and it presents as a signature bug.

Regenerate with ``python scripts/gen_manifest_testvectors.py``. Regenerating to
make this file pass is only correct if the canonicalisation change was
deliberate — it is a contract major and a coordinated consumer release, not a
commit. See ``contract/compat.yaml``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from cortex_contract.execution import Manifest, canonical_manifest_bytes

VECTORS_PATH = Path(__file__).resolve().parents[2] / "contract" / "testvectors" / "manifest_canonical.jsonl"


def _load() -> list[dict]:
    if not VECTORS_PATH.is_file():
        pytest.fail(f"missing {VECTORS_PATH}; run scripts/gen_manifest_testvectors.py")
    return [
        json.loads(line)
        for line in VECTORS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


VECTORS = _load()


def test_enough_vectors_to_be_worth_trusting() -> None:
    assert len(VECTORS) >= 20, f"only {len(VECTORS)} vectors"
    assert len({v["name"] for v in VECTORS}) == len(VECTORS), "duplicate vector names"


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_canonical_bytes_match_the_vector(vector: dict) -> None:
    manifest = Manifest.model_validate(vector["manifest"])
    produced = canonical_manifest_bytes(manifest)

    assert produced.decode("utf-8") == vector["canonical_utf8"], vector["why"]
    assert hashlib.sha256(produced).hexdigest() == vector["canonical_sha256"]
    assert len(produced) == vector["canonical_length"]


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_signature_is_never_part_of_the_signed_bytes(vector: dict) -> None:
    """Otherwise signing would have to sign its own output."""
    manifest = Manifest.model_validate(vector["manifest"])
    mutated = manifest.model_copy(update={"signature": "a-completely-different-signature"})
    assert canonical_manifest_bytes(mutated) == canonical_manifest_bytes(manifest)


def _by_name() -> dict[str, dict]:
    return {v["name"]: v for v in VECTORS}


def test_absent_empty_and_null_all_prune_to_the_same_bytes() -> None:
    """The rule that lets a contract minor add fields without breaking old signers."""
    names = _by_name()
    baseline = names["minimal"]["canonical_sha256"]
    for name in ("empty_predicates_dropped", "empty_paths_dropped", "nulls_dropped"):
        assert names[name]["canonical_sha256"] == baseline, name


def test_an_empty_string_is_kept() -> None:
    """The one exception. A reimplementation that prunes falsy values fails here."""
    names = _by_name()
    assert names["empty_string_kept"]["canonical_sha256"] != names["minimal"]["canonical_sha256"]
    assert '"space_id":""' in names["empty_string_kept"]["canonical_utf8"]


def test_list_order_is_significant_but_map_order_is_not() -> None:
    names = _by_name()
    assert (
        names["predicate_key_order_reversed"]["canonical_sha256"]
        == names["full_1_1_0"]["canonical_sha256"]
    ), "predicate keys must sort, so insertion order cannot matter"
    assert (
        names["path_order_is_significant"]["canonical_sha256"]
        != names["full_1_1_0"]["canonical_sha256"]
    ), "allowed_paths is a list; reordering it changes the grant and must change the bytes"


def test_non_ascii_stays_literal_utf8() -> None:
    """ensure_ascii=True would escape these and silently change every digest."""
    names = _by_name()
    assert "café" in names["unicode_path_latin"]["canonical_utf8"]
    assert "仓库" in names["unicode_path_cjk"]["canonical_utf8"]
    assert "\U0001f600" in names["unicode_path_astral"]["canonical_utf8"]
    assert "\\u" not in names["unicode_path_cjk"]["canonical_utf8"]


def test_keys_are_sorted_at_every_level() -> None:
    for vector in VECTORS:
        payload = json.loads(vector["canonical_utf8"])
        assert list(payload) == sorted(payload), vector["name"]
        predicates = payload.get("row_predicates")
        if isinstance(predicates, dict):
            assert list(predicates) == sorted(predicates), vector["name"]


def test_canonical_form_carries_no_whitespace() -> None:
    for vector in VECTORS:
        text = vector["canonical_utf8"]
        assert ", " not in text and ": " not in text, vector["name"]
        assert not text.endswith("\n"), vector["name"]
