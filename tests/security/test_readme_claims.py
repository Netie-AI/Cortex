"""DOC-01 — the front page may not claim a security control that does not run.

README.md said, in the present tense, that the system "applies Wasm sandboxing
+ platform security". It did not. `wasm_modules/base_agent.wasm` was git's
empty blob, and a repo-wide grep for the sandbox found hits in zero non-test
files — the module existed, was tested, and was never called.

Every internal document was honest about this (ARCHITECTURE.md "Scaffold only",
PARKING_LOT P2 "Do not claim WASM shipped"). The README was the outlier, and it
is the only one a customer reads. That asymmetry is the failure mode worth
catching: internal honesty does not propagate outward on its own.

So this asserts the property rather than the incident. A security claim on the
front page has to be traceable to code that actually runs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"

#: Words that promise an isolation boundary. Claiming one that nothing enforces
#: is worse than claiming nothing: it tells a buyer they are protected.
#: "container" earns its place the hard way. The first fix for DOC-01 deleted
#: the Wasm claim and wrote "untrusted code is packaged and run in containers"
#: in its place - one unbacked isolation promise swapped for another, on the
#: same front page. `app_runner.py` refuses the docker stack outright, so the
#: replacement was false in the same direction as the claim it replaced.
_ISOLATION_CLAIMS = (
    "wasm sandbox",
    "wasm sandboxing",
    "sandboxed",
    "firecracker",
    "gvisor",
    "run in containers",
    "run in a container",
    "container isolation",
)

_SEARCH_ROOTS = ("CortexOS", "packs", "scripts")


_NEGATIONS = (" not ", " no ", " never ", "n't ")

#: Splits a sentence into independently-judged clauses. A negation binds to the
#: clause it sits in, not to everything after the next dash.
#:
#: Commas count. "untrusted code is packaged and run in containers, not
#: sandboxed in-process" carries the assertion and a negation *of a different
#: control* in one comma-joined breath, and honouring the negation across the
#: comma is what let the claim through. Splitting here is deliberately eager:
#: the cost of an over-split is prose that has to state its claim plainly, and
#: the cost of an under-split is a security promise nothing keeps.
_CLAUSE_SPLIT = re.compile(r"\s+[-–—]+\s+|[;,]")


def _readme_prose() -> str:
    """README text with fenced code blocks dropped — a sample is not a claim."""
    out, fenced = [], False
    for line in README.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            out.append(line)
    return "\n".join(out).lower()


def _affirmative_sentences(prose: str) -> list[str]:
    """Sentences that assert something, dropping the ones that deny it.

    Needed because the honest fix for this very ticket is a sentence saying the
    isolation is *not* shipped, and a bare keyword match cannot tell a promise
    from a disclaimer — it would force the README to avoid the plainest word
    for the thing it is being honest about.

    But dropping the whole sentence was itself the hole. "Process-level
    isolation is **not** shipped - untrusted code is packaged and run in
    containers" denies one control and asserts another in one breath, and the
    single sentence on the front page making an unbacked claim was the one
    sentence this gate could not see. A clause is only exempt if the negation
    reaches it, so the sentence is split at each dash and semicolon first and
    every clause judged on its own.
    """
    sentences = [s.strip() for s in prose.replace("\n", " ").split(".") if s.strip()]
    clauses: list[str] = []
    for sentence in sentences:
        clauses.extend(c.strip() for c in _CLAUSE_SPLIT.split(sentence) if c.strip())
    return [c for c in clauses if not any(neg in f" {c} " for neg in _NEGATIONS)]


def _imports_a_sandbox() -> list[str]:
    """Non-test modules importing an in-process isolation backend."""
    hits: list[str] = []
    for root in _SEARCH_ROOTS:
        base = ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "__pycache__" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                if any(n.split(".")[0] in {"wasmtime", "wasmer"} for n in names):
                    hits.append(rel)
                    break
    return sorted(set(hits))


def test_readme_claims_no_isolation_the_engine_does_not_enforce() -> None:
    """The claim and the code have to agree, in that direction especially."""
    affirmative = " . ".join(_affirmative_sentences(_readme_prose()))
    claimed = [phrase for phrase in _ISOLATION_CLAIMS if phrase in affirmative]
    if not claimed:
        return  # no claim, nothing to back up

    assert _imports_a_sandbox(), (
        "README.md claims "
        + ", ".join(repr(c) for c in claimed)
        + " but no non-test module imports an isolation backend. Either wire it "
        "up and call it, or say what is actually true on the front page."
    )


def test_readme_states_what_does_guard_the_system() -> None:
    """Removing a false claim must not leave a customer with no answer at all.

    Deleting the sentence would satisfy the test above and tell a buyer nothing.
    The controls named here are the ones with real callers and real gates.
    """
    prose = _readme_prose()
    for expected in ("sqlglot", "manifest", "hash chain"):
        assert expected in prose, f"README no longer explains {expected!r} as a real control"


def test_no_empty_placeholder_artifacts_are_tracked() -> None:
    """A zero-byte .wasm is the artifact form of the same overclaim."""
    empty = [
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*.wasm")
        if ".claude" not in p.parts and p.is_file() and p.stat().st_size == 0
    ]
    assert not empty, f"zero-byte binaries still tracked: {empty}"
