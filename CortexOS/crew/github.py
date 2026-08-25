"""GitHub PR snapshot via `gh`. Chat-driven; Crew does not merge."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from typing import Any

from CortexOS.crew.board import snapshot as board_snapshot

RunFn = Callable[..., subprocess.CompletedProcess[str]]


def _run(argv: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def available(*, runner: RunFn | None = None) -> bool:
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return False
    run = runner or _run
    try:
        result = run(["gh", "auth", "status"], timeout=4)
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
    """List GitHub repos in CREW_GH_ORG (default Netie-AI). Read only."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {
            "ok": False,
            "detail": "CREW_LIVE_PROBES=0",
            "repos": [],
            "org": org or os.environ.get("CREW_GH_ORG", "Netie-AI"),
        }
    run = runner or _run
    org_name = (org or os.environ.get("CREW_GH_ORG") or "Netie-AI").strip() or "Netie-AI"
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
        result = run(argv, timeout=20)
    except FileNotFoundError:
        return {"ok": False, "detail": "gh not installed", "repos": [], "org": org_name}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": type(exc).__name__, "repos": [], "org": org_name}
    if result.returncode != 0:
        return {
            "ok": False,
            "detail": (result.stderr or result.stdout or "gh failed")[:400],
            "repos": [],
            "org": org_name,
        }
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return {"ok": False, "detail": "invalid json", "repos": [], "org": org_name}
    repos: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        lang = row.get("primaryLanguage") or {}
        lang_name = lang.get("name") if isinstance(lang, dict) else ""
        repos.append(
            {
                "name": row.get("name"),
                "description": row.get("description") or "",
                "private": bool(row.get("isPrivate")),
                "url": row.get("url") or "",
                "updated": row.get("updatedAt") or "",
                "language": lang_name or "",
            }
        )
    return {
        "ok": True,
        "detail": "",
        "repos": repos,
        "org": org_name,
        "law": "Report in chat. Do not auto-merge. Adaptive catalog lives in crew.estate.",
    }


def list_prs(limit: int = 20, *, runner: RunFn | None = None) -> dict[str, Any]:
    """List open PRs for CLAIMS repos (or CREW_GH_REPOS). Never merges."""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {"ok": False, "detail": "CREW_LIVE_PROBES=0", "prs": [], "repos": []}
    run = runner or _run
    repos = _repos()
    fields = "number,title,url,headRefName,isDraft,reviewDecision,updatedAt"
    prs: list[dict[str, Any]] = []
    errors: list[str] = []
    targets = repos or [""]
    for repo in targets:
        argv = ["gh", "pr", "list", "--limit", str(limit), "--json", fields]
        if repo:
            argv.extend(["--repo", repo])
        try:
            result = run(argv, timeout=20)
        except FileNotFoundError:
            return {"ok": False, "detail": "gh not installed", "prs": [], "repos": repos}
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{repo or 'cwd'}:{type(exc).__name__}")
            continue
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout or "gh failed")[:200])
            continue
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError:
            errors.append(f"{repo or 'cwd'}: invalid json")
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            prs.append(
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
    return {
        "ok": not errors or bool(prs),
        "detail": "; ".join(errors)[:400] if errors else "",
        "prs": prs[:80],
        "repos": repos,
        "law": "Report in chat. Do not auto-merge. Ticket Runner seats existing writers.",
    }
