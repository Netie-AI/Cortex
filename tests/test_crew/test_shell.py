"""Gate tests for CortexOS.crew.shell.

The hostile corpus below is the point of the file: every entry is a command
line that a naive allowlist would run. Each must be refused *while the master
switch is on and the space is armed*, otherwise the test would pass for the
wrong reason (the switch, not the analysis).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from CortexOS.crew import shell

MODULE_SOURCE = Path(shell.__file__).read_text(encoding="utf-8")


def armed_decide(
    command: str,
    *,
    workspace_root: Path | None = None,
    allowed: frozenset[str] | None = None,
    denied: frozenset[str] | None = None,
) -> shell.ShellDecision:
    """Decide with both switches on, so only the analysis can refuse."""
    return shell.decide(
        command,
        armed=True,
        master_on=True,
        workspace_root=workspace_root,
        allowed=allowed,
        denied=denied,
    )


# --------------------------------------------------------------------------
# the module must stay a decision layer
# --------------------------------------------------------------------------


def test_gate_never_executes_anything() -> None:
    for spawn in ("import subprocess", "os.system", "popen", "os.exec", "os.spawn", "pty."):
        assert spawn not in MODULE_SOURCE, f"shell.py must not reach for {spawn!r}"
    assert not hasattr(shell, "subprocess")
    assert not hasattr(shell, "run")


# --------------------------------------------------------------------------
# master switch, arming, and the positive path (proves the gate can say yes)
# --------------------------------------------------------------------------


def test_master_switch_off_denies_even_a_read_only_command() -> None:
    verdict = shell.decide("ls .", armed=True, master_on=False)
    assert verdict.decision == shell.DENY
    assert "CORTEX_CREW_SHELL" in verdict.reason


def test_unarmed_space_denies_even_with_master_on() -> None:
    verdict = shell.decide("ls .", armed=False, master_on=True)
    assert verdict.decision == shell.DENY
    assert "not armed" in verdict.reason


def test_master_enabled_reads_the_one_switch() -> None:
    assert shell.master_enabled({}) is False
    assert shell.master_enabled({"CORTEX_CREW_SHELL": "0"}) is False
    assert shell.master_enabled({"CORTEX_CREW_SHELL": "1"}) is True
    assert shell.master_enabled({"CORTEX_CREW_SHELL": " on "}) is True


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls src",
        "cat notes/todo.md",
        "grep -rn pattern src",
        "head -n 20 notes/todo.md",
        'grep "two words" notes/todo.md',
        "wc -l notes/todo.md",
    ],
)
def test_read_only_allowlist_is_allowed(command: str) -> None:
    verdict = armed_decide(command)
    assert verdict.decision == shell.ALLOW, verdict.reason
    assert verdict.reason


@pytest.mark.parametrize(
    "command",
    ["python -m pytest -q", "git status", "ruff check src", "mkdir out", "cp a.txt b.txt"],
)
def test_mutating_allowlist_needs_operator_approval(command: str) -> None:
    verdict = armed_decide(command)
    assert verdict.decision == shell.CONFIRM, verdict.reason
    assert "approval" in verdict.reason


def test_argv_is_returned_for_the_integrator() -> None:
    verdict = armed_decide('grep -n "two words" notes/todo.md')
    assert verdict.executable == "grep"
    assert verdict.argv == ("-n", "two words", "notes/todo.md")
    assert verdict.public()["argv"] == ["-n", "two words", "notes/todo.md"]
    assert verdict.refused is False


# --------------------------------------------------------------------------
# unknown binaries are never guessed to be safe
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "curl https://example.test/x",
        "rm -rf .",
        "chmod 777 f",
        "powershell -Command x",
        "bash script.sh",
        "sh -c ls",
        "LS .",  # no case folding: the gate does not guess this means `ls`
        "ls.exe .",
        "/bin/ls .",
        "./ls",
        "../ls",
        "C:/Windows/System32/cmd",
    ],
)
def test_unknown_or_path_qualified_executable_is_denied(command: str) -> None:
    verdict = armed_decide(command)
    assert verdict.decision == shell.DENY
    assert verdict.reason


def test_a_flag_where_the_executable_belongs_is_denied() -> None:
    verdict = armed_decide("-rf /")
    assert verdict.decision == shell.DENY
    assert "flag" in verdict.reason


# --------------------------------------------------------------------------
# HOSTILE CORPUS - each entry names the tripwire its refusal must mention
# --------------------------------------------------------------------------

HOSTILE: list[tuple[str, str, str]] = [
    # command substitution
    ("substitution-dollar", "ls $(whoami)", "metacharacter"),
    ("substitution-brace", "cat ${HOME}/.ssh/id_rsa", "metacharacter"),
    ("substitution-quoted", 'ls "$(rm -rf .)"', "metacharacter"),
    ("substitution-ansi-c", "ls $'\\x3b' rm", "metacharacter"),
    # backticks
    ("backtick", "ls `whoami`", "metacharacter"),
    ("backtick-in-arg", "grep `id` notes.md", "metacharacter"),
    # pipes
    ("pipe", "cat notes.md | sh", "metacharacter"),
    ("pipe-double", "ls || rm -rf .", "metacharacter"),
    # boolean chaining
    ("and-and", "ls && rm -rf .", "metacharacter"),
    ("background", "ls & rm -rf .", "metacharacter"),
    # semicolon chaining, including a quoted one
    ("semicolon", "ls ; rm -rf .", "metacharacter"),
    ("semicolon-quoted", 'ls ";" rm -rf .', "metacharacter"),
    ("semicolon-escaped", "ls foo\\;bar", "backslash"),
    ("find-exec-terminator", "find . -exec rm {} ;", "metacharacter"),
    # redirection
    ("redirect-out", "cat notes.md > /etc/passwd", "metacharacter"),
    ("redirect-append", "echo pwned >> profile", "metacharacter"),
    ("redirect-in", "cat < /etc/shadow", "metacharacter"),
    # newline / carriage-return injection
    ("newline", "ls\nrm -rf .", "metacharacter"),
    ("newline-quoted", 'ls "a\nrm -rf ."', "metacharacter"),
    ("carriage-return", "ls\rrm -rf .", "metacharacter"),
    ("tab-control", "ls\tfoo\x0bbar", "control character"),
    ("nul-byte", "ls foo\x00rm", "NUL byte"),
    # path traversal in arguments
    ("traversal-posix", "cat ../../etc/passwd", "traversal"),
    # a backslash reaches shlex as an escape, so `..\..\x` would parse to
    # `....x` and slip the traversal scan; the gate refuses the ambiguity
    ("traversal-windows", "cat ..\\..\\Windows\\win.ini", "backslash"),
    ("traversal-buried", "cat notes/../../secrets.env", "traversal"),
    ("traversal-in-flag-value", "grep --file=../../etc/passwd .", "traversal"),
    ("traversal-encoded", "cat %2e%2e/secrets.env", "percent-encoded"),
    ("absolute-posix", "cat /etc/passwd", "absolute path"),
    ("absolute-windows", "cat C:/Users/me/.aws/credentials", "drive-qualified"),
    ("home-expansion", "cat ~/.ssh/id_rsa", "home-directory"),
    # allowlisted binary carrying a flag the gate cannot bound
    ("find-delete", "find . -delete", "-delete"),
    ("find-execdir", "find . -name x -execdir rm {} +", "-execdir"),
    ("git-dash-c", "git -c core.sshCommand=calc log", "-c"),
    ("git-upload-pack", "git --upload-pack=evil fetch origin", "--upload-pack"),
    ("rg-pre", "rg --pre=evil pattern .", "--pre"),
    ("sort-output", "sort -o out.txt notes.md", "-o"),
    ("sort-compress", "sort --compress-program=evil notes.md", "--compress-program"),
    ("tail-follow", "tail -f app.log", "-f"),
    ("tail-follow-bundled", "tail -nf 20 app.log", "-f"),
    ("python-inline", "python -c import os", "-c"),
    # unicode and quoting tricks
    ("fullwidth-semicolon", "ls \uff1b rm -rf .", "non-ASCII"),
    ("fullwidth-dollar", "ls \uff04(whoami)", "non-ASCII"),
    ("homoglyph-cyrillic", "\u0441at notes.md", "non-ASCII"),
    ("bidi-override", "ls \u202enotes.md", "non-ASCII"),
    ("zero-width-join", "l\u200bs notes.md", "non-ASCII"),
    ("nbsp-separator", "ls\u00a0notes.md", "non-ASCII"),
    ("unbalanced-quote", 'ls "notes.md', "unparseable"),
    ("unbalanced-single", "ls 'notes.md", "unparseable"),
    ("empty", "   ", "empty"),
    ("too-long", "ls " + "a" * 600, "at most"),
    ("too-many-args", "ls " + " ".join(f"f{i}" for i in range(40)), "exceeds"),
]


@pytest.mark.parametrize(
    ("command", "tripwire"),
    [(cmd, trip) for _, cmd, trip in HOSTILE],
    ids=[name for name, _, _ in HOSTILE],
)
def test_hostile_corpus_is_refused_with_a_named_tripwire(command: str, tripwire: str) -> None:
    verdict = armed_decide(command)
    assert verdict.decision == shell.DENY, f"gate allowed {command!r}: {verdict.reason}"
    assert verdict.refused is True
    assert tripwire in verdict.reason, f"reason {verdict.reason!r} does not name {tripwire!r}"


def test_every_hostile_case_is_also_refused_inside_a_real_workspace(tmp_path: Path) -> None:
    for _, command, _ in HOSTILE:
        verdict = armed_decide(command, workspace_root=tmp_path)
        assert verdict.decision == shell.DENY, command


# --------------------------------------------------------------------------
# workspace containment
# --------------------------------------------------------------------------


def test_paths_inside_the_workspace_root_pass(tmp_path: Path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "todo.md").write_text("x", encoding="utf-8")
    verdict = armed_decide("cat notes/todo.md", workspace_root=tmp_path)
    assert verdict.decision == shell.ALLOW, verdict.reason


def test_symlink_out_of_the_workspace_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.env").write_text("k=v", encoding="utf-8")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without developer mode
        pytest.skip(f"cannot create symlink here: {exc}")
    verdict = armed_decide("cat link/secret.env", workspace_root=root)
    assert verdict.decision == shell.DENY
    assert "outside the workspace" in verdict.reason


# --------------------------------------------------------------------------
# per-agent grants, mirroring policy.decide
# --------------------------------------------------------------------------


def test_agent_grant_deny_wins_over_the_allowlist() -> None:
    verdict = armed_decide("ls .", denied=frozenset({"ls"}))
    assert verdict.decision == shell.DENY
    assert "grant denies" in verdict.reason


def test_agent_grant_allowlist_narrows_further() -> None:
    verdict = armed_decide("cat notes.md", allowed=frozenset({"ls"}))
    assert verdict.decision == shell.DENY
    assert "grant does not allow" in verdict.reason
    assert armed_decide("ls .", allowed=frozenset({"ls"})).decision == shell.ALLOW


def test_a_grant_cannot_widen_past_the_allowlist() -> None:
    verdict = armed_decide("rm -rf .", allowed=frozenset({"rm"}))
    assert verdict.decision == shell.DENY
    assert "not on the shell allowlist" in verdict.reason
