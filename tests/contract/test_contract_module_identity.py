"""CONTRACT-01 — one file must not be two module identities.

``cortex_contract`` (the distributed name, what DMS pins) and
``packages.cortex_contract`` (the in-tree path) both resolved to
``packages/cortex_contract/execution.py`` while producing *different classes*:

    cortex_contract.execution.Manifest is packages.cortex_contract.execution.Manifest
    -> False

That matters in exactly one place, and it is the worst place. ``
canonical_manifest_bytes`` branches on ``isinstance(manifest, Manifest)``:
model instances take ``model_dump(mode="json")``, anything else falls to
``dict(manifest)``. Under two identities the isinstance check silently returns
False, so a Manifest built with one spelling is canonicalised through the
Mapping branch meant for raw dicts.

The bytes agreed anyway — by luck. Every Manifest field is ``str``,
``list[str]``, ``dict[str, str]`` or ``None``, and for those two branches
coincide. Add one ``datetime``, ``UUID`` or nested model and they diverge:
``dict(model)`` keeps a ``datetime`` object, ``model_dump(mode="json")``
produces an ISO string.

DMS signs those bytes and Cortex verifies them, so the divergence would
present as a signature failure — a crypto bug, not a serialisation bug — and
CLAUDE.md §4 already names this the most dangerous function in the repo.
"""

from __future__ import annotations

import datetime as dt
import sys

import pytest
from cortex_contract.execution import Manifest, canonical_manifest_bytes
from pydantic import BaseModel

NOW = dt.datetime(2026, 8, 6, 12, 0, 0, tzinfo=dt.timezone.utc)


def _manifest(**over: object) -> Manifest:
    base: dict[str, object] = {
        "session_id": "sess-1",
        "org_id": "acme",
        "pool_id": "pool-a",
        "issuer_key_id": "int-1",
        "allowed_paths": ["/data/pool/acme/**"],
        "row_predicates": {"orders": "TRUE"},
        "issued_at": NOW.isoformat(),
        "expires_at": NOW.isoformat(),
        "signature": "sig",
    }
    base.update(over)
    return Manifest(**base)  # type: ignore[arg-type]


def test_only_one_spelling_of_the_contract_is_imported() -> None:
    """The whole defect in one assertion.

    Importing the engine must not leave both names loaded. Two entries here is
    two classes, and two classes is the isinstance branch below going wrong.
    """
    import CortexOS.execution.manifest  # noqa: F401  — the engine's entry point
    import CortexOS.execution.submit  # noqa: F401

    both = "cortex_contract" in sys.modules and "packages.cortex_contract" in sys.modules
    assert not both, (
        "both 'cortex_contract' and 'packages.cortex_contract' are in sys.modules — "
        "one file, two classes. Import the distributed name everywhere."
    )


def test_isinstance_holds_for_a_manifest_the_engine_built() -> None:
    """If this is False, canonicalisation silently takes the Mapping branch."""
    from CortexOS.execution import manifest as engine_manifest

    built = _manifest()
    assert isinstance(built, engine_manifest.Manifest)


def test_canonical_bytes_agree_across_engine_call_sites() -> None:
    """Both production call sites must canonicalise identically.

    ``drillthrough`` imports the function inside its functions, so this reaches
    for the same spelling those local imports use — that spelling *is* the
    thing under test.
    """
    import importlib

    drill_mod = importlib.import_module(_drillthrough_contract_module())
    from CortexOS.execution.manifest import canonical_manifest_bytes as manifest_bytes

    built = _manifest()
    assert (
        drill_mod.canonical_manifest_bytes(built)
        == manifest_bytes(built)
        == canonical_manifest_bytes(built)
    )


def _drillthrough_contract_module() -> str:
    """Which contract spelling drillthrough.py actually imports."""
    import pathlib
    import re

    src = pathlib.Path("CortexOS/execution/drillthrough.py").read_text(encoding="utf-8")
    hit = re.search(r"from ([\w.]*cortex_contract)\.execution import", src)
    assert hit, "drillthrough.py no longer imports the contract — update this test"
    return f"{hit.group(1)}.execution"


def test_non_string_field_canonicalises_through_the_model_branch() -> None:
    """The latent hazard, made explicit.

    Today every Manifest field is a string, so the two branches coincide and the
    divergence is invisible. This pins the *rule* rather than that accident: a
    model must canonicalise through ``model_dump(mode="json")``, so a field that
    is not already a string is serialised the JSON way.

    Without it, the first non-string field added to the contract silently
    changes the signed bytes.
    """

    class StampedManifest(Manifest):
        stamped_at: dt.datetime = NOW

    built = StampedManifest(
        session_id="s",
        org_id="o",
        pool_id="p",
        issuer_key_id="k",
        allowed_paths=["/x/**"],
        row_predicates={"t": "TRUE"},
        issued_at=NOW.isoformat(),
        expires_at=NOW.isoformat(),
        signature="sig",
    )

    raw = canonical_manifest_bytes(built).decode("utf-8")
    assert "2026-08-06T12:00:00Z" in raw, (
        "a datetime field did not canonicalise as an ISO string — this went "
        "through dict(model), not model_dump(mode='json')"
    )


def test_mapping_branch_still_serves_real_mappings() -> None:
    """R-0005: tightening the model branch must not refuse legitimate dict input."""
    as_dict = _manifest().model_dump(mode="json")

    assert canonical_manifest_bytes(as_dict) == canonical_manifest_bytes(_manifest())


def test_dict_and_json_dump_differ_for_non_strings() -> None:
    """The mechanism, asserted directly, so the reason above cannot rot.

    If pydantic ever made these identical, the tests here would still pass while
    testing nothing. This fails loudly if the premise stops holding.
    """

    class Demo(BaseModel):
        when: dt.datetime

    demo = Demo(when=NOW)
    assert dict(demo) != demo.model_dump(mode="json")
    with pytest.raises(TypeError):
        import json

        json.dumps(dict(demo))
