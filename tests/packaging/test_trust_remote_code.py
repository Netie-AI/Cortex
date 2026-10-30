"""TRUST-REMOTE-CODE-01 (#449): model loads never run code shipped in a model repo.

``trust_remote_code=True`` lets ``from_pretrained`` import the model repo's own
``modeling_*.py``. Where the model id comes from the environment
(``CORTEX_LOCAL_MODEL``), that turns a config value into code execution. Every
``trust_remote_code=`` keyword in shipped code must be the literal ``False``;
``True``, a variable, or ``**kwargs`` that set it are all refused, because a
value the scan cannot see is one it cannot prove safe.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("CortexOS", "packs", "scripts", "netie")


def violations(source: str, rel: str) -> list[str]:
    out: list[str] = []
    for node in ast.walk(ast.parse(source, filename=rel)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg != "trust_remote_code":
                    continue
                if not (isinstance(kw.value, ast.Constant) and kw.value.value is False):
                    out.append(f"{rel}:{node.lineno}: trust_remote_code is not the literal False")
        elif isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and key.value == "trust_remote_code":
                    out.append(f"{rel}:{node.lineno}: trust_remote_code passed through a dict")
    return out


def _files() -> list[Path]:
    files: list[Path] = []
    for top in SCAN_ROOTS:
        base = ROOT / top
        if base.is_dir():
            files += [p for p in base.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(files)


def test_no_shipped_code_trusts_remote_code() -> None:
    found: list[str] = []
    for path in _files():
        rel = path.relative_to(ROOT).as_posix()
        found += violations(path.read_text(encoding="utf-8"), rel)
    assert found == [], "\n".join(found)


def test_scan_saw_the_known_load_sites() -> None:
    """A blind walker would pass vacuously; the two known sites must be in scope."""
    rels = {p.relative_to(ROOT).as_posix() for p in _files()}
    assert {"CortexOS/nlp/local_inference.py", "scripts/finetune_dms_tone.py"} <= rels
    for rel in ("CortexOS/nlp/local_inference.py", "scripts/finetune_dms_tone.py"):
        assert "trust_remote_code=False" in (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "source",
    [
        "AutoModel.from_pretrained(m, trust_remote_code=True)\n",
        "def f(m, t):\n    return AutoTokenizer.from_pretrained(m, trust_remote_code=t)\n",
        "import os\nAutoModel.from_pretrained(m, trust_remote_code=bool(os.environ.get('X')))\n",
        "AutoModel.from_pretrained(m, **{'trust_remote_code': True})\n",
        "pipeline('text-generation', model=m, trust_remote_code=1)\n",
    ],
)
def test_plants_fail(source: str) -> None:
    assert violations(source, "plant.py")


def test_literal_false_passes() -> None:
    assert violations("AutoModel.from_pretrained(m, trust_remote_code=False)\n", "ok.py") == []
