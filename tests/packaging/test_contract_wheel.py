"""EPIC-002 — the built cortex-contract wheel is the same contract.

DMS pins ``cortex-contract`` as a dependency so the models and the
canonicalisation rule are *the same code* as the engine's. That promise is
only real if the artifact we hand out reproduces ``canonical_manifest_bytes``
byte-for-byte — a wheel that drifts from the repo would present as a
signature bug on every manifest DMS signs.

This test builds the wheel with pip, imports it in a clean subprocess whose
``cortex_contract`` resolves from the unpacked artifact (asserted, not
assumed — the repo may also be importable via an editable install), and
replays every frozen vector in ``contract/testvectors/manifest_canonical.jsonl``
against it. PyPI publishing stays a founder step; this proves the artifact is
publishable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from cortex_contract.version import CONTRACT_VERSION

REPO_ROOT = Path(__file__).resolve().parents[2]
PKG_DIR = REPO_ROOT / "packages" / "cortex_contract"
VECTORS_PATH = REPO_ROOT / "contract" / "testvectors" / "manifest_canonical.jsonl"

# Runs inside the built artifact only: argv[1] = unpacked wheel dir,
# argv[2] = vectors path, argv[3] = expected contract version.
_SUBPROCESS_PROBE = """
import hashlib, json, sys
from pathlib import Path

site = Path(sys.argv[1]).resolve()
import cortex_contract
got = Path(cortex_contract.__file__).resolve()
assert str(got).startswith(str(site)), (
    f"cortex_contract resolved from {got}, not the built wheel at {site}"
)

from cortex_contract.version import CONTRACT_VERSION
assert CONTRACT_VERSION == sys.argv[3], (CONTRACT_VERSION, sys.argv[3])

from cortex_contract.execution import Manifest, canonical_manifest_bytes

checked = 0
for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    vector = json.loads(line)
    produced = canonical_manifest_bytes(Manifest.model_validate(vector["manifest"]))
    assert produced.decode("utf-8") == vector["canonical_utf8"], vector["name"]
    assert hashlib.sha256(produced).hexdigest() == vector["canonical_sha256"], vector["name"]
    assert len(produced) == vector["canonical_length"], vector["name"]
    checked += 1
assert checked >= 20, f"only {checked} vectors replayed"
print(f"wheel-ok {checked}")
"""


@pytest.fixture(scope="module")
def built_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The wheel these assertions run against.

    ``CORTEX_CONTRACT_WHEEL`` points them at an ALREADY-BUILT artifact instead
    of a fresh one. The release workflow sets it to the exact file it is about
    to attach to the GitHub Release, so the wheel a consumer downloads is the
    wheel that was proved - not a rebuild that merely came from the same tree.
    """
    prebuilt = (os.environ.get("CORTEX_CONTRACT_WHEEL") or "").strip()
    if prebuilt:
        wheel = Path(prebuilt)
        assert wheel.is_file(), f"CORTEX_CONTRACT_WHEEL={prebuilt!r} is not a file"
        assert wheel.suffix == ".whl", f"CORTEX_CONTRACT_WHEEL={prebuilt!r} is not a wheel"
        return wheel
    out = tmp_path_factory.mktemp("contract_wheel")
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(PKG_DIR), "--no-deps", "-w", str(out)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"pip wheel failed:\n{proc.stdout}\n{proc.stderr}"
    wheels = sorted(out.glob("cortex_contract-*.whl"))
    assert len(wheels) == 1, [w.name for w in wheels]
    return wheels[0]


def test_wheel_filename_carries_the_contract_version(built_wheel: Path) -> None:
    """Artifact-level twin of scripts/check_versions.py."""
    assert built_wheel.name.startswith(f"cortex_contract-{CONTRACT_VERSION}-")


def test_wheel_ships_the_module_not_the_repo_layout(built_wheel: Path) -> None:
    with zipfile.ZipFile(built_wheel) as zf:
        names = zf.namelist()
    assert any(n == "cortex_contract/__init__.py" for n in names), names[:10]
    assert not any(n.startswith("packages/") for n in names), (
        "wheel must ship top-level cortex_contract, never the packages/ spelling"
    )


def test_artifact_reproduces_every_canonical_vector(
    built_wheel: Path, tmp_path: Path
) -> None:
    site = tmp_path / "site"
    with zipfile.ZipFile(built_wheel) as zf:
        zf.extractall(site)
    probe = tmp_path / "probe.py"
    probe.write_text(_SUBPROCESS_PROBE, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(probe), str(site), str(VECTORS_PATH), CONTRACT_VERSION],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=tmp_path,  # repo root must not be importable via cwd
        env={
            **os.environ,
            "PYTHONPATH": str(site),
            "PYTHONSAFEPATH": "1",  # no cwd/script-dir on sys.path (3.11+)
        },
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    assert "wheel-ok" in proc.stdout


def test_release_workflow_verifies_the_wheel_it_attaches() -> None:
    """The proof is only worth having if the release actually runs it.

    release.yml builds the cortex-contract wheel into dist_release/ and attaches
    it to the GitHub Release. Without this step that artifact ships unproved, so
    deleting the step must fail here rather than silently in a consumer's build.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "CORTEX_CONTRACT_WHEEL" in workflow, (
        "release.yml no longer points the canonical-vector proof at the wheel it ships"
    )
    assert "tests/packaging/test_contract_wheel.py" in workflow, (
        "release.yml no longer runs the contract-wheel proof"
    )
    build_at = workflow.index("python -m build --wheel packages/cortex_contract")
    verify_at = workflow.index("CORTEX_CONTRACT_WHEEL")
    assert build_at < verify_at, "the wheel must be verified after it is built, not before"
