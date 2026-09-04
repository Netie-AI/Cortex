"""GitHub PR snapshot via `gh`. Chat-driven; Crew does not merge."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from CortexOS.crew.board import snapshot as board_snapshot

RunFn = Callable[..., subprocess.CompletedProcess[str]]

_FETCH_FILE = "fetched_issues.json"
_FETCH_CAP = 80


def _run(argv: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _gh_wait_s() -> float:
    raw = os.environ.get("CREW_GH_WAIT_S", "1.5")
    try:
        return max(0.4, min(8.0, float(raw)))
    except ValueError:
        return 1.5


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


def issue_spec(repo: str, number: Any, url: str = "") -> str:
    if repo and number is not None:
        try:
            return f"{repo}#{int(number)}"
        except (TypeError, ValueError):
            pass
    parsed = parse_issue_spec((url or "").strip())
    if parsed is None:
        return ""
    owner, name, n = parsed
    return f"{owner}/{name}#{n}"


def issue_title(spec: str, *, runner: RunFn | None = None) -> str:
    """Read-only title. Empty string when gh cannot answer."""
    parsed = parse_issue_spec(spec)
    if parsed is None:
        return ""
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return canonical_spec(spec)
    owner, repo, number = parsed
    run = runner or _run
    argv = [
        "gh",
        "issue",
        "view",
        str(number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "title",
    ]
    try:
        result = run(argv, timeout=_gh_wait_s())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return canonical_spec(spec)
    if result.returncode != 0:
        return canonical_spec(spec)
    try:
        blob = json.loads(result.stdout or "{}")
    except ValueError:
        return canonical_spec(spec)
    title = str(blob.get("title") or "").strip() if isinstance(blob, dict) else ""
    return title or canonical_spec(spec)


def show_issue(spec: str, *, runner: RunFn | None = None) -> dict[str, Any]:
    """Read-only issue title and body. Never assigns, closes, or merges."""
    law = (
        "Read only. Crew does not set GitHub assignees. "
        "SEATED tickets stay with Ticket Runner."
    )
    parsed = parse_issue_spec(spec)
    if parsed is None:
        return {
            "ok": False,
            "spec": (spec or "").strip(),
            "title": "",
            "body": "",
            "state": "",
            "seated": False,
            "ready": False,
            "detail": "DENIED: owner/repo#n",
            "law": law,
        }
    canon = canonical_spec(spec)
    seated = seated_claim(canon)
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {
            "ok": False,
            "spec": canon,
            "title": canon,
            "body": "",
            "state": "",
            "seated": seated is not None,
            "ready": seated is None,
            "detail": "CREW_LIVE_PROBES=0",
            "law": law,
        }
    owner, repo, number = parsed
    run = runner or _run
    argv = [
        "gh",
        "issue",
        "view",
        str(number),
        "--repo",
        f"{owner}/{repo}",
        "--json",
        "title,body,state",
    ]
    try:
        result = run(argv, timeout=_gh_wait_s())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {
            "ok": False,
            "spec": canon,
            "title": canon,
            "body": "",
            "state": "",
            "seated": seated is not None,
            "ready": seated is None,
            "detail": f"{type(exc).__name__}",
            "law": law,
        }
    if result.returncode != 0:
        return {
            "ok": False,
            "spec": canon,
            "title": canon,
            "body": "",
            "state": "",
            "seated": seated is not None,
            "ready": seated is None,
            "detail": (result.stderr or result.stdout or "gh failed")[:400],
            "law": law,
        }
    try:
        blob = json.loads(result.stdout or "{}")
    except ValueError:
        blob = {}
    if not isinstance(blob, dict):
        blob = {}
    title = str(blob.get("title") or "").strip() or canon
    body = str(blob.get("body") or "")[:4000]
    state = str(blob.get("state") or "").strip()
    detail = ""
    if seated is not None:
        detail = (
            f"SEATED ({seated.get('owner_pr')}). Do not implement. "
            "Ticket Runner owns the seat."
        )
    return {
        "ok": True,
        "spec": canon,
        "title": title,
        "body": body,
        "state": state,
        "seated": seated is not None,
        "ready": seated is None,
        "detail": detail,
        "law": law,
    }


def list_open_issues(limit: int = 20, *, runner: RunFn | None = None) -> dict[str, Any]:
    """Open GitHub *issues* for CLAIMS repos. Marks SEATED. Never assigns on GitHub."""
    law = (
        "Crew /assign binds a teammate locally. Ticket Runner seats CLAIMS. "
        "Control does not assign. Crew does not set GitHub assignees."
    )
    if os.environ.get("CREW_LIVE_PROBES", "1") == "0":
        return {
            "ok": False,
            "detail": "CREW_LIVE_PROBES=0",
            "issues": [],
            "repos": [],
            "law": law,
        }
    run = runner or _run
    repos = _repos()
    issues: list[dict[str, Any]] = []
    errors: list[str] = []
    wait = _gh_wait_s()
    targets = repos or [""]
    for repo in targets:
        argv = [
            "gh",
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,url,updatedAt",
        ]
        if repo:
            argv.extend(["--repo", repo])
        try:
            result = run(argv, timeout=wait)
        except FileNotFoundError:
            return {
                "ok": False,
                "detail": "gh not installed",
                "issues": [],
                "repos": repos,
                "law": law,
            }
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
            spec = issue_spec(str(repo or ""), row.get("number"), str(row.get("url") or ""))
            if not spec:
                continue
            seated = seated_claim(spec)
            issues.append(
                {
                    "spec": spec,
                    "repo": repo or spec.rsplit("#", 1)[0],
                    "number": row.get("number"),
                    "title": row.get("title") or "",
                    "url": row.get("url") or "",
                    "updated": row.get("updatedAt") or "",
                    "seated": seated is not None,
                    "ready": seated is None,
                }
            )
    return {
        "ok": not errors or bool(issues),
        "detail": "; ".join(errors)[:400] if errors else "",
        "issues": issues[:80],
        "repos": repos,
        "law": law,
    }


def remember_fetched(data_dir: Path, payload: dict[str, Any]) -> None:
    """Cache /fetch results so GET /v1/belt stays off the gh path (Control 1.5s)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    issues = [
        row for row in (payload.get("issues") or []) if isinstance(row, dict)
    ][:_FETCH_CAP]
    blob = {
        "issues": issues,
        "detail": str(payload.get("detail") or "")[:400],
        "ok": bool(payload.get("ok")),
    }
    (data_dir / _FETCH_FILE).write_text(
        json.dumps(blob, ensure_ascii=False), encoding="utf-8"
    )


def remembered_issues(data_dir: Path) -> list[dict[str, Any]]:
    path = data_dir / _FETCH_FILE
    if not path.is_file():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = blob.get("issues") if isinstance(blob, dict) else blob
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict) and str(row.get("spec") or "").strip():
            out.append(row)
    return out[:_FETCH_CAP]


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
