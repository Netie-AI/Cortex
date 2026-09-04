"""GitHub PR snapshot via `gh`. Chat-driven; Crew does not merge."""

from __future__ import annotations

import json
import os
import re
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
    repos: list[dict[str, Any]] = []
    errors: list[str] = []
    for org_name in owners:
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
            return {"ok": False, "detail": "gh not installed", "repos": [], "org": org_name, "owners": owners}
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(f"{org_name}:{type(exc).__name__}")
            continue
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout or "gh failed")[:200])
            continue
        try:
            rows = json.loads(result.stdout or "[]")
        except ValueError:
            errors.append(f"{org_name}: invalid json")
            continue
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            lang = row.get("primaryLanguage") or {}
            lang_name = lang.get("name") if isinstance(lang, dict) else ""
            repos.append(
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
    return {
        "ok": not errors or bool(repos),
        "detail": "; ".join(errors)[:400] if errors else "",
        "repos": repos,
        "org": owners[0] if owners else "Netie-AI",
        "owners": owners,
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


_ISSUE_SPEC = re.compile(
    r"(?:https://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:#|/issues/)(\d+)\Z"
)


def parse_issue_spec(raw: str) -> tuple[str, str, int] | None:
    text = (raw or "").strip()
    matched = _ISSUE_SPEC.match(text)
    if matched is None:
        return None
    return matched.group(1), matched.group(2), int(matched.group(3))


def canonical_spec(raw: str) -> str:
    parsed = parse_issue_spec(raw)
    if parsed is None:
        return (raw or "").strip()
    owner, repo, number = parsed
    return f"{owner}/{repo}#{number}"


def seated_claim(raw: str) -> dict[str, Any] | None:
    """Ticket Runner seat. Crew must not close this without a different writer."""
    parsed = parse_issue_spec(raw)
    if parsed is None:
        return None
    owner, repo, number = parsed
    key = f"{owner}/{repo}#{number}"
    for row in board_snapshot().get("tickets") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("role") or "") != "SEATED":
            continue
        ticket = str(row.get("ticket") or "")
        owner_pr = str(row.get("owner_pr") or "")
        if ticket == key or owner_pr == key:
            return row
    return None


def close_issue(
    spec: str,
    *,
    comment: str = "",
    runner: RunFn | None = None,
) -> dict[str, Any]:
    """Close one GitHub *issue*. Never merges a PR."""
    parsed = parse_issue_spec(spec)
    if parsed is None:
        return {
            "ok": False,
            "detail": "DENIED: need owner/repo#n (issues only; Crew does not merge PRs)",
        }
    seated = seated_claim(spec)
    if seated is not None:
        return {
            "ok": False,
            "detail": (
                f"DENIED: {seated.get('ticket')} is SEATED "
                f"({seated.get('owner_pr')}). Ticket Runner owns the seat."
            ),
        }
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {"ok": False, "detail": "CREW_LIVE_PROBES=0"}
    owner, repo, number = parsed
    argv = ["gh", "issue", "close", str(number), "--repo", f"{owner}/{repo}"]
    note = (comment or "").strip()[:500]
    if note:
        argv.extend(["--comment", note])
    run = runner or _run
    try:
        result = run(argv, timeout=20)
    except FileNotFoundError:
        return {"ok": False, "detail": "gh not installed"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": type(exc).__name__}
    if result.returncode != 0:
        return {
            "ok": False,
            "detail": (result.stderr or result.stdout or "gh issue close failed")[:400],
        }
    return {
        "ok": True,
        "spec": f"{owner}/{repo}#{number}",
        "detail": (result.stdout or "").strip()[:400],
        "law": "Closed the issue. Did not merge a PR. Ticket Runner seats writers.",
    }
