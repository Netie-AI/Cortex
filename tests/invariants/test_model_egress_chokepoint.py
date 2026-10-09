"""Model egress chokepoint invariant (EPIC-HARNESS-ENT R2.5, HX-02).

Every model call Cortex makes goes through the OpenVault FreeRoute client,
``CortexOS.integrations.freeroute.complete`` (Crew reaches it through
``CortexOS.crew.freeroute.complete_core``). That client posts to OpenVault's
``/v1/chat/completions`` over stdlib HTTP; provider secrets never enter this
process, and it needs no provider SDK. So over ``CortexOS/**`` and ``packs/**``
(not tests):

(a) no file references a provider completion entry point. Entry points: litellm
    ``completion`` / ``acompletion`` / ``text_completion``, ``messages.create``,
    ``chat.completions.create``, ``responses.create``, ``generate_content``, and
    Bedrock ``converse`` / ``invoke_model``. A reference counts as an attribute
    or an imported name, at module level or inside a function, called or merely
    aliased (``f = litellm.acompletion``), or fetched by ``getattr``. Calling
    ``freeroute.complete`` / ``complete_core`` is the admitted route and is
    never a reference;
(b) no provider host literal appears anywhere: OpenVault, not Cortex, knows
    where providers live.

#283 scoped (a) to ``with CortexOS.integrations.harness.gate.call(...)``. That
gate (HX-03) is not on main, so on main there is no in-process gate to sit
inside: a provider entry point anywhere is an OpenVault bypass. When HX-03
lands, it re-adds the ``with gate.call`` exemption here.

Every current bypass is debt, listed by exact repo-relative path in
``egress_debt/{crew,workflow,packs,fabrication,other}.txt``. Their union must
stay inside ``_DEBT_CEILING``, frozen below, so debt can only shrink; a debt
line whose file is now clean must be deleted in the same commit. Each migration
ticket deletes only its own file's lines (HX-07 crew; HX-08 workflow, packs,
fabrication). R2.6: the end state is empty crew, workflow, packs and
fabrication files.

A pure relocation (#310 moves ``CortexOS/crew/llm.py`` into an engine-owned
package) renames the path in place in both the debt file and
``_DEBT_CEILING`` and, if the new home is outside ``CREW_PREFIXES``, adds that
prefix. The ceiling's size is frozen separately, so a rename cannot widen it.

This file is a protected path: a commit touching it needs ``INVARIANT-CHANGE:``.
Never add a path to ``_DEBT_CEILING``; route the call through FreeRoute.

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

#: The OpenVault FreeRoute client: the one admitted model route on main.
CHOKEPOINT_FILE = "CortexOS/integrations/freeroute.py"
CHOKEPOINT_PATH = "/v1/chat/completions"

#: Where Crew modules live. #310 relocates them; add the new package here.
CREW_PREFIXES: tuple[str, ...] = ("CortexOS/crew/",)

#: litellm module-level completion entry points.
LITELLM_ENTRIES = frozenset({"completion", "acompletion", "text_completion"})
#: attribute names that are an entry point on any receiver.
BARE_ENTRIES = frozenset({"generate_content", "converse", "invoke_model"})
#: ``<x>.<parent>.create`` entry points.
CREATE_PARENTS = frozenset({"messages", "responses"})

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

#: Frozen at main c7469da (HX-02 port of #283). May only shrink. Never add a path here.
_DEBT_CEILING: frozenset[str] = frozenset(
    {
        "CortexOS/crew/llm.py",
        "CortexOS/fabrication/hls_compiler.py",
        "CortexOS/routing/adapters/anthropic.py",
        "CortexOS/routing/adapters/openai.py",
        "CortexOS/routing/adapters/vllm.py",
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


def _entries(tree: ast.AST, aliases: dict[str, str]) -> Iterator[tuple[int, str]]:
    for node in ast.walk(tree):
        hit = _entry_reference(node, aliases)
        if hit:
            yield getattr(node, "lineno", 0), hit


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
    found = [
        f"{rel}:{ln}: provider entry {what} outside OpenVault FreeRoute"
        for ln, what in sorted(_entries(tree, aliases))
    ]
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
    if rel.startswith(CREW_PREFIXES):
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


def test_every_model_egress_goes_through_freeroute_or_is_listed_debt() -> None:
    problems = debt_problems(load_debt(), _DEBT_CEILING, scan_tree(ROOT), ROOT)
    assert problems == [], (
        "Provider egress outside OpenVault FreeRoute. Call "
        "CortexOS.integrations.freeroute.complete (crew: CortexOS.crew.freeroute."
        "complete_core); never add to _DEBT_CEILING.\n  " + "\n  ".join(problems)
    )


def test_debt_ceiling_size_is_frozen() -> None:
    """5 after BRAIN-FREEROUTE-01. Lower it when a path leaves; never raise it."""
    assert len(_DEBT_CEILING) <= 5


def test_freeroute_client_is_the_chokepoint() -> None:
    """The admitted route exists, talks to OpenVault, and is itself clean."""
    source = (ROOT / CHOKEPOINT_FILE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert any(isinstance(n, ast.FunctionDef) and n.name == "complete" for n in tree.body)
    assert any(
        isinstance(n, ast.Constant) and n.value == CHOKEPOINT_PATH for n in ast.walk(tree)
    )
    assert scan_source(source, CHOKEPOINT_FILE) == []


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
        # R2.5 plant: direct call inside a function, import inside the function.
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
        # the packs bypass shape: an Anthropic SDK client outside OpenVault
        "def f(key):\n    import anthropic\n    c = anthropic.Anthropic(api_key=key)\n"
        "    return c.messages.create(model='x', messages=[])\n",
        # a FreeRoute call in the same function does not excuse a direct one
        "import litellm\nfrom CortexOS.integrations import freeroute\n"
        "def f(m):\n    freeroute.complete('t', m)\n    return litellm.completion(model='x')\n",
        # nor does any with block: on main there is no in-process gate
        "import litellm\nfrom contextlib import nullcontext\n"
        "def f():\n    with nullcontext():\n        return litellm.completion(model='x')\n",
    ],
)
def test_direct_provider_plant_fails(source: str) -> None:
    hits = _one(source)
    assert hits and all("outside OpenVault FreeRoute" in h for h in hits), hits


@pytest.mark.parametrize(
    "source,rel",
    [
        # the admitted route, in each import shape a new /ask writer might use
        ("from CortexOS.integrations import freeroute\n"
         "def f(m):\n    return freeroute.complete('generative_ask', m, max_tokens=600)\n",
         "packs/dms/generative/plant.py"),
        ("from CortexOS.integrations.freeroute import complete\n"
         "def f(m):\n    return complete('insights', m)\n",
         "CortexOS/insights/plant.py"),
        ("from CortexOS.integrations import freeroute as core\n"
         "def f(m):\n    return core.complete('t', m)\n",
         "CortexOS/plant/mod.py"),
        ("from CortexOS.crew.freeroute import complete_core\n"
         "async def f(m):\n    return await complete_core('think', m)\n",
         "CortexOS/crew/plant.py"),
        ("from ..integrations import freeroute\n"
         "def f(m):\n    return freeroute.complete('t', m)\n",
         "CortexOS/crew/plant.py"),
        # unrelated attributes named like entries' parents are not entries
        ("def f(x):\n    return x.completion_cost(), x.messages.append(1), x.create()\n",
         "CortexOS/plant/mod.py"),
        # a host in a docstring is prose, not egress
        ('"""Talks to api.openai.com only through OpenVault."""\n', "CortexOS/plant/mod.py"),
    ],
)
def test_freeroute_plant_passes(source: str, rel: str) -> None:
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
    # the chokepoint itself gets no exemption: OpenVault holds the hosts
    assert _one(f"BASE = {literal!r}\n", CHOKEPOINT_FILE)


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
    debt: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    problems = debt_problems(debt, frozenset(), found, tmp_path)
    assert any("provider entry litellm.acompletion" in p for p in problems)
    assert any("host literal" in p for p in problems)


def test_debt_path_not_in_ceiling_fails(tmp_path: Path) -> None:
    """R2.5 plant: widening the debt without widening the (frozen) ceiling."""
    (tmp_path / "CortexOS" / "crew").mkdir(parents=True)
    (tmp_path / "CortexOS" / "crew" / "new.py").write_text(
        "import litellm\nf = litellm.completion\n", encoding="utf-8"
    )
    found = scan_tree(tmp_path)
    debt: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    debt["crew"] = ["CortexOS/crew/new.py"]
    problems = debt_problems(debt, _DEBT_CEILING, found, tmp_path)
    assert any("not in _DEBT_CEILING" in p for p in problems)


def test_stale_misfiled_or_duplicate_debt_fails(tmp_path: Path) -> None:
    (tmp_path / "CortexOS" / "crew").mkdir(parents=True)
    (tmp_path / "CortexOS" / "crew" / "clean.py").write_text("x = 1\n", encoding="utf-8")
    debt: dict[str, list[str]] = {c: [] for c in CATEGORIES}
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
