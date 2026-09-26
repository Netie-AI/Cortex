"""Model egress chokepoint invariant (EPIC-HARNESS-ENT R2.5, HX-02).

Every provider attempt Cortex sends must be admitted by the harness gate first.
At build time that means, over ``CortexOS/**`` and ``packs/**`` (not tests):

(a) every reference to a provider completion entry point sits inside the body of
    a ``with`` / ``async with`` whose context expression calls
    ``CortexOS.integrations.harness.gate.call``. Entry points: litellm
    ``completion`` / ``acompletion`` / ``text_completion``, ``messages.create``,
    ``chat.completions.create``, ``responses.create``, ``generate_content``, and
    Bedrock ``converse`` / ``invoke_model``. A reference counts as an attribute
    or an imported name, at module level or inside a function, called or merely
    aliased (``f = litellm.acompletion``), or fetched by ``getattr``;
(b) no provider host literal appears outside the files exempt by design.

The gate does not exist yet (HX-03 builds it), so every current site is debt,
listed by exact repo-relative path in ``egress_debt/{crew,workflow,packs,
fabrication,other}.txt``. Their union must stay inside ``_DEBT_CEILING``, frozen
below, so debt can only shrink; a debt line whose file is now clean must be
deleted in the same commit. Each migration ticket deletes only its own file's
lines (HX-07 crew; HX-08 workflow, packs, fabrication). R2.6: the end state is
empty crew, workflow, packs and fabrication files.

This file is a protected path: a commit touching it needs ``INVARIANT-CHANGE:``.
Never add a path to ``_DEBT_CEILING``; route the call through ``gate.call``.

Regenerate the violation list (never the ceiling) with::

    python tests/invariants/test_model_egress_chokepoint.py
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("CortexOS", "packs")
DEBT_DIR = Path(__file__).resolve().parent / "egress_debt"
CATEGORIES = ("crew", "workflow", "packs", "fabrication", "other")

GATE_CALL = "CortexOS.integrations.harness.gate.call"

#: litellm module-level completion entry points.
LITELLM_ENTRIES = frozenset({"completion", "acompletion", "text_completion"})
#: attribute names that are an entry point on any receiver.
BARE_ENTRIES = frozenset({"generate_content", "converse", "invoke_model"})
#: ``<x>.<parent>.create`` entry points.
CREATE_PARENTS = frozenset({"messages", "responses"})

#: Files exempt from (b) by design: they are where provider hosts live.
HOST_EXEMPT_PREFIXES = (
    "CortexOS/integrations/harness/transport/",
    "CortexOS/integrations/harness/registry.py",
    "CortexOS/integrations/direct_providers.py",
)

PROVIDER_HOST = re.compile(
    r"(?i)\b(?:"
    r"api\.openai\.com|[a-z0-9-]+\.openai\.azure\.com|api\.anthropic\.com"
    r"|generativelanguage\.googleapis\.com|[a-z0-9-]*aiplatform\.googleapis\.com"
    r"|integrate\.api\.nvidia\.com|api\.mistral\.ai|api\.cerebras\.ai|openrouter\.ai"
    r"|api\.groq\.com|api\.together\.(?:xyz|ai)|api\.deepseek\.com|api\.cohere\.(?:ai|com)"
    r"|bedrock-runtime(?:-fips)?\.[a-z0-9-]+\.amazonaws\.com|api\.x\.ai|api\.fireworks\.ai"
    r"|api\.perplexity\.ai|api\.moonshot\.(?:ai|cn)"
    r")\b"
)

#: Frozen at 03b545f (HX-02). May only shrink. Never add a path here.
_DEBT_CEILING: frozenset[str] = frozenset(
    {
        "CortexOS/crew/config.py",
        "CortexOS/crew/keys.py",
        "CortexOS/crew/llm.py",
        "CortexOS/fabrication/hls_compiler.py",
        "CortexOS/routing/adapters/anthropic.py",
        "CortexOS/routing/adapters/openai.py",
        "CortexOS/routing/adapters/vllm.py",
        "packs/dms/generative/brain.py",
        "packs/dms/tasks/suggest.py",
    }
)


# -- scanner -----------------------------------------------------------------


def _module_package(rel: str) -> list[str]:
    parts = rel[:-3].split("/")
    return parts[:-1] if parts[-1] != "__init__" else parts[:-1]


def _aliases(tree: ast.AST, rel: str) -> dict[str, str]:
    """Local name -> fully qualified dotted name, from every import in the file."""
    pkg = _module_package(rel)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    out[a.asname] = a.name
                else:
                    head = a.name.split(".")[0]
                    out[head] = head
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                mod = ".".join([*base, node.module] if node.module else base)
            else:
                mod = node.module or ""
            for a in node.names:
                if a.name != "*":
                    out[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name
    return out


def _dotted(node: ast.AST, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _is_gate(item: ast.withitem, aliases: dict[str, str]) -> bool:
    expr = item.context_expr
    return isinstance(expr, ast.Call) and _dotted(expr.func, aliases) == GATE_CALL


def _entry_reference(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Attribute):
        if node.attr in BARE_ENTRIES:
            return node.attr
        parent = node.value
        if node.attr == "create" and isinstance(parent, ast.Attribute):
            if parent.attr in CREATE_PARENTS:
                return f"{parent.attr}.create"
            if (
                parent.attr == "completions"
                and isinstance(parent.value, ast.Attribute)
                and parent.value.attr == "chat"
            ):
                return "chat.completions.create"
        if node.attr in LITELLM_ENTRIES:
            dotted = _dotted(node, aliases)
            if dotted and dotted.split(".")[0] == "litellm":
                return dotted
    elif isinstance(node, ast.Name):
        target = aliases.get(node.id, "")
        if target.split(".")[0] == "litellm" and target.rsplit(".", 1)[-1] in LITELLM_ENTRIES:
            return target
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in LITELLM_ENTRIES
    ):
        dotted = _dotted(node.args[0], aliases)
        if dotted and dotted.split(".")[0] == "litellm":
            return f"getattr({dotted}, {node.args[1].value!r})"
    return None


def _ungated_entries(tree: ast.AST, aliases: dict[str, str]) -> Iterator[tuple[int, str]]:
    def visit(node: ast.AST, gated: bool) -> Iterator[tuple[int, str]]:
        if not gated:
            hit = _entry_reference(node, aliases)
            if hit:
                yield getattr(node, "lineno", 0), hit
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                yield from visit(item, gated)
            inner = gated or any(_is_gate(i, aliases) for i in node.items)
            for stmt in node.body:
                yield from visit(stmt, inner)
            return
        for child in ast.iter_child_nodes(node):
            yield from visit(child, gated)

    yield from visit(tree, False)


def _host_literals(tree: ast.AST) -> Iterator[tuple[int, str]]:
    docstrings = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            m = PROVIDER_HOST.search(node.value)
            if m:
                yield node.lineno, f"host literal {m.group(0)!r}"


def scan_source(source: str, rel: str) -> list[str]:
    """Violations in one file, as ``rel:line: what`` strings."""
    tree = ast.parse(source, filename=rel)
    aliases = _aliases(tree, rel)
    found = [f"{rel}:{ln}: ungated {what}" for ln, what in _ungated_entries(tree, aliases)]
    if not rel.startswith(HOST_EXEMPT_PREFIXES):
        found += [f"{rel}:{ln}: {what}" for ln, what in _host_literals(tree)]
    return found


def _py_files(root: Path) -> Iterable[Path]:
    for top in SCAN_ROOTS:
        base = root / top
        if base.is_dir():
            yield from sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def scan_tree(root: Path = ROOT) -> dict[str, list[str]]:
    """repo-relative path -> violations, for every violating file."""
    out: dict[str, list[str]] = {}
    for path in _py_files(root):
        rel = path.relative_to(root).as_posix()
        hits = scan_source(path.read_text(encoding="utf-8"), rel)
        if hits:
            out[rel] = hits
    return out


def category(rel: str) -> str:
    if rel.startswith("CortexOS/crew/"):
        return "crew"
    if rel.startswith(("CortexOS/routing/", "CortexOS/execution/")):
        return "workflow"
    if rel.startswith("packs/"):
        return "packs"
    if rel.startswith("CortexOS/fabrication/"):
        return "fabrication"
    return "other"


def load_debt(debt_dir: Path = DEBT_DIR) -> dict[str, list[str]]:
    debt: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        lines = (debt_dir / f"{cat}.txt").read_text(encoding="utf-8").splitlines()
        debt[cat] = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return debt


def debt_problems(
    debt: dict[str, list[str]], ceiling: frozenset[str], violations: dict[str, list[str]], root: Path
) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    for cat, paths in debt.items():
        for rel in paths:
            if rel in seen:
                problems.append(f"{rel}: listed twice in egress_debt/")
            seen.add(rel)
            if rel not in ceiling:
                problems.append(f"{rel}: in egress_debt/{cat}.txt but not in _DEBT_CEILING (debt may only shrink)")
            if category(rel) != cat:
                problems.append(f"{rel}: belongs in egress_debt/{category(rel)}.txt, not {cat}.txt")
            if not (root / rel).is_file():
                problems.append(f"{rel}: listed as debt but the file does not exist; delete the line")
            elif rel not in violations:
                problems.append(f"{rel}: clean now; delete its line from egress_debt/{cat}.txt")
    for rel, hits in sorted(violations.items()):
        if rel not in seen:
            problems.extend(hits)
    return problems


# -- the invariant --------------------------------------------------------------


def test_every_model_egress_is_gated_or_listed_debt() -> None:
    problems = debt_problems(load_debt(), _DEBT_CEILING, scan_tree(ROOT), ROOT)
    assert problems == [], (
        "Provider egress outside the harness gate. Wrap each attempt in "
        f"`with {GATE_CALL}(...)`; never add to _DEBT_CEILING.\n  " + "\n  ".join(problems)
    )


def test_scan_actually_saw_the_tree() -> None:
    """A walker that finds nothing would pass the invariant vacuously."""
    files = list(_py_files(ROOT))
    assert len(files) > 200
    assert {f.relative_to(ROOT).parts[0] for f in files} == set(SCAN_ROOTS)
    assert set(scan_tree(ROOT)) == _DEBT_CEILING & set(scan_tree(ROOT))
    assert scan_tree(ROOT), "baseline debt vanished; the scanner is probably blind"


def test_debt_files_exist_for_every_category() -> None:
    assert sorted(p.stem for p in DEBT_DIR.glob("*.txt")) == sorted(CATEGORIES)


# -- meta-tests: the invariant can fail -----------------------------------------


def _one(source: str, rel: str = "CortexOS/plant/mod.py") -> list[str]:
    return scan_source(source, rel)


@pytest.mark.parametrize(
    "source",
    [
        # R2.5 plant: ungated call inside a function, import inside the function.
        "def f():\n    import litellm\n    return litellm.completion(model='x', messages=[])\n",
        # R2.5 plant: an alias, never called here.
        "import litellm\nf = litellm.acompletion\n",
        "import litellm as ll\nasync def f():\n    return await ll.text_completion(prompt='x')\n",
        "from litellm import acompletion\nasync def f():\n    return await acompletion(model='x')\n",
        "from litellm import completion as c\nx = c\n",
        "import litellm\nf = getattr(litellm, 'acompletion')\n",
        "def f(litellm):\n    return litellm.acompletion(model='x')\n",
        "def f(client):\n    return client.messages.create(model='x')\n",
        "def f(client):\n    return client.chat.completions.create(model='x')\n",
        "def f(client):\n    return client.responses.create(model='x')\n",
        "def f(m):\n    return m.generate_content('x')\n",
        "def f(b):\n    return b.converse(modelId='x')\n",
        "def f(b):\n    return b.invoke_model(modelId='x')\n",
        # a with block that is not the gate does not count
        "import litellm\nfrom contextlib import nullcontext\n"
        "def f():\n    with nullcontext():\n        return litellm.completion(model='x')\n",
        # the reference sits after the gated block, not inside it
        "import litellm\nfrom CortexOS.integrations.harness import gate\n"
        "def f(i):\n    with gate.call(i):\n        pass\n    return litellm.completion(model='x')\n",
    ],
)
def test_ungated_plant_fails(source: str) -> None:
    hits = _one(source)
    assert hits and all("ungated" in h for h in hits), hits


@pytest.mark.parametrize(
    "source,rel",
    [
        ("from CortexOS.integrations.harness import gate\nimport litellm\n"
         "async def f(i):\n    async with gate.call(i):\n        return await litellm.acompletion(model='x')\n",
         "CortexOS/plant/mod.py"),
        ("import CortexOS.integrations.harness.gate as g\n"
         "def f(i, client):\n    with g.call(i) as adm:\n        return client.messages.create(model='x')\n",
         "packs/plant/mod.py"),
        ("from CortexOS.integrations.harness.gate import call\nimport litellm\n"
         "def f(i):\n    with call(i):\n        f = litellm.completion\n        return f(model='x')\n",
         "CortexOS/plant/mod.py"),
        ("import CortexOS.integrations.harness.gate\nimport litellm\n"
         "def f(i):\n    with CortexOS.integrations.harness.gate.call(i):\n        return litellm.completion()\n",
         "CortexOS/plant/mod.py"),
        # relative import from inside CortexOS/crew/
        ("from ..integrations.harness import gate\n"
         "async def f(i, litellm):\n    async with gate.call(i):\n        return await litellm.acompletion()\n",
         "CortexOS/crew/plant.py"),
        # unrelated attributes named like entries' parents are not entries
        ("def f(x):\n    return x.completion_cost(), x.messages.append(1), x.create()\n",
         "CortexOS/plant/mod.py"),
        # a host in a docstring is prose, not egress
        ('"""Talks to api.openai.com through the gate."""\n', "CortexOS/plant/mod.py"),
        # exempt by design
        ("BASE = 'https://api.openai.com/v1'\n", "CortexOS/integrations/direct_providers.py"),
        ("BASE = 'https://api.anthropic.com'\n", "CortexOS/integrations/harness/transport/anthropic.py"),
        ("ROWS = {'nvidia': 'https://integrate.api.nvidia.com/v1'}\n", "CortexOS/integrations/harness/registry.py"),
    ],
)
def test_gated_or_exempt_plant_passes(source: str, rel: str) -> None:
    assert _one(source, rel) == []


@pytest.mark.parametrize(
    "literal",
    [
        "https://api.openai.com/v1",
        "api.anthropic.com",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "https://integrate.api.nvidia.com/v1",
        "https://my-deploy.openai.azure.com/openai",
        "https://bedrock-runtime.ap-southeast-5.amazonaws.com",
        "https://us-central1-aiplatform.googleapis.com",
        "https://openrouter.ai/api/v1",
    ],
)
def test_host_literal_plant_fails(literal: str) -> None:
    hits = _one(f"BASE = {literal!r}\n")
    assert len(hits) == 1 and "host literal" in hits[0], hits
    # f-string parts are literals too
    assert _one(f"def f(p):\n    return f'{literal}/{{p}}'\n")


def test_plants_on_disk_are_found_by_the_tree_walk(tmp_path: Path) -> None:
    (tmp_path / "CortexOS" / "deep" / "er").mkdir(parents=True)
    (tmp_path / "packs" / "p").mkdir(parents=True)
    (tmp_path / "CortexOS" / "deep" / "er" / "x.py").write_text(
        "class C:\n    def m(self):\n        import litellm\n        return litellm.acompletion()\n",
        encoding="utf-8",
    )
    (tmp_path / "packs" / "p" / "y.py").write_text("H = 'https://api.mistral.ai/v1'\n", encoding="utf-8")
    found = scan_tree(tmp_path)
    assert set(found) == {"CortexOS/deep/er/x.py", "packs/p/y.py"}
    debt = {c: [] for c in CATEGORIES}
    problems = debt_problems(debt, frozenset(), found, tmp_path)
    assert any("ungated litellm.acompletion" in p for p in problems)
    assert any("host literal" in p for p in problems)


def test_debt_path_not_in_ceiling_fails(tmp_path: Path) -> None:
    """R2.5 plant: widening the debt without widening the (frozen) ceiling."""
    (tmp_path / "CortexOS" / "crew").mkdir(parents=True)
    (tmp_path / "CortexOS" / "crew" / "new.py").write_text(
        "import litellm\nf = litellm.completion\n", encoding="utf-8"
    )
    found = scan_tree(tmp_path)
    debt = {c: [] for c in CATEGORIES}
    debt["crew"] = ["CortexOS/crew/new.py"]
    problems = debt_problems(debt, _DEBT_CEILING, found, tmp_path)
    assert any("not in _DEBT_CEILING" in p for p in problems)


def test_stale_misfiled_or_duplicate_debt_fails(tmp_path: Path) -> None:
    (tmp_path / "CortexOS" / "crew").mkdir(parents=True)
    (tmp_path / "CortexOS" / "crew" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    debt = {c: [] for c in CATEGORIES}
    debt["crew"] = ["CortexOS/crew/clean.py", "CortexOS/crew/gone.py"]
    debt["other"] = ["CortexOS/crew/clean.py"]
    ceiling = frozenset({"CortexOS/crew/clean.py", "CortexOS/crew/gone.py"})
    problems = debt_problems(debt, ceiling, scan_tree(tmp_path), tmp_path)
    assert any("clean now" in p for p in problems)
    assert any("does not exist" in p for p in problems)
    assert any("listed twice" in p for p in problems)
    assert any("belongs in egress_debt/crew.txt" in p for p in problems)


if __name__ == "__main__":
    for rel, hits in sorted(scan_tree(ROOT).items()):
        print(f"[{category(rel)}] {rel}")
        for h in hits:
            print(f"    {h}")
    sys.exit(0)
