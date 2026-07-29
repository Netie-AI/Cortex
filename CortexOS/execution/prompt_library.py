"""Preset prompt library — the "perfect prompts" a workflow hands to each subagent.

Built-ins live here so a fresh install always has them; ``data/prompts/<id>.md``
overrides a built-in of the same id, so a user can tune a prompt without a code
change. Rendering is deliberately dumb — ``{{var}}`` substitution only, no
expression language — because these strings are pasted straight into a model
prompt and anything cleverer becomes an injection surface.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from CortexOS.paths import data_path

_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")

PROMPTS_DIR = data_path("prompts")


@dataclass(frozen=True, slots=True)
class Prompt:
    id: str
    title: str
    body: str
    source: str  # "builtin" | "user"

    @property
    def variables(self) -> tuple[str, ...]:
        seen: list[str] = []
        for m in _VAR.finditer(self.body):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return tuple(seen)


# -- built-ins ---------------------------------------------------------------
# Each is written to be dropped in as a subagent's whole instruction. They share
# a shape: role, the one job, how to search, what to return. Subagent output is
# consumed by another agent, so every one of them ends by pinning the return
# format — a subagent that writes a chatty preamble poisons the synthesis step.

_RESEARCH_PLAN = """\
You are the planning step of a research workflow on: **{{topic}}**

Break the topic into {{fanout}} genuinely different research angles. Different
means a different *kind* of source would answer it — not the same question
reworded. Cover at minimum: what the thing is, how it actually behaves in
practice, what its critics say, and what changed most recently.

Return JSON only:
{"angles": [{"id": "kebab-case", "question": "...", "why": "what this angle
catches that the others miss", "search_terms": ["...", "..."]}]}
"""

_RESEARCH_SEARCH = """\
You are a research subagent. Your single angle is:

  **{{question}}**

Parent topic: {{topic}}
Why this angle matters: {{why}}

Method — follow it in order:
1. Call `web_search` with each of these terms, and with any better term the
   first results suggest: {{search_terms}}
2. Call `web_fetch` on the 3-5 most promising URLs. Titles and snippets are not
   evidence; read the page.
3. Note the date on every source. Say so when a source is old enough that it
   may have been overtaken.

Rules:
- Distinguish what a source *states* from what you infer. Mark inferences.
- If sources disagree, report the disagreement — do not average it away.
- If you could not find real support for a claim, say so. An honest gap is
  worth more here than a confident guess, because a later agent will try to
  verify whatever you write.

Return JSON only:
{"angle": "{{id}}", "findings": [{"claim": "...", "evidence": "quote or close
paraphrase", "url": "...", "published": "YYYY-MM or unknown", "confidence":
"high|medium|low"}], "gaps": ["what you could not establish"]}
"""

_RESEARCH_VERIFY = """\
You are a skeptic. Try to REFUTE this claim, not to confirm it:

  Claim: {{claim}}
  Offered evidence: {{evidence}}
  Source: {{url}}

Search for contradicting evidence, a more recent correction, or a sign the
source is unreliable or circular (an outlet quoting the same origin the claim
came from is not independent corroboration).

Default to refuted=true when you cannot find independent support. A claim that
survives an honest attempt to kill it is worth stating; one that merely was not
checked is not.

Early-victory ban (Anthropic verifier discipline): do NOT set refuted=false
after a cursory glance. You must (1) attempt at least one independent search or
fetch, (2) say what you checked, and (3) only then decide. "Looks fine" is a
fail — set refuted=true with reason naming the missing check.

Return JSON only:
{"refuted": true|false, "reason": "one sentence", "counter_url": "... or empty",
"corrected_claim": "... or empty",
"criteria_checked": ["independent_source", "recency_or_correction", "circularity"]}
"""

_RESEARCH_SYNTH = """\
You are the synthesis step. You have verified findings from several research
subagents on: **{{topic}}**

Findings:
{{findings}}

Write the answer the user actually asked for. Requirements:
- Lead with the answer, not with how the research was done.
- Cite inline as [n] and list the URLs at the end.
- Where the subagents disagreed, say which reading you find better supported
  and why — do not present a contradiction as settled.
- Carry the gaps forward into a short "what remains unclear" section. Do not
  quietly drop them; the gaps are part of the finding.
- No filler, no restating the question back.
"""

_AUDIT_ANIMATION = """\
You are auditing **animation and transition smoothness** in: {{target}}

Look for, with file:line for each:
- Transitions on properties that force layout or paint (width, height, top,
  left, margin) where transform/opacity would do the same job on the compositor.
- Durations and easing curves that disagree between elements that move together
  — the eye reads that as jank even at 60fps.
- Animations with no `will-change`/promotion on the moving element, and the
  opposite mistake: `will-change` left on permanently, which costs memory.
- Layout thrash: a read of a layout property (offsetWidth, getBoundingClientRect)
  inside the same frame as a write.
- Missing `prefers-reduced-motion` handling.

Report only what you can point at in the code. A suspicion with no file:line is
not a finding. Return JSON only:
{"findings": [{"title": "...", "file": "...", "line": 0, "severity":
"high|medium|low", "why_janky": "the mechanism, not a restatement", "fix": "the
concrete change"}]}
"""

_AUDIT_LATENCY = """\
You are auditing **interaction latency** in: {{target}}

Trace the path from user input to first visible response. Look for:
- Work on the UI thread between the event and the first paint that could be
  deferred, batched, or moved off-thread.
- Awaits that serialize work which has no real dependency between the parts.
- Absent optimistic UI: input that shows nothing until a round-trip returns.
- Debounce/throttle that is missing, or set so long it reads as lag.
- Per-keystroke or per-frame work that rebuilds something cacheable.

For each finding give the file:line and say roughly how much delay it accounts
for and how you got that number. Return JSON only:
{"findings": [{"title": "...", "file": "...", "line": 0, "est_ms": 0,
"basis": "how the estimate was derived", "severity": "high|medium|low",
"fix": "..."}]}
"""

_AUDIT_RENDER = """\
You are auditing **render and DOM cost** in: {{target}}

Look for: full re-renders where a targeted update would do; list rendering with
no virtualization or keying; layout invalidated inside loops; images and media
with no explicit dimensions (causing reflow on load); expensive selectors and
deep DOM under frequently-updated roots; synchronous parse/format of large
payloads on the render path.

Point at real code with file:line. Return JSON only:
{"findings": [{"title": "...", "file": "...", "line": 0, "severity":
"high|medium|low", "why": "...", "fix": "..."}]}
"""

_AUDIT_LOADPATH = """\
You are auditing the **startup / load path** in: {{target}}

Trace what happens between launch and the first usable frame. Look for: work
done eagerly that nothing needs yet; assets loaded serially that could be
parallel; blocking work before first paint; caches that are built at startup
instead of on first use; and anything re-done on every launch that could be
persisted.

Give file:line. Return JSON only:
{"findings": [{"title": "...", "file": "...", "line": 0, "severity":
"high|medium|low", "why": "...", "fix": "..."}]}
"""

_AUDIT_VERIFY = """\
You are verifying one audit finding before it reaches the user. Be adversarial.

  Finding: {{title}}
  Location: {{file}}:{{line}}
  Claim: {{why}}
  Proposed fix: {{fix}}

Read the actual code at that location. Then decide:
- Does the code really do what the finding claims? Read it; do not take the
  finding's word for it.
- Would the proposed fix actually change the behaviour, or is it cargo cult?
- Is it already handled somewhere the finder did not look?

Early-victory ban: you MUST open/read the cited file (or report you could not)
before confirmed=true. Partial skim → confirmed=false with reason. Never mark
confirmed after only restating the finder's claim.

Return JSON only:
{"confirmed": true|false, "reason": "one sentence, citing what you read",
"revised_severity": "high|medium|low", "revised_fix": "... or empty",
"criteria_checked": ["code_matches_claim", "fix_would_change_behaviour",
"not_already_handled"]}
"""

_AUDIT_REPORT = """\
You are writing the final audit report for: {{target}}

Confirmed findings:
{{findings}}

Write it for someone who will act on it today. Order by (severity × how cheap
the fix is) — a high-severity finding that needs a rewrite ranks below a
medium one that is a two-line change. Group by file where that helps.

For each: what is slow, the mechanism, the fix, and the file:line. State
plainly if a finding is cheap to try but uncertain to help — that is useful to
know before someone spends a day on it.

End with what was NOT covered by this audit, so nobody reads it as exhaustive.
"""

_REVIEW_DIMENSION = """\
You are reviewing changed code along one dimension only: **{{dimension}}**

{{dimension_detail}}

Scope: {{target}}

Report only defects you can point at with file:line and describe as a concrete
failure — specific inputs or state that produce a wrong result. Style opinions,
"consider extracting this", and speculation are out of scope. If the dimension
turns up nothing, return an empty list; a padded review costs more than an
empty one.

Return JSON only:
{"findings": [{"title": "...", "file": "...", "line": 0, "severity":
"high|medium|low", "failure": "inputs/state -> wrong output", "fix": "..."}]}
"""

_BUG_FIND = """\
You are hunting bugs in: {{target}}
Round {{round}}. Already reported (do not repeat these):
{{seen}}

Find defects that are *new*. Look where the last round did not: error paths,
concurrency, boundary values, resource cleanup, partial-failure states,
assumptions that hold on the happy path only.

Every bug needs a failure scenario concrete enough to write a test from.
Return JSON only:
{"bugs": [{"title": "...", "file": "...", "line": 0, "severity":
"high|medium|low", "repro": "concrete inputs/state -> observed wrong
behaviour"}]}
"""

_GENERIC_WORKER = """\
{{instruction}}

Scope: {{target}}

Ground every claim in something you actually read or ran. Where you are
inferring rather than observing, say so. Return your result as the final
message with no preamble — another agent consumes this directly.
"""

_DOC_PLAN = """\
You plan which pages of a pre-materialized document to read for this question.

Question: {{question}}

Available pages (ids + short previews may be in context as {{pages}}).
Pick up to {{fanout}} page batches that best answer the question. Prefer
pages that mention key terms from the question. Always include page 1 if
it looks like a title/toc page when relevant.

Return JSON only:
{"pages": [{"page": 1, "why": "one short reason"}, ...]}
"""

_DOC_EXTRACT = """\
Extract only what answers the user question from this page batch.
Question: {{question}}
Page material (already OCR'd / text-extracted by the client):
{{page_text}}

Return distilled bullets with page cites. Max ~1200 characters.
Do not invent numbers or clauses that are not in the material.
Format:
- (p{{page}}) claim or quote
"""

_DOC_SYNTH = """\
You are the final deduce agent for a document Q&A.

Question: {{question}}

Verified page extracts:
{{findings}}

Write a short markdown answer (prefer **bold** for key terms). Cite pages
like (p12). Do not invent figures. Use a mermaid fence only if a structure
diagram clearly helps. Keep the answer chat-bubble sized when possible.
"""

_BUILTIN: tuple[Prompt, ...] = (
    Prompt("research.plan", "Research — plan angles", _RESEARCH_PLAN, "builtin"),
    Prompt("research.search", "Research — search one angle", _RESEARCH_SEARCH, "builtin"),
    Prompt("research.verify", "Research — refute a claim", _RESEARCH_VERIFY, "builtin"),
    Prompt("research.synthesize", "Research — synthesize", _RESEARCH_SYNTH, "builtin"),
    Prompt("document.plan", "Document — plan pages", _DOC_PLAN, "builtin"),
    Prompt("document.extract", "Document — extract page batch", _DOC_EXTRACT, "builtin"),
    Prompt("document.synthesize", "Document — final deduce", _DOC_SYNTH, "builtin"),
    Prompt("audit.animation", "Audit — animation smoothness", _AUDIT_ANIMATION, "builtin"),
    Prompt("audit.latency", "Audit — interaction latency", _AUDIT_LATENCY, "builtin"),
    Prompt("audit.render", "Audit — render/DOM cost", _AUDIT_RENDER, "builtin"),
    Prompt("audit.load_path", "Audit — startup/load path", _AUDIT_LOADPATH, "builtin"),
    Prompt("audit.verify", "Audit — verify a finding", _AUDIT_VERIFY, "builtin"),
    Prompt("audit.report", "Audit — final report", _AUDIT_REPORT, "builtin"),
    Prompt("review.dimension", "Review — one dimension", _REVIEW_DIMENSION, "builtin"),
    Prompt("bug.find", "Bug hunt — find round", _BUG_FIND, "builtin"),
    Prompt("generic.worker", "Generic worker", _GENERIC_WORKER, "builtin"),
)

_BY_ID: dict[str, Prompt] = {p.id: p for p in _BUILTIN}


def _user_dir() -> Path:
    return PROMPTS_DIR


def _load_user(prompt_id: str) -> Prompt | None:
    """A ``data/prompts/<id>.md`` file shadows the built-in of the same id."""
    safe = prompt_id.replace("/", "").replace("\\", "").replace("..", "")
    path = _user_dir() / f"{safe}.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    title = safe
    for line in text.splitlines():
        if line.strip().startswith("#"):
            title = line.lstrip("#").strip() or safe
            break
    return Prompt(safe, title, text, "user")


def get(prompt_id: str) -> Prompt | None:
    return _load_user(prompt_id) or _BY_ID.get(prompt_id)


def catalog() -> list[dict[str, Any]]:
    """Built-ins plus any user overrides/additions, user wins on id collision."""
    out: dict[str, Prompt] = dict(_BY_ID)
    user_dir = _user_dir()
    if user_dir.is_dir():
        for path in sorted(user_dir.glob("*.md")):
            loaded = _load_user(path.stem)
            if loaded is not None:
                out[loaded.id] = loaded
    return [
        {
            "id": p.id,
            "title": p.title,
            "source": p.source,
            "variables": list(p.variables),
            "chars": len(p.body),
        }
        for p in sorted(out.values(), key=lambda x: x.id)
    ]


def render(prompt_id: str, variables: Mapping[str, Any] | None = None) -> str:
    """Substitute ``{{var}}``. An unknown var renders empty rather than raising —
    a half-filled prompt still runs, and the agent will say what it lacked."""
    prompt = get(prompt_id)
    if prompt is None:
        raise KeyError(f"unknown prompt id {prompt_id!r}")
    return render_text(prompt.body, variables)


def render_text(body: str, variables: Mapping[str, Any] | None = None) -> str:
    vals = dict(variables or {})

    def repl(m: re.Match[str]) -> str:
        raw = vals.get(m.group(1), "")
        if isinstance(raw, (list, tuple)):
            return ", ".join(str(x) for x in raw)
        return str(raw)

    return _VAR.sub(repl, body)


def save(prompt_id: str, body: str, *, title: str = "") -> dict[str, Any]:
    """Persist a user prompt to ``data/prompts/<id>.md``."""
    safe = "".join(c for c in prompt_id.strip().lower() if c.isalnum() or c in "-_.")
    if not safe or len(safe) > 64:
        return {"ok": False, "error": "invalid prompt id"}
    text = (body or "").strip()
    if not text:
        return {"ok": False, "error": "prompt body required"}
    if not text.lstrip().startswith("#"):
        text = f"# {title or safe}\n\n{text}\n"
    directory = _user_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe}.md"
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "id": safe, "path": str(path), "chars": len(text)}
