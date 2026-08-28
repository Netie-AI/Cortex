"""Shell command gate for crew agents - a decision layer, never an executor.

Same posture as ``CortexOS/crew/policy.py``: one master switch
(``CORTEX_CREW_SHELL=1``), then per-space arming by the operator, then a
per-command decision. Default is DENY, and every refusal carries the tripwire
that produced it - a control that refuses silently reads as a hang (KB R-0011).

**This module never runs anything.** No process module is imported here and no
handle to one is returned. Deciding is separable from executing, and the gate
is worth landing on its own: whether a crew agent is ever wired to a real
process is an operator decision that belongs in the integration step, not here.
A test asserts the source stays free of any spawn call so that boundary cannot
rot.

The analysis rule is the one ``CortexOS/execution/manifest.py`` states for SQL,
for the same reason: **a command line the gate cannot fully analyse is one it
cannot prove safe, so it is refused.** That is why the raw line is scanned for
shell metacharacters *before* quoting is stripped (a quoted ``;`` is still
refused - quoting must not be able to hide a chain from the scanner), why
non-ASCII is refused outright (a homoglyph or a bidi override is not the byte
sequence a shell would see), and why an unknown executable is never guessed to
be harmless.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

# Decision values are the same strings policy.decide returns, so a caller can
# render a tool refusal and a shell refusal through one code path.
ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

# Observation only: these read the workspace and write nothing. Everything not
# listed in one of the two tiers below is denied - the gate never infers that
# an unfamiliar binary is safe.
READ_ONLY_COMMANDS = frozenset(
    {
        "cat",
        "date",
        "diff",
        "echo",
        "grep",
        "head",
        "ls",
        "pwd",
        "rg",
        "sort",
        "tail",
        "uniq",
        "wc",
    }
)

# Allowlisted but able to change the tree, the environment, or the network, or
# able to run code of their own. Reaching these still costs a human Approve,
# exactly as a mutating computer-control tool does in policy.decide.
MUTATING_COMMANDS = frozenset(
    {
        "cp",
        "find",
        "git",
        "mkdir",
        "mv",
        "mypy",
        "node",
        "npm",
        "pip",
        "pytest",
        "python",
        "python3",
        "ruff",
    }
)

ALLOWLIST = READ_ONLY_COMMANDS | MUTATING_COMMANDS

# Flags that turn an allowlisted binary into something the gate can no longer
# bound: they run a second program, write to a path the argument scan does not
# see as a path, or block forever. Denied even though the binary is allowed.
DANGEROUS_FLAGS: dict[str, frozenset[str]] = {
    # -exec/-ok run an arbitrary child; -delete mutates without naming a target
    # the operator reviewed; -fprint* write to a path find chooses.
    "find": frozenset(
        {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fls", "-fprint", "-fprint0", "-fprintf"}
    ),
    # `git -c core.sshCommand=...` and --upload-pack/--receive-pack are remote
    # code execution dressed as configuration.
    "git": frozenset({"-c", "--config-env", "--exec-path", "--upload-pack", "--receive-pack"}),
    # --pre pipes every file through a program of the caller's choosing.
    "rg": frozenset({"--pre", "--pre-glob", "--hostname-bin"}),
    # --compress-program is a child process; -o writes outside the read tier.
    "sort": frozenset({"-o", "--output", "--compress-program", "--files0-from"}),
    # -f never terminates, so the gate cannot promise the call is bounded.
    "tail": frozenset({"-f", "--follow"}),
    # An inline program is the definition of a command line that cannot be
    # analysed. `-m module` stays available.
    "python": frozenset({"-c"}),
    "python3": frozenset({"-c"}),
}

# Characters that chain, background, substitute, or redirect. Scanned on the
# raw line, so quoting cannot smuggle one past the gate.
METACHARACTERS = frozenset(";&|<>$`()\n\r")

_MAX_COMMAND_CHARS = 512
_MAX_TOKENS = 24

_TRUTHY = frozenset({"1", "true", "TRUE", "yes", "on"})


@dataclass(frozen=True)
class ShellDecision:
    """One gate verdict. ``reason`` always names the tripwire, even on ALLOW."""

    decision: str
    reason: str
    executable: str = ""
    argv: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        return self.decision == DENY

    def public(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "executable": self.executable,
            "argv": list(self.argv),
        }


def master_enabled(env: dict[str, str] | None = None) -> bool:
    """Read the master switch. One place, so the switch cannot drift per call."""
    source = os.environ if env is None else env
    return source.get("CORTEX_CREW_SHELL", "").strip() in _TRUTHY


def _deny(reason: str, executable: str = "", argv: tuple[str, ...] = ()) -> ShellDecision:
    return ShellDecision(DENY, reason, executable, argv)


def _scan_raw(command: str) -> str | None:
    """Return a refusal reason for the raw line, or None if it is scannable."""
    if not command or not command.strip():
        return "empty command line"
    if len(command) > _MAX_COMMAND_CHARS:
        return (
            f"command line is {len(command)} chars; "
            f"the gate analyses at most {_MAX_COMMAND_CHARS}"
        )
    for index, char in enumerate(command):
        if char in METACHARACTERS:
            return f"shell metacharacter {char!r} at index {index} chains or redirects commands"
        if char == "\\":
            # A backslash is an escape to the parser and a separator to Windows,
            # so `..\..\x` reaches shlex as `....x` - the traversal scan would
            # never see it. Refuse rather than analyse two readings of one line.
            return (
                f"backslash at index {index} is both an escape and a path separator; "
                "the gate cannot settle on one reading - use forward slashes"
            )
        if char == "\x00":
            return f"NUL byte at index {index}"
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return f"control character {char!r} at index {index}"
        if ord(char) > 0x7F:
            return (
                f"non-ASCII character U+{ord(char):04X} at index {index}; "
                "the gate cannot prove it is the byte a shell would see"
            )
    return None


def _split(command: str) -> tuple[list[str] | None, str | None]:
    """shlex split, refusing anything that does not parse to a single reading."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, f"unparseable command line ({exc}); refusing what cannot be analysed"
    if not tokens:
        return None, "command line parsed to no tokens"
    if len(tokens) > _MAX_TOKENS:
        return None, f"{len(tokens)} arguments exceeds the {_MAX_TOKENS} the gate will analyse"
    return tokens, None


def _flag_hit(token: str, flags: frozenset[str]) -> str | None:
    """Match a dangerous flag exactly, as ``--flag=value``, or inside a bundle."""
    for flag in sorted(flags):
        if token == flag or token.startswith(flag + "="):
            return flag
        short = len(flag) == 2 and flag.startswith("-") and not flag.startswith("--")
        if short and token.startswith("-") and not token.startswith("--"):
            if flag[1] in token[1:]:
                return flag
    return None


def _path_candidates(token: str) -> list[str]:
    """Path-shaped strings inside one token, including a ``--flag=path`` value."""
    if token.startswith("-"):
        head, sep, tail = token.partition("=")
        return [tail] if sep and tail else []
    return [token]


def _scan_path(candidate: str, root: Path | None) -> str | None:
    """Refuse anything that names, or could resolve to, a path outside root."""
    if not candidate:
        return None
    lowered = candidate.lower()
    if "%2e" in lowered or "%2f" in lowered or "%5c" in lowered:
        return f"percent-encoded path in {candidate!r}; the gate cannot resolve it"
    if candidate.startswith("~"):
        return f"home-directory expansion in {candidate!r} reaches outside the workspace"
    normalized = candidate.replace("\\", "/")
    if normalized.startswith("/"):
        return f"absolute path {candidate!r} reaches outside the workspace"
    if len(candidate) >= 2 and candidate[1] == ":":
        return f"drive-qualified path {candidate!r} reaches outside the workspace"
    if any(segment == ".." for segment in normalized.split("/")):
        return f"path traversal '..' in {candidate!r}"
    if root is None:
        return None
    try:
        resolved = (root / normalized).resolve()
        resolved.relative_to(root.resolve())
    except (ValueError, OSError):
        return f"path {candidate!r} resolves outside the workspace root"
    return None


def decide(
    command: str,
    *,
    armed: bool,
    master_on: bool,
    workspace_root: Path | None = None,
    allowed: frozenset[str] | None = None,
    denied: frozenset[str] | None = None,
) -> ShellDecision:
    """Return the verdict for one command line. Default is DENY.

    ``allowed`` / ``denied`` are per-agent grants narrowing ALLOWLIST further; a
    deny always wins, and a non-empty ``allowed`` means only those binaries.
    ``workspace_root`` is the space's jailed root - when given, every path-shaped
    argument must resolve inside it.

    The switches are checked before the parse so the operator's fix is named
    first ("the shell is off") rather than buried under a syntax complaint. Once
    both are on, nothing else relaxes: the corpus of chaining, traversal and
    quoting tricks is refused with the master switch on and the space armed.
    """
    if not master_on:
        return _deny("shell access is off: set CORTEX_CREW_SHELL=1 and restart")
    if not armed:
        return _deny("shell is not armed for this space (arm it in the crew panel)")

    raw_reason = _scan_raw(command)
    if raw_reason is not None:
        return _deny(raw_reason)

    tokens, split_reason = _split(command)
    if tokens is None:
        return _deny(split_reason or "command line could not be analysed")

    executable, args = tokens[0], tuple(tokens[1:])
    if executable.startswith("-"):
        return _deny(f"command starts with a flag, not an executable: {executable!r}", "", args)
    if any(sep in executable for sep in ("/", "\\", ":")):
        return _deny(
            f"executable {executable!r} names a path; only bare allowlisted names run",
            "",
            args,
        )
    if denied and executable in denied:
        return _deny(f"agent grant denies '{executable}'", executable, args)
    if allowed and executable not in allowed:
        return _deny(f"agent grant does not allow '{executable}'", executable, args)
    if executable not in ALLOWLIST:
        return _deny(
            f"'{executable}' is not on the shell allowlist; "
            "unknown binaries are never assumed safe",
            executable,
            args,
        )

    dangerous = DANGEROUS_FLAGS.get(executable)
    if dangerous:
        for token in args:
            hit = _flag_hit(token, dangerous)
            if hit is not None:
                return _deny(
                    f"'{executable}' flag '{hit}' (in {token!r}) escapes what the gate can bound",
                    executable,
                    args,
                )

    for token in args:
        for candidate in _path_candidates(token):
            path_reason = _scan_path(candidate, workspace_root)
            if path_reason is not None:
                return _deny(path_reason, executable, args)

    if executable in READ_ONLY_COMMANDS:
        return ShellDecision(
            ALLOW,
            f"'{executable}' is a read-only allowlisted command",
            executable,
            args,
        )
    return ShellDecision(
        CONFIRM,
        f"'{executable}' can change the workspace and needs operator approval",
        executable,
        args,
    )
