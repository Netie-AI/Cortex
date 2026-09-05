"""Scheduled operator work: GitHub, ship-gate, mail digest.

Default Cortex routines still dispatch through execute_run_plan. A "hello"
prompt on the minimal DAG only echoes the prompt — that is not GitHub, review,
build, or email. This module is the path those goals take: Crew gh/ship-gate,
IMAP read, optional SMTP send. Not a second orchestrator.
"""

from __future__ import annotations

import os
import re
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

OPERATOR_KINDS = frozenset({"github", "pr_create", "review", "build", "email"})

_PR_CREATE = re.compile(
    r"\b(?:open|create|file|cut)\b.{0,32}\b(?:pr|pull request)s?\b|\bgh pr create\b",
    re.I,
)
_GITHUB = re.compile(
    r"\b(?:github|gh pr|pull requests?|open prs?|my prs?)\b",
    re.I,
)
_REVIEW = re.compile(
    r"\b(?:code review|pr review|review (?:the )?(?:pr|pull|diff|code|repo)s?)\b",
    re.I,
)
_BUILD = re.compile(
    r"\b(?:ship[- ]?gate|pytest|github actions|\bci\b|run tests|test suite)\b",
    re.I,
)
_FORGE = re.compile(r"\b(?:openforge|3d mesh|netlist)\b", re.I)
_EMAIL = re.compile(
    r"\b(?:e-?mail|gmail|inbox|daily digest|send me (?:an? )?(?:e-?mail|digest)|imap)\b",
    re.I,
)

ListPrsFn = Callable[..., dict[str, Any]]
CreatePrFn = Callable[..., dict[str, Any]]
PrDiffFn = Callable[..., dict[str, Any]]
ShipFn = Callable[[str], str]
InboxFn = Callable[..., dict[str, Any]]
MailFn = Callable[[str, str], dict[str, Any]]


def classify(goal: str) -> frozenset[str]:
    text = goal or ""
    found: set[str] = set()
    if _PR_CREATE.search(text):
        found.add("pr_create")
        found.add("github")
    if _GITHUB.search(text):
        found.add("github")
    if _REVIEW.search(text):
        found.add("review")
        found.add("github")
    if _BUILD.search(text) or _FORGE.search(text):
        found.add("build")
    if _EMAIL.search(text):
        found.add("email")
    if not found:
        found.add("web")
    return frozenset(found)


def is_operator_work(kinds: frozenset[str] | set[str]) -> bool:
    return bool(frozenset(kinds) & OPERATOR_KINDS)


def explain_kinds(kinds: frozenset[str] | set[str]) -> str:
    k = frozenset(kinds)
    if not is_operator_work(k):
        return "This stays a Cortex engine run (search/generate), not an echo-only chip."
    bits: list[str] = []
    if "github" in k or "review" in k:
        bits.append("list open PRs with gh")
    if "review" in k:
        bits.append("read PR diffs")
    if "pr_create" in k:
        bits.append("open a PR with gh (never merge)")
    if "build" in k:
        bits.append("run the Crew ship-gate")
    if "email" in k:
        bits.append("read IMAP and send a digest if SMTP/Gmail app password is set")
    return "This run will " + ", ".join(bits) + "."


def _step(tool: str, ok: bool, summary: str) -> dict[str, Any]:
    return {"tool": tool[:80], "ok": bool(ok), "summary": str(summary or "")[:400]}


def _fmt_prs(blob: dict[str, Any]) -> str:
    rows = blob.get("prs") or []
    if not rows:
        return str(blob.get("detail") or "No open pull requests.")[:2000]
    lines = ["Open pull requests:"]
    for pr in rows[:20]:
        if not isinstance(pr, dict):
            continue
        lines.append(
            f"- #{pr.get('number')} {pr.get('title') or ''} {pr.get('url') or ''} "
            f"review={pr.get('review') or ''}"
        )
    law = str(blob.get("law") or "")
    if law:
        lines.append(law)
    return "\n".join(lines)[:8000]


def _build_slug(prompt: str) -> str:
    lowered = (prompt or "").lower()
    try:
        from CortexOS.crew.estate import CATALOG

        for fp in CATALOG:
            slug = str(getattr(fp, "slug", "") or "")
            full = str(getattr(fp, "full_name", "") or "")
            if slug and slug.lower() in lowered:
                return slug
            if full and full.lower() in lowered:
                return slug
    except Exception:
        pass
    if _FORGE.search(prompt or ""):
        return "OpenForge"
    return "AirGPT"


def send_digest(subject: str, body: str) -> dict[str, Any]:
    """Outbound digest. Crew inbox.py still never sends; the engine may."""
    user = (os.environ.get("SMTP_USER") or os.environ.get("GMAIL_IMAP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASS") or os.environ.get("GMAIL_APP_PASSWORD") or "").strip()
    to = (os.environ.get("SMTP_TO") or user).strip()
    host = (os.environ.get("SMTP_HOST") or "").strip()
    if not host and user.lower().endswith("@gmail.com"):
        host = "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or (587 if host else 0) or 0)
    if not (user and password and to and host and port):
        return {
            "ok": False,
            "sent": False,
            "error": "no_smtp",
            "detail": "Set GMAIL_IMAP_USER+GMAIL_APP_PASSWORD (Gmail SMTP) or SMTP_HOST/USER/PASS/TO.",
        }
    msg = EmailMessage()
    msg["Subject"] = (subject or "Netie digest")[:200]
    msg["From"] = user
    msg["To"] = to
    msg.set_content((body or "")[:20000])
    try:
        with smtplib.SMTP(host, port, timeout=8) as smtp:
            smtp.ehlo()
            if port == 587:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as exc:
        return {"ok": False, "sent": False, "error": type(exc).__name__, "detail": str(exc)[:240]}
    return {"ok": True, "sent": True, "to": to}


def run(
    prompt: str,
    *,
    kinds: frozenset[str] | set[str] | None = None,
    list_prs_fn: ListPrsFn | None = None,
    create_pr_fn: CreatePrFn | None = None,
    pr_diff_fn: PrDiffFn | None = None,
    ship_fn: ShipFn | None = None,
    inbox_fn: InboxFn | None = None,
    mail_send_fn: MailFn | None = None,
) -> dict[str, Any]:
    kinds = frozenset(kinds or classify(prompt))
    if not is_operator_work(kinds):
        return {
            "ok": False,
            "error": "not_operator_work",
            "output": "",
            "steps": [],
            "work_kinds": sorted(kinds),
            "chosen": "scheduled_work",
        }

    steps: list[dict[str, Any]] = []
    chunks: list[str] = []
    errors: list[str] = []
    did = False

    if kinds & {"github", "review", "pr_create"}:
        if list_prs_fn is None:
            from CortexOS.crew import github as github_mod

            list_prs_fn = github_mod.list_prs
        listed = list_prs_fn()
        ok = bool(listed.get("ok") or listed.get("prs"))
        did = True
        steps.append(_step("gh_pr_list", ok, f"{len(listed.get('prs') or [])} open PRs"))
        chunks.append(_fmt_prs(listed if isinstance(listed, dict) else {}))
        if not ok:
            errors.append(str(listed.get("detail") or listed.get("error") or "gh_pr_list failed")[:300])

        if "review" in kinds:
            if pr_diff_fn is None:
                from CortexOS.crew import github as github_mod

                pr_diff_fn = github_mod.pr_diff
            prs = [p for p in (listed.get("prs") or []) if isinstance(p, dict)][:2]
            if not prs:
                steps.append(_step("gh_pr_diff", True, "no open PR to review"))
            for pr in prs:
                num = pr.get("number")
                diff = pr_diff_fn(number=num, repo=str(pr.get("repo") or ""))
                dok = bool(diff.get("ok"))
                text = str(diff.get("diff") or diff.get("detail") or "")[:4000]
                steps.append(_step("gh_pr_diff", dok, f"PR #{num} {len(text)} chars"))
                chunks.append(f"## Review diff PR #{num}\n{text or '(empty diff)'}")
                if not dok:
                    errors.append(str(diff.get("detail") or "gh_pr_diff failed")[:300])

        if "pr_create" in kinds:
            if create_pr_fn is None:
                from CortexOS.crew import github as github_mod

                create_pr_fn = github_mod.create_pr
            created = create_pr_fn(title=(prompt or "Scheduled PR")[:120])
            cok = bool(created.get("ok"))
            did = True
            steps.append(
                _step(
                    "gh_pr_create",
                    cok,
                    str(created.get("url") or created.get("detail") or created.get("error") or "")[:240],
                )
            )
            chunks.append(
                "PR create: "
                + str(created.get("url") or created.get("detail") or created.get("error") or "")
            )
            if not cok:
                errors.append(str(created.get("detail") or created.get("error") or "gh_pr_create failed")[:300])

    if "build" in kinds:
        slug = _build_slug(prompt)
        if ship_fn is None:
            from CortexOS.crew.ship_gate import render_slug

            ship_fn = render_slug
        try:
            report = ship_fn(slug)
            did = True
            steps.append(_step("ship_gate", True, f"{slug}: {str(report)[:120]}"))
            chunks.append(str(report)[:8000])
        except Exception as exc:
            steps.append(_step("ship_gate", False, type(exc).__name__))
            errors.append(str(exc)[:300])

    if "email" in kinds:
        if inbox_fn is None:
            from CortexOS.crew import inbox as inbox_mod

            inbox_fn = inbox_mod.fetch
        mail = inbox_fn(limit=8)
        mok = bool(mail.get("ok") or mail.get("messages"))
        msgs = mail.get("messages") or []
        did = True
        steps.append(_step("imap_fetch", mok, f"{len(msgs)} headers"))
        digest_lines = ["Inbox digest:"]
        for row in msgs[:8]:
            if not isinstance(row, dict):
                continue
            digest_lines.append(
                f"- {row.get('date') or ''} | {row.get('from') or ''} | {row.get('subject') or ''}"
            )
        if not msgs:
            digest_lines.append(str(mail.get("detail") or "No IMAP messages."))
        digest = "\n".join(digest_lines)[:8000]
        chunks.append(digest)
        if not mok:
            errors.append(str(mail.get("detail") or "imap_fetch failed")[:300])
        sender = mail_send_fn or send_digest
        sent = sender("Netie daily digest", digest)
        sok = bool(sent.get("ok") and sent.get("sent"))
        steps.append(_step("smtp_send", sok, str(sent.get("to") or sent.get("error") or sent.get("detail") or "")[:240]))
        if sok:
            chunks.append(f"Sent digest to {sent.get('to')}.")
        else:
            chunks.append("Digest not sent: " + str(sent.get("detail") or sent.get("error") or "no_smtp"))
            if sent.get("error") != "no_smtp":
                errors.append(str(sent.get("detail") or sent.get("error") or "smtp failed")[:300])

    output = "\n\n".join(c for c in chunks if c).strip()
    ok = did and bool(output) and not (
        kinds <= {"email"} and not any(s.get("ok") for s in steps)
    )
    if did and output and "github" in kinds:
        ok = any(s.get("tool") == "gh_pr_list" and s.get("ok") for s in steps) or any(
            s.get("tool") == "gh_pr_create" and s.get("ok") for s in steps
        )
    if did and "build" in kinds:
        ok = ok or any(s.get("tool") == "ship_gate" and s.get("ok") for s in steps)
    if did and "email" in kinds:
        ok = any(s.get("tool") in ("imap_fetch", "smtp_send") and s.get("ok") for s in steps)
    return {
        "ok": bool(ok),
        "status": "ok" if ok else "error",
        "output": output,
        "error": "; ".join(errors)[:1000],
        "steps": steps,
        "work_kinds": sorted(kinds & OPERATOR_KINDS or kinds),
        "chosen": "scheduled_work",
    }
