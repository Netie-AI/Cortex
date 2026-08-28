"""Build Cortex discovery reference catalogs from awesome-list snapshots.

Offline-first: parses local markdown snapshots (or fetches raw GitHub READMEs
when --fetch is set) into JSON indexes under CortexOS/discovery/refs/.

Sources (skills first, then MCP, then SkillOpt evolve):
  - BehiSecc/awesome-claude-skills
  - itgoyo/awesome-agent-skills
  - rohitg00/awesome-claude-code-toolkit
  - punkpeye/awesome-mcp-servers
  - microsoft/SkillOpt
  - vercel-labs/skills (find-skills meta)
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from html import unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "CortexOS" / "discovery" / "refs"

GITHUB_RAW = {
    "punkpeye/awesome-mcp-servers": "https://raw.githubusercontent.com/punkpeye/awesome-mcp-servers/main/README.md",
    "BehiSecc/awesome-claude-skills": "https://raw.githubusercontent.com/BehiSecc/awesome-claude-skills/main/README.md",
    "rohitg00/awesome-claude-code-toolkit": "https://raw.githubusercontent.com/rohitg00/awesome-claude-code-toolkit/main/README.md",
    "itgoyo/awesome-agent-skills": "https://raw.githubusercontent.com/itgoyo/awesome-agent-skills/master/README.md",
}

LINK_RE = re.compile(
    r"\[([^\]]+)\]\((https://github\.com/([^/\s\)]+)/([^/\s\#\)]+)"
    r"(?:/[^)\s]*)?)\)\s*[-–—:]?\s*(.*)"
)
TAG_CANDIDATES = (
    "playwright",
    "browser",
    "test",
    "testing",
    "github",
    "postgres",
    "sqlite",
    "filesystem",
    "search",
    "memory",
    "rag",
    "docker",
    "kubernetes",
    "slack",
    "notion",
    "email",
    "calendar",
    "aws",
    "azure",
    "gcp",
    "security",
    "pdf",
    "excel",
    "chrome",
    "puppeteer",
    "selenium",
    "skill",
    "agent",
    "subagent",
    "mcp",
)


def slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.lower()).strip("-")
    return (s or "item")[:80]


def clean(text: str) -> str:
    s = unescape(re.sub(r"<[^>]+>", " ", text or ""))
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", s)
    s = re.sub(r"[*_`#]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:320]


def infer_tags(*parts: str) -> list[str]:
    low = " ".join(parts).lower()
    return [t for t in TAG_CANDIDATES if t in low]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Cortex-Discovery/0.1"})
    with urllib.request.urlopen(req, timeout=45) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def parse_entries(text: str, *, source: str, default_kind: str) -> list[dict]:
    items: list[dict] = []
    seen: set[str] = set()
    category = "general"
    for line in text.splitlines():
        if line.startswith("## "):
            category = clean(line[3:])[:80] or category
            continue
        if line.startswith("### "):
            category = clean(line[4:])[:80] or category
            continue
        m = LINK_RE.search(line)
        if not m:
            continue
        name, url, owner, repo, desc = m.groups()
        key = f"{owner}/{repo}".lower()
        if key in seen or "awesome-" in repo.lower():
            continue
        seen.add(key)
        kind = default_kind
        low = f"{name} {desc} {repo} {category}".lower()
        if default_kind == "toolkit":
            if "mcp" in low:
                kind = "mcp"
            elif "skill" in low:
                kind = "skill"
            elif any(x in low for x in ("agent", "subagent", "plugin", "command", "hook")):
                kind = "subagent"
            else:
                kind = "toolkit"
        tags = infer_tags(name, desc, repo, category)
        items.append(
            {
                "id": f"{kind}:{slug(owner + '-' + repo)}",
                "kind": kind,
                "name": clean(name)[:120] or repo,
                "description": clean(desc) or f"{kind} from {owner}/{repo}",
                "url": f"https://github.com/{owner}/{repo}",
                "source": source,
                "category": category,
                "tags": tags,
                "install_hint": _install_hint(kind, owner, repo),
                "reputation": "community",
            }
        )
    return items


def _install_hint(kind: str, owner: str, repo: str) -> str:
    if kind == "skill":
        return f"npx skills add {owner}/{repo}"
    if kind == "mcp":
        return f"Add MCP server from github.com/{owner}/{repo} (third-party MCP clients stay P16-gated)"
    return f"Review github.com/{owner}/{repo} then wire via Cortex discovery install path"


PRIORITY_SKILLS = [
    {
        "id": "skill:vercel-labs-find-skills",
        "kind": "skill",
        "name": "find-skills",
        "description": (
            "Meta-skill: discover and install the right agent skill for a goal via "
            "Skills CLI (npx skills find / add). Ask: Are there any good skills for [GOAL]?"
        ),
        "url": "https://github.com/vercel-labs/skills/tree/main/skills/find-skills",
        "source": "vercel-labs/skills",
        "category": "meta",
        "tags": ["find-skills", "discovery", "meta", "install"],
        "install_hint": "npx skills add vercel-labs/skills --skill find-skills -g -y",
        "reputation": "official",
        "installs_hint": 2_000_000,
    },
    {
        "id": "skill:microsoft-skillopt",
        "kind": "skill",
        "name": "SkillOpt",
        "description": (
            "Train reusable natural-language skills for frozen LLM agents via "
            "trajectory-driven edits and validation-gated best_skill.md artifacts."
        ),
        "url": "https://github.com/microsoft/SkillOpt",
        "source": "microsoft/SkillOpt",
        "category": "evolution",
        "tags": ["skillopt", "evolve", "optimize", "training"],
        "install_hint": "pip install skillopt",
        "reputation": "official",
    },
    {
        "id": "skill:playwright-testing",
        "kind": "skill",
        "name": "Playwright / webapp testing",
        "description": (
            "Browser automation and webapp testing with Playwright for reliable "
            "E2E coverage of agent UIs and APIs."
        ),
        "url": "https://github.com/microsoft/playwright",
        "source": "curated",
        "category": "testing",
        "tags": ["playwright", "testing", "e2e", "browser", "reliability"],
        "install_hint": "npm init playwright@latest ; pip install playwright",
        "reputation": "official",
    },
    {
        "id": "skill:anthropic-skill-creator",
        "kind": "skill",
        "name": "skill-creator",
        "description": "Create new SKILL.md packages for Claude Code / agent skill ecosystems.",
        "url": "https://github.com/anthropics/skills",
        "source": "anthropics/skills",
        "category": "meta",
        "tags": ["create", "skill", "authoring"],
        "install_hint": "npx skills add anthropics/skills",
        "reputation": "official",
    },
]


def _dedupe(items: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def build(*, texts: dict[str, str], mcp_cap: int = 800) -> dict[str, object]:
    mcp_items = parse_entries(
        texts.get("punkpeye/awesome-mcp-servers", ""),
        source="punkpeye/awesome-mcp-servers",
        default_kind="mcp",
    )
    priority_mcp = [m for m in mcp_items if m["tags"]]
    rest_mcp = [m for m in mcp_items if not m["tags"]]
    mcp_out = (priority_mcp + rest_mcp)[:mcp_cap]

    behi = parse_entries(
        texts.get("BehiSecc/awesome-claude-skills", ""),
        source="BehiSecc/awesome-claude-skills",
        default_kind="skill",
    )
    itgoyo = parse_entries(
        texts.get("itgoyo/awesome-agent-skills", ""),
        source="itgoyo/awesome-agent-skills",
        default_kind="skill",
    )
    toolkit = parse_entries(
        texts.get("rohitg00/awesome-claude-code-toolkit", ""),
        source="rohitg00/awesome-claude-code-toolkit",
        default_kind="toolkit",
    )

    skills = _dedupe(
        PRIORITY_SKILLS
        + [x for x in behi if x["kind"] == "skill"]
        + [x for x in itgoyo if x["kind"] == "skill"]
        + [x for x in toolkit if x["kind"] == "skill"]
    )
    subagents = _dedupe([x for x in toolkit if x["kind"] in ("subagent", "toolkit")])

    sources = {
        "updated": "2026-07-24",
        "policy": "skills_first_then_mcp; evolve_with_skillopt",
        "sources": [
            {
                "id": "punkpeye/awesome-mcp-servers",
                "url": "https://github.com/punkpeye/awesome-mcp-servers",
                "role": "mcp",
            },
            {
                "id": "rohitg00/awesome-claude-code-toolkit",
                "url": "https://github.com/rohitg00/awesome-claude-code-toolkit",
                "role": "toolkit_subagents",
            },
            {
                "id": "BehiSecc/awesome-claude-skills",
                "url": "https://github.com/BehiSecc/awesome-claude-skills",
                "role": "skills",
            },
            {
                "id": "itgoyo/awesome-agent-skills",
                "url": "https://github.com/itgoyo/awesome-agent-skills",
                "role": "skills",
            },
            {
                "id": "microsoft/SkillOpt",
                "url": "https://github.com/microsoft/SkillOpt",
                "role": "evolve",
            },
            {
                "id": "vercel-labs/skills/find-skills",
                "url": "https://github.com/vercel-labs/skills",
                "role": "find_skills_meta",
            },
        ],
    }
    return {
        "sources": sources,
        "skills": {"version": 1, "count": len(skills), "items": skills},
        "mcp_servers": {"version": 1, "count": len(mcp_out), "items": mcp_out},
        "subagents": {"version": 1, "count": len(subagents), "items": subagents},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true", help="Fetch live READMEs from GitHub")
    parser.add_argument(
        "--snapshot-dir",
        default="",
        help="Optional directory of *.md snapshots named by source slug",
    )
    parser.add_argument("--mcp-cap", type=int, default=800)
    args = parser.parse_args()

    texts: dict[str, str] = {}
    snap = Path(args.snapshot_dir) if args.snapshot_dir else None

    # Prefer agent-tools snapshots if present (dev convenience).
    agent_tools = Path.home() / ".cursor" / "projects" / "d-Cortex" / "agent-tools"
    snapshot_map = {
        "punkpeye/awesome-mcp-servers": "dd1cfafb-3c9a-4a94-98bc-9f0a54d21aa7.txt",
        "BehiSecc/awesome-claude-skills": "a999276b-84af-4966-9b8c-d1db2d18e5df.txt",
        "rohitg00/awesome-claude-code-toolkit": "73f83dd6-c6eb-4133-962c-9fb4415be272.txt",
    }

    for source, url in GITHUB_RAW.items():
        loaded = False
        if snap:
            for candidate in (snap / f"{source.replace('/', '__')}.md", snap / f"{slug(source)}.md"):
                if candidate.exists():
                    texts[source] = candidate.read_text(encoding="utf-8", errors="replace")
                    loaded = True
                    break
        if not loaded and source in snapshot_map:
            p = agent_tools / snapshot_map[source]
            if p.exists():
                texts[source] = p.read_text(encoding="utf-8", errors="replace")
                loaded = True
        if not loaded and args.fetch:
            try:
                texts[source] = fetch(url)
                loaded = True
            except Exception as exc:  # noqa: BLE001 — soft fail per source
                print(f"warn: fetch failed for {source}: {exc}")
        if not loaded:
            texts[source] = ""

    catalogs = build(texts=texts, mcp_cap=args.mcp_cap)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sources.json").write_text(json.dumps(catalogs["sources"], indent=2), encoding="utf-8")
    for name in ("skills", "mcp_servers", "subagents"):
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(catalogs[name], indent=2), encoding="utf-8")
        print(f"wrote {path} count={catalogs[name]['count']}")


if __name__ == "__main__":
    main()
