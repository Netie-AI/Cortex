"""GitHub PR snapshot via `gh`. Chat-driven; Crew does not merge."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from CortexOS.crew.board import snapshot as board_snapshot

RunFn = Callable[..., subprocess.CompletedProcess[str]]

GH_WAIT_S = 1.5


def _run(argv: list[str], timeout: float = GH_WAIT_S) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def available(*, runner: RunFn | None = None) -> bool:
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return False
    run = runner or _run
    try:
        result = run(["gh", "auth", "status"], timeout=GH_WAIT_S)
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def _repos() -> list[str]:
    env = [r.strip() for r in os.environ.get("CREW_GH_REPOS", "").split(",") if r.strip()]
    if env:
        return env
    seen: list[str] = []
    for row in board_snapshot().get("tickets") or []:
        repo = str(row.get("repo") or "").strip()
        if repo and repo not in seen:
            seen.append(repo)
    return seen


def list_org_repos(org: str | None = None, *, runner: RunFn | None = None) -> dict[str, Any]:
    """List GitHub repos for CREW_GH_ORG / CREW_GH_OWNERS. Read only."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        owners = [org] if org else [o.strip() for o in os.environ.get("CREW_GH_OWNERS", "Netie-AI,jian-hong").split(",") if o.strip()]
        return {
            "ok": False,
            "detail": "CREW_LIVE_PROBES=0",
            "repos": [],
            "org": org or (owners[0] if owners else "Netie-AI"),
            "owners": owners,
        }
    run = runner or _run
    if org:
        owners = [org.strip()]
    else:
        owners = [
            o.strip()
            for o in os.environ.get("CREW_GH_OWNERS", "Netie-AI,jian-hong").split(",")
            if o.strip()
        ]
        if not owners:
            owners = [os.environ.get("CREW_GH_ORG") or "Netie-AI"]

    def _one(org_name: str) -> tuple[list[dict[str, Any]], str]:
        argv = [
            "gh",
            "repo",
            "list",
            org_name,
            "--limit",
            "100",
            "--json",
            "name,description,isPrivate,url,updatedAt,primaryLanguage",
        ]
        try:
            result = run(argv, timeout=GH_WAIT_S)
        except FileNotFoundError:
            return [], "gh not installed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], f"{org_name}:{type(exc).__name__}"
        if result.returncode != 0:
            return [], (result.stderr or result.stdout or "gh failed")[:200]
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError:
            return [], f"{org_name}: invalid json"
        chunk: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            lang = row.get("primaryLanguage") or {}
            lang_name = lang.get("name") if isinstance(lang, dict) else ""
            chunk.append(
                {
                    "name": row.get("name"),
                    "owner": org_name,
                    "description": row.get("description") or "",
                    "private": bool(row.get("isPrivate")),
                    "url": row.get("url") or "",
                    "updated": row.get("updatedAt") or "",
                    "language": lang_name or "",
                }
            )
        return chunk, ""

    repos: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(len(owners), 1)) as pool:
        for chunk, err in pool.map(_one, owners):
            repos.extend(chunk)
            if err == "gh not installed":
                return {
                    "ok": False,
                    "detail": "gh not installed",
                    "repos": [],
                    "org": owners[0] if owners else "Netie-AI",
                    "owners": owners,
                }
            if err:
                errors.append(err)
    return {
        "ok": not errors or bool(repos),
        "detail": "; ".join(errors)[:400] if errors else "",
        "repos": repos,
        "org": owners[0] if owners else "Netie-AI",
        "owners": owners,
        "law": "Report in chat. Do not auto-merge. Adaptive catalog lives in crew.estate.",
    }


def list_prs(
    limit: int = 20,
    *,
    runner: RunFn | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """List open PRs for CLAIMS repos (or CREW_GH_REPOS). Never merges."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {"ok": False, "detail": "CREW_LIVE_PROBES=0", "prs": [], "repos": []}
    run = runner or _run
    wait = GH_WAIT_S if timeout is None else float(timeout)
    repos = _repos()
    fields = "number,title,url,headRefName,isDraft,reviewDecision,updatedAt"
    targets = repos or [""]

    def _one(repo: str) -> tuple[list[dict[str, Any]], str]:
        argv = ["gh", "pr", "list", "--limit", str(limit), "--json", fields]
        if repo:
            argv.extend(["--repo", repo])
        try:
            result = run(argv, timeout=wait)
        except FileNotFoundError:
            return [], "gh not installed"
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [], f"{repo or 'cwd'}:{type(exc).__name__}"
        if result.returncode != 0:
            return [], (result.stderr or result.stdout or "gh failed")[:200]
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError:
            return [], f"{repo or 'cwd'}: invalid json"
        chunk: list[dict[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            chunk.append(
                {
                    "repo": repo or row.get("url", ""),
                    "number": row.get("number"),
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "head": row.get("headRefName"),
                    "draft": bool(row.get("isDraft")),
                    "review": row.get("reviewDecision") or "",
                    "updated": row.get("updatedAt") or "",
                }
            )
        return chunk, ""

    prs: list[dict[str, Any]] = []
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=max(len(targets), 1)) as pool:
        for chunk, err in pool.map(_one, targets):
            prs.extend(chunk)
            if err == "gh not installed":
                return {"ok": False, "detail": "gh not installed", "prs": [], "repos": repos}
            if err:
                errors.append(err)
    return {
        "ok": not errors or bool(prs),
        "detail": "; ".join(errors)[:400] if errors else "",
        "prs": prs[:80],
        "repos": repos,
        "law": "Report in chat. Do not auto-merge. Ticket Runner seats existing writers.",
    }


def search_public(
    query: str, limit: int = 8, *, runner: RunFn | None = None
) -> dict[str, Any]:
    """Search public GitHub repos. Read only. Never clones."""
    q = (query or "").strip()
    if not q:
        return {"ok": False, "detail": "query required", "query": "", "repos": []}
    if runner is None and os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {
            "ok": False,
            "detail": "CREW_LIVE_PROBES=0",
            "query": q,
            "repos": [],
        }
    run = runner or _run
    cap = max(1, min(int(limit or 8), 20))
    argv = [
        "gh",
        "search",
        "repos",
        q,
        "--limit",
        str(cap),
        "--json",
        "name,url,description,fullName",
    ]
    try:
        result = run(argv, timeout=GH_WAIT_S)
    except FileNotFoundError:
        return {"ok": False, "detail": "gh not installed", "query": q, "repos": []}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "detail": type(exc).__name__,
            "query": q,
            "repos": [],
        }
    if result.returncode != 0:
        return {
            "ok": False,
            "detail": (result.stderr or result.stdout or "gh failed")[:200],
            "query": q,
            "repos": [],
        }
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return {"ok": False, "detail": "invalid json", "query": q, "repos": []}
    repos: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        repos.append(
            {
                "name": row.get("name") or "",
                "full_name": row.get("fullName") or "",
                "url": row.get("url") or "",
                "description": row.get("description") or "",
            }
        )
    return {
        "ok": bool(repos),
        "query": q,
        "repos": repos,
        "law": "Search then fetch. Do not guess the owner. Do not clone every repo.",
    }


def pr_diff(
    number: int | None = None,
    *,
    repo: str = "",
    runner: RunFn | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Read a PR diff. Review material only — never merge."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0" and runner is None:
        return {"ok": False, "detail": "CREW_LIVE_PROBES=0", "diff": ""}
    run = runner or _run
    wait = max(GH_WAIT_S, 3.0) if timeout is None else float(timeout)
    argv = ["gh", "pr", "diff"]
    if number is not None:
        argv.append(str(int(number)))
    if repo:
        argv.extend(["--repo", repo])
    try:
        result = run(argv, timeout=wait)
    except FileNotFoundError:
        return {"ok": False, "detail": "gh not installed", "diff": ""}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": type(exc).__name__, "diff": ""}
    if result.returncode != 0:
        return {
            "ok": False,
            "detail": (result.stderr or result.stdout or "gh failed")[:200],
            "diff": "",
        }
    return {
        "ok": True,
        "diff": (result.stdout or "")[:12000],
        "number": number,
        "repo": repo,
        "law": "Report in chat. Do not auto-merge.",
    }


def create_pr(
    *,
    title: str = "",
    body: str = "",
    repo: str = "",
    runner: RunFn | None = None,
) -> dict[str, Any]:
    """Open a PR with gh. Never merges. Skip when live probes are off."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0" and runner is None:
        return {"ok": False, "detail": "CREW_LIVE_PROBES=0", "url": ""}
    run = runner or _run
    argv = ["gh", "pr", "create"]
    title = (title or "").strip()
    if title:
        argv.extend(["--title", title[:200], "--body", (body or title)[:2000]])
    else:
        argv.append("--fill")
    if repo:
        argv.extend(["--repo", repo])
    try:
        result = run(argv, timeout=max(GH_WAIT_S, 3.0))
    except FileNotFoundError:
        return {"ok": False, "detail": "gh not installed", "url": ""}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": type(exc).__name__, "url": ""}
    text = (result.stdout or result.stderr or "")[:500]
    if result.returncode != 0:
        return {"ok": False, "detail": text or "gh failed", "url": ""}
    url = ""
    for token in text.split():
        if token.startswith("https://") and "/pull/" in token:
            url = token.strip()
            break
    return {
        "ok": True,
        "url": url or text.strip(),
        "detail": text.strip(),
        "law": "Do not auto-merge. Ticket Runner seats existing writers.",
    }
