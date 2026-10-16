"""HX-01: the provider key env names Cortex scrubs mirror OpenVault's catalogue.

OpenVault owns the catalogue; Cortex holds only a name list pinned to an
OpenVault commit. The OpenVault comparison needs a checkout: set
``OPENVAULT_SRC`` to its repo root, or keep it as a sibling ``../OpenVault``.
CI checks out OpenVault at ``OV_PIN`` and sets ``OPENVAULT_SRC`` (ci.yml), and
under CI a missing checkout fails instead of skipping, so the comparison
cannot pass by not running. Outside CI, with no checkout, it skips and only the
Cortex-side checks run. OpenVault has no endpoint that returns these names with
their aliases.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from CortexOS.integrations.harness import ov_key_envs
from CortexOS.integrations.harness import secrets as harness_secrets

ROOT = Path(__file__).resolve().parents[2]


def _literal_names(source: str, name: str, consts: dict[str, str]) -> set[str]:
    """String names in OpenVault's ``name = {...}`` / ``frozenset({...})`` literal."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        else:
            continue
        if not (isinstance(target, ast.Name) and target.id == name) or value is None:
            continue
        if isinstance(value, ast.Call) and value.args:
            value = value.args[0]
        if isinstance(value, ast.Dict):
            elts = list(value.values) if name == "PROVIDER_TO_ENV" else [k for k in value.keys if k is not None]
        elif isinstance(value, (ast.Set, ast.List, ast.Tuple)):
            elts = list(value.elts)
        else:
            break
        out: set[str] = set()
        for elt in elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                out.add(elt.value)
            elif isinstance(elt, ast.Name) and elt.id in consts:
                out.add(consts[elt.id])
            else:
                raise AssertionError(f"OpenVault {name} holds a non-literal entry: {ast.dump(elt)}")
        return out
    raise AssertionError(f"OpenVault {name} not found; did it move?")


def _str_consts(source: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            out[node.targets[0].id] = node.value.value
    return out


def derive_ov_key_envs(read: dict[str, str]) -> set[str]:
    """OpenVault's ``known_env_keys() - NON_SECRET_ENV_KEYS`` from source text, never imported."""
    consts: dict[str, str] = {}
    for text in read.values():
        consts.update(_str_consts(text))
    sets = {
        name: _literal_names(read[path], name, consts) for name, path in ov_key_envs.OV_SOURCES.items()
    }
    known = sets["PROVIDER_TO_ENV"] | sets["ENV_KEY_TO_PROVIDER"] | sets["CF_TOKEN_ENV_KEYS"]
    return known - sets["NON_SECRET_ENV_KEYS"]


def _has_sources(root: Path) -> bool:
    return all((root / p).is_file() for p in ov_key_envs.OV_SOURCES.values())


def _ov_checkout() -> Path | None:
    explicit = os.environ.get("OPENVAULT_SRC", "")
    if explicit:
        # Asked for by name: a wrong path is a failure, never a quiet skip.
        if not _has_sources(Path(explicit)):
            pytest.fail(f"OPENVAULT_SRC={explicit} lacks {sorted(set(ov_key_envs.OV_SOURCES.values()))}")
        return Path(explicit)
    sibling = ROOT.parent / "OpenVault"
    return sibling if _has_sources(sibling) else None


def test_mirror_matches_an_openvault_checkout() -> None:
    src = _ov_checkout()
    if src is None:
        if os.environ.get("CI", "").strip().lower() not in ("", "0", "false"):
            pytest.fail(
                f"CI has no OpenVault checkout: check out {ov_key_envs.OV_REPO}@{ov_key_envs.OV_PIN} "
                "and set OPENVAULT_SRC (ci.yml); skipping here would pass the drift check unrun"
            )
        pytest.skip("no OpenVault checkout (set OPENVAULT_SRC); Cortex-side checks still run")
        return
    read = {p: (src / p).read_text(encoding="utf-8") for p in set(ov_key_envs.OV_SOURCES.values())}
    ov = derive_ov_key_envs(read)
    mirror = set(ov_key_envs.OV_KEY_ENVS)
    where = f"{src} (mirror pinned to {ov_key_envs.OV_REPO}@{ov_key_envs.OV_PIN})"
    assert sorted(ov - mirror) == [], f"OpenVault custodies names Cortex does not scrub: {where}"
    assert sorted(mirror - ov) == [], f"Cortex mirrors names OpenVault no longer lists: {where}"


def test_the_drift_check_can_fail() -> None:
    """Synthetic OpenVault sources: one name added, one dropped, one non-secret."""
    names = [n for n in ov_key_envs.OV_KEY_ENVS if n != "OPENAI_API_KEY"]
    kv = "\n".join(f'    "{n}": "x",' for n in names)
    read = {
        ov_key_envs.OV_SOURCES["PROVIDER_TO_ENV"]: (
            'PROVIDER_TO_ENV: dict[str, str] = {"newprov": "NEWPROV_API_KEY"}\n'
            f"ENV_KEY_TO_PROVIDER: dict[str, str] = {{\n{kv}\n    \"OPENVAULT_URL\": \"custom\",\n}}\n"
        ),
        ov_key_envs.OV_SOURCES["CF_TOKEN_ENV_KEYS"]: (
            'CF_TOKEN_ENV_KEYS = frozenset({"CF_API_TOKEN"})\nCF_ACCOUNT_ID_ENV = "CLOUDFLARE_ACCOUNT_ID"\n'
        ),
        ov_key_envs.OV_SOURCES["NON_SECRET_ENV_KEYS"]: (
            'NON_SECRET_ENV_KEYS = frozenset({"OPENVAULT_URL", CF_ACCOUNT_ID_ENV})\n'
        ),
    }
    ov = derive_ov_key_envs(read)
    mirror = set(ov_key_envs.OV_KEY_ENVS)
    assert sorted(ov - mirror) == ["NEWPROV_API_KEY"]
    assert sorted(mirror - ov) == ["OPENAI_API_KEY"]
    assert "OPENVAULT_URL" not in ov


def test_mirror_is_a_sorted_name_list_with_no_config_or_registry_data() -> None:
    names = ov_key_envs.OV_KEY_ENVS
    assert list(names) == sorted(set(names))
    assert all(n.isupper() and " " not in n and "/" not in n for n in names)
    assert "OPENVAULT_URL" not in names and "CLOUDFLARE_ACCOUNT_ID" not in names
    assert len(ov_key_envs.OV_PIN) == 40


def test_every_secret_crew_offers_or_vaults_is_scrubbed() -> None:
    """Cortex-side drift: a key name Crew accepts or upserts to OpenVault is one every child loses."""
    from CortexOS.crew import keys, openvault, shell

    names = set(harness_secrets.secret_env_names())
    offered = {k for k in keys.KNOWN if shell._SECRET_KEY_RE.search(k)}
    assert offered, "crew offers no secret fields; the check is vacuous"
    assert sorted(offered - names) == []
    assert sorted(set(openvault._ENV_TO_PROVIDER) - names) == []
    assert sorted({f["key"] for f in keys.public_fields() if f["key"] in offered} - names) == []
