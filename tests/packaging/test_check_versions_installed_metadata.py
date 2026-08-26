"""CONTRACT-01 gap 4 - installed metadata must not be able to lie about the contract.

`pip show cortex-contract` reported 1.1.0 against a tree whose CONTRACT_VERSION
was 1.3.0, and `scripts/check_versions.py` printed OK and exited 0. Nothing in
the repo compared the two, so the number a consumer resolves a pin against and
the number the source declares were free to drift.

That is the CONTRACT-01 hazard one step upstream. Install a wheel whose metadata
says one thing while the working tree says another and the bare
`cortex_contract` name starts resolving to site-packages - inside
`canonical_manifest_bytes`, the one function whose bytes DMS signs and Cortex
verifies, where a mismatch presents as a signature failure rather than a
version-skew bug.

The comparator is exercised in both directions here on purpose. On this machine
the real check is *red* - correctly, that is the open defect - so a test that
only ever saw the live environment could not tell a working comparator from one
that is stuck. These call it with constructed inputs instead.
"""

from __future__ import annotations

from scripts.check_versions import (
    REINSTALL,
    contract_dist_versions,
    contract_metadata_errors,
    imported_contract,
)

SOURCE = "1.3.0"
MATCHING = {"cortex-contract": SOURCE}


def test_agreeing_metadata_produces_no_errors() -> None:
    """The green direction. Without this the comparator could reject everything."""
    assert contract_metadata_errors(SOURCE, MATCHING, SOURCE, "/tree/cortex_contract/__init__.py") == []


def test_stale_dist_metadata_is_an_error() -> None:
    """The exact live defect, as data: dist-info 1.1.0 against a 1.3.0 tree."""
    errors = contract_metadata_errors(
        SOURCE, {"cortex-contract": "1.1.0"}, SOURCE, "/tree/cortex_contract/__init__.py"
    )

    assert len(errors) == 1, errors
    assert "1.1.0" in errors[0] and SOURCE in errors[0]
    assert REINSTALL in errors[0], (
        "a failure that does not say how to fix it makes the next person guess, "
        "and guessing here means running pip until the number changes"
    )


def test_a_bundling_distribution_is_not_held_to_the_contract_version() -> None:
    """``netie`` ships the same module, and its version is the ENGINE version.

    Comparing it here would assert engine == contract, which check 3 of this
    same script exists to forbid - 2.5.0 tracks G-gates, 1.3.0 tracks the wire,
    and they move independently by hard invariant. A gate that contradicts its
    own sibling check reds CI on a correct tree, which is R-0005: a control that
    refuses legitimate work is a failure.

    The staleness this reaches for is caught by the loaded-module check instead,
    on evidence from the import system rather than from a version number that
    was never claiming to be the contract's.
    """
    errors = contract_metadata_errors(
        SOURCE, {"cortex-contract": SOURCE, "netie": "2.5.0"}, SOURCE, "/x/__init__.py"
    )

    assert errors == [], errors


def test_the_contract_distribution_itself_is_still_checked() -> None:
    """Excluding the bundler must not excuse the dist the pin actually resolves."""
    errors = contract_metadata_errors(
        SOURCE, {"cortex-contract": "1.1.0"}, SOURCE, "/x/__init__.py"
    )

    assert len(errors) == 1, errors
    assert "cortex-contract" in errors[0]
    assert "1.1.0" in errors[0]


def test_only_the_contract_distribution_is_collected() -> None:
    """The filter is in the lookup, not only in the comparator.

    Asserted against this real environment: whatever ships cortex_contract here,
    the collected mapping never carries a bundling dist, so no future edit to
    the comparator can reintroduce the coupling.
    """
    from scripts.check_versions import contract_dist_versions

    assert set(contract_dist_versions()) <= {"cortex-contract"}


def test_a_stale_copy_shadowing_the_source_is_an_error() -> None:
    """Dist metadata can agree while the module that loads is a different copy.

    This is the half that survives a correct `pip install`: the wheel installs
    cleanly, its metadata matches, and the module resolved at runtime is still
    an older file earlier on sys.path.
    """
    errors = contract_metadata_errors(
        SOURCE, MATCHING, "1.1.0", "/site-packages/cortex_contract/__init__.py"
    )

    assert len(errors) == 1, errors
    assert "site-packages" in errors[0]
    assert "1.1.0" in errors[0]


def test_a_contract_that_will_not_import_is_an_error() -> None:
    """No importable contract means no consumer pin resolves. Not a pass."""
    errors = contract_metadata_errors(SOURCE, {}, None, None)

    assert len(errors) == 1, errors
    assert REINSTALL in errors[0]


def test_no_installed_distribution_is_reported_not_swallowed() -> None:
    """R-0011 boundary.

    CI installs the engine only, so no distribution ships `cortex_contract`
    under its own name and there is genuinely nothing to compare. That must not
    be an error - but the loaded module is still checked, so the run is not
    silently asserting nothing.
    """
    assert contract_metadata_errors(SOURCE, {}, SOURCE, "/tree/__init__.py") == []
    assert contract_metadata_errors(SOURCE, {}, "1.1.0", "/tree/__init__.py") != []


def test_the_lookup_helpers_answer_about_this_environment() -> None:
    """Guard the guard: a comparator fed nothing agrees with everything.

    The pure function above proves the comparison works. This proves the two
    functions that feed it can actually see the interpreter they run in, so a
    broken lookup cannot turn the check green by starving it.
    """
    version, file = imported_contract()

    assert version is not None, "cortex_contract is not importable in this environment"
    assert file and file.endswith("__init__.py")

    dists = contract_dist_versions()
    assert all(isinstance(v, str) and v for v in dists.values()), dists
