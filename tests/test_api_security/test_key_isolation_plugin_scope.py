"""``tests/api_key_isolation.py`` stays an auth-key plugin, not a second conftest.

It is loaded suite-wide through ``addopts`` because
``test_branch_does_not_dual_write_freeroute_layer`` (#215) freezes
``tests/conftest.py``. That is only sound while the plugin cannot reach the
FreeRoute / OpenVault layer the guard protects or override a conftest fixture.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "tests" / "api_key_isolation.py"
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "typing",
    "pytest",
    "CortexOS.security",
    "packs.dms.security.request_authorizer",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _fixture_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and target.attr == "fixture":
                    names.add(node.name)
    return names


def test_plugin_is_the_only_addopts_plugin():
    lines = (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
    assert [ln.strip() for ln in lines if ln.strip().startswith("addopts")] == [
        'addopts = "-p tests.api_key_isolation"'
    ]


def test_plugin_imports_only_the_auth_port_and_dms_authorizer():
    imported = set()
    for node in ast.walk(_tree(PLUGIN)):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS


def test_plugin_sets_only_dms_api_keys():
    touched = set()
    for node in ast.walk(_tree(PLUGIN)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"setenv", "delenv", "setattr", "delattr", "setitem", "delitem"}
        ):
            first = node.args[0] if node.args else None
            touched.add((node.func.attr, first.value if isinstance(first, ast.Constant) else None))
    assert touched == {("setenv", "DMS_API_KEYS")}


def test_plugin_fixtures_do_not_shadow_conftest_fixtures():
    plugin = _fixture_names(_tree(PLUGIN))
    conftest = _fixture_names(_tree(ROOT / "tests" / "conftest.py"))
    assert plugin == {"configured_test_api_keys", "registered_test_authorizer"}
    assert conftest, "conftest fixture scan found nothing; the AST walk is broken"
    assert not plugin & conftest
