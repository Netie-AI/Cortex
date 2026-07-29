"""Plain-English translation for every engine code a user could ever see.

Engine internals say ``port_conflict:8801`` and ``governor:error_streak:3``.
A person reading a Routines or Apps page needs to know what happened and what
to do about it. Every message here is written for someone who has never read
this codebase: no jargon, no blame, and always a next step.

Unknown codes degrade honestly — they keep the raw code visible rather than
inventing a friendly lie.
"""

from __future__ import annotations

from typing import Any

# code prefix -> (title, what happened, what to do)
_MESSAGES: dict[str, tuple[str, str, str]] = {
    "port_conflict": (
        "That port is already taken",
        "Another program on this computer is already using the port this app wanted.",
        "Close the other program, or approve the app again to get a different port.",
    ),
    # NOTE: "already_running" is defined once, further down. An app-flavoured
    # copy used to sit here and was silently shadowed by the routine one, so it
    # never reached a user. Distinct wording per surface needs a surface-scoped
    # key, not a second entry in the same dict.
    "process_exited": (
        "The app closed straight away",
        "It started but shut down before it could serve anything — usually a crash on startup.",
        "Check the app's start command and that its dependencies installed.",
    ),
    "health_timeout": (
        "The app never finished starting",
        "It was still not answering after the startup window, so we stopped it.",
        "Try again — if it keeps happening the app may need longer or be misconfigured.",
    ),
    "build_failed": (
        "Setup didn't finish",
        "Installing this app's dependencies failed.",
        "Check the app has a valid package list, then import it again.",
    ),
    "build_timeout": (
        "Setup took too long",
        "Installing dependencies ran past the time limit and was cancelled.",
        "Check your internet connection and try again.",
    ),
    "unsupported_stack": (
        "We can't run this kind of app yet",
        "This app needs a runtime we don't start for you yet (Docker apps, for example).",
        "Run it yourself for now, or repackage it as a Python, Node or static site app.",
    ),
    "node_runtime_missing": (
        "Node isn't installed",
        "This app needs Node.js, which isn't on this computer.",
        "Install Node.js, then start the app again.",
    ),
    "no_start_command": (
        "We couldn't tell how to start it",
        "The app didn't say which command runs it.",
        "Add a start script to the app, then import it again.",
    ),
    "missing_install_dir": (
        "The app's files are missing",
        "The folder this app was installed into is no longer there.",
        "Import the app again.",
    ),
    "stale_pid": (
        "It had already stopped",
        "The app wasn't running any more, so there was nothing to shut down.",
        "Nothing to do — it's marked stopped now.",
    ),
    "secrets_found": (
        "This app contains passwords or keys",
        "We found what look like real API keys or passwords saved inside the app's files.",
        "Remove them from the code, then import it again. Keys belong in settings, not files.",
    ),
    "stack_unknown": (
        "We don't recognise this app",
        "Nothing in the files told us how this app is meant to run.",
        "Make sure you zipped the whole project folder, including its setup files.",
    ),
    "stress_failed": (
        "The app didn't pass its check",
        "It couldn't be started safely during the automatic check.",
        "Make sure the app runs on your own machine first, then import it again.",
    ),
    "governor": (
        "Paused automatically",
        "This routine was paused to stop it wasting time or money.",
        "Look at the last few runs, fix the cause, then resume it.",
    ),
    "timeout_after": (
        "This run took too long",
        "The routine was still working past its time limit, so it was stopped.",
        "Give it more time, or ask for something smaller.",
    ),
    "unknown_commitment": (
        "We can't find that reminder",
        "It may already have been closed or dismissed.",
        "Refresh the list.",
    ),
    "provenance_required": (
        "We need to know where this came from",
        "A reminder is only stored with a record of where you said it — otherwise there'd be no way to check it later.",
        "Include the source, then try again.",
    ),
    "payload_not_wrapped": (
        "Outside text wasn't marked as data",
        "Text arriving from a webhook or another app has to be labelled as data before the engine reads it, so nothing inside it can act like an instruction.",
        "This is an internal safeguard — nothing to fix on your side.",
    ),
    "unknown_proposal": (
        "That suggestion is no longer on the list",
        "It may be from an older check, or the goal has moved on since.",
        "Run Seek again to get a fresh list.",
    ),
    "unknown_outcome": (
        "We don't recognise that response",
        "A suggestion can be marked as accepted, succeeded, dismissed or failed.",
        "Pick one of those and try again.",
    ),
    "no_goal_bound": (
        "No goal is set yet",
        "The engine works towards a goal you set — there isn't one yet, so there's nothing for it to pursue on its own.",
        "Set a goal describing what you want the business to achieve.",
    ),
    "unknown_goal": (
        "That goal no longer exists",
        "It may have been deleted.",
        "Refresh the page.",
    ),
    "goal_statement_required": (
        "Describe the goal first",
        "A goal needs one line saying what you want achieved, like \"grow monthly revenue without misleading anyone\".",
        "Write the goal in a sentence, then try again.",
    ),
    "missing_folder": (
        "We couldn't find that folder",
        "There's nothing at the location you gave us.",
        "Check the folder path and try again.",
    ),
    "empty_folder": (
        "That folder is empty",
        "There are no files in it to turn into an app.",
        "Pick the folder that actually contains your project.",
    ),
    "folder_too_many_files": (
        "That folder is too big",
        "It has more files than we import in one go — it may be the wrong folder.",
        "Pick your project folder itself, not a whole drive or home directory.",
    ),
    "folder_too_large": (
        "That folder is too large",
        "It's bigger than the import limit.",
        "Remove large files you don't need (videos, datasets), then try again.",
    ),
    "cannot_dockerize": (
        "We can't containerise this one",
        "We only write Dockerfiles for Python, Node and plain website apps.",
        "Nothing to do — the app still runs locally.",
    ),
    "already_has_dockerfile": (
        "It already has a Dockerfile",
        "This app came with its own, so we left it alone.",
        "Nothing to do.",
    ),
    "goal_required": (
        "Tell me what to do first",
        "This needs one line describing what you want done, like \"summarize my open PRs every weekday morning\".",
        "Type what you want, then try again.",
    ),
    "goal_not_met": (
        "It ran, but didn't produce a result",
        "The routine finished without the answer it was supposed to produce.",
        "Try rewording what you asked for, or give it more effort.",
    ),
    "already_running": (
        "It's already running",
        "This is running right now, so there's nothing to start.",
        "Wait for it to finish.",
    ),
    "not_runnable": (
        "This routine is paused",
        "Paused routines don't run on their schedule.",
        "Resume it to start it running again.",
    ),
    "unknown_routine": (
        "That routine no longer exists",
        "It may have been deleted.",
        "Refresh the page.",
    ),
    "unknown_app": (
        "That app no longer exists",
        "It may have been deleted.",
        "Refresh the page.",
    ),
    "not_approved": (
        "This app needs approving first",
        "Apps only run after you've looked at them and approved them.",
        "Open the app and choose Approve.",
    ),
    "no_port": (
        "This app has no port yet",
        "A port is assigned when you approve an app, and this one hasn't been approved.",
        "Approve the app, then start it.",
    ),
    "adapter_unavailable": (
        "That approach isn't available",
        "The engine doesn't have this way of working plugged in yet.",
        "Nothing to do — the engine will use one of its own approaches instead.",
    ),
}

_GOVERNOR_DETAIL = {
    "error_streak": "It failed several times in a row.",
    "cost_cap": "It used up the money you allowed it for today.",
}


def explain(code: str | None) -> dict[str, Any]:
    """Turn an engine code into {code, title, what, fix}. Never raises."""
    raw = str(code or "").strip()
    if not raw:
        return {"code": "", "title": "All good", "what": "Nothing went wrong.", "fix": ""}

    head, _, detail = raw.partition(":")
    head = head.strip()
    detail = detail.strip()

    if head == "governor":
        reason, _, extra = detail.partition(":")
        title, what, fix = _MESSAGES["governor"]
        specific = _GOVERNOR_DETAIL.get(reason.strip())
        return {
            "code": raw,
            "title": title,
            "what": specific or what,
            "fix": fix,
            "detail": extra.strip(),
        }

    for prefix, (title, what, fix) in _MESSAGES.items():
        if head == prefix or raw.startswith(prefix):
            out = {"code": raw, "title": title, "what": what, "fix": fix}
            if detail:
                out["detail"] = detail[:300]
            return out

    return {
        "code": raw,
        "title": "Something went wrong",
        "what": "The engine reported a problem we don't have a friendly explanation for yet.",
        "fix": "Try again. If it keeps happening, share this code with support.",
        "detail": raw[:300],
    }


def explain_all(codes: list[str] | None) -> list[dict[str, Any]]:
    return [explain(code) for code in (codes or [])]


def routine_state(routine: dict[str, Any]) -> dict[str, Any]:
    """One friendly line describing what a routine is doing right now."""
    status = str(routine.get("status") or "idle")
    if status == "running":
        return {"label": "Working now", "tone": "busy", "detail": ""}
    if status == "paused":
        reason = str(routine.get("paused_reason") or "")
        if reason.startswith("governor:"):
            explained = explain(reason)
            return {"label": "Paused automatically", "tone": "warn", "detail": explained["what"]}
        return {"label": "Paused", "tone": "idle", "detail": "You paused this."}
    if not routine.get("enabled"):
        return {"label": "Off", "tone": "idle", "detail": "This routine is switched off."}
    streak = int(routine.get("error_streak") or 0)
    if streak:
        return {
            "label": "Last run failed",
            "tone": "warn",
            "detail": f"It has failed {streak} time{'s' if streak > 1 else ''} in a row.",
        }
    return {"label": "Scheduled", "tone": "ok", "detail": ""}
