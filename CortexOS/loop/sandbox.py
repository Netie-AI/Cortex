"""C-LOOP (#291) tool sandbox: no network, no source handle, scratch-only writes.

A loop tool runs inside :func:`sandboxed` with a fresh scratch folder. While it
runs, a process audit hook (PEP 578) refuses:

* any network call (``SANDBOX_NETWORK``);
* opening a database connection (``SANDBOX_SOURCE_HANDLE``);
* starting a process (``SANDBOX_SUBPROCESS``);
* writing, creating, moving or deleting a path outside the scratch folder
  (``SANDBOX_WRITE_OUTSIDE_SCRATCH``).

Every refusal is recorded on the run as well as raised, so a tool that catches
the exception is still refused. Inputs are checked before the tool starts: only
plain data crosses in; a connection or any other live object is refused
(``SANDBOX_SOURCE_HANDLE`` / ``SANDBOX_NON_DATA_INPUT``).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

NETWORK = "SANDBOX_NETWORK"
SOURCE_HANDLE = "SANDBOX_SOURCE_HANDLE"
SUBPROCESS = "SANDBOX_SUBPROCESS"
WRITE_OUTSIDE_SCRATCH = "SANDBOX_WRITE_OUTSIDE_SCRATCH"
NON_DATA_INPUT = "SANDBOX_NON_DATA_INPUT"

_NETWORK_EVENTS = frozenset(
    {
        "socket.connect",
        "socket.bind",
        "socket.sendto",
        "socket.sendmsg",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "urllib.Request",
        "http.client.connect",
        "ftplib.connect",
        "smtplib.connect",
    }
)
_SOURCE_EVENTS = frozenset({"sqlite3.connect", "sqlite3.connect/handle", "dbm.open"})
_PROCESS_EVENTS = frozenset(
    {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "pty.spawn"}
)
# Path-mutating events and the argument positions that hold a path.
_PATH_EVENTS: dict[str, tuple[int, ...]] = {
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.rename": (0, 1),
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.utime": (0,),
    "shutil.rmtree": (0,),
    "shutil.move": (0, 1),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


class SandboxRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(slots=True)
class _Run:
    scratch: Path
    refusals: list[SandboxRefused] = field(default_factory=list)


_ACTIVE: ContextVar[_Run | None] = ContextVar("c_loop_sandbox", default=None)
_HOOK_LOCK = threading.Lock()
_HOOK_INSTALLED = False


def _is_write(mode: Any, flags: Any) -> bool:
    if isinstance(mode, str) and any(c in mode for c in "wax+"):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _inside(path: Any, scratch: Path) -> bool:
    if isinstance(path, int):
        return True  # an fd the tool already holds; opening it was checked
    try:
        raw = os.fsdecode(path)
    except TypeError:
        return False
    resolved = Path(os.path.realpath(os.path.abspath(raw)))
    return resolved == scratch or scratch in resolved.parents


def _check(run: _Run, event: str, args: tuple[Any, ...]) -> SandboxRefused | None:
    if event in _NETWORK_EVENTS:
        return SandboxRefused(NETWORK, f"{event} is refused inside a loop tool")
    if event in _SOURCE_EVENTS:
        return SandboxRefused(SOURCE_HANDLE, f"{event}: a loop tool never opens a database")
    if event in _PROCESS_EVENTS:
        return SandboxRefused(SUBPROCESS, f"{event} is refused inside a loop tool")
    if event == "open" and args:
        path = args[0]
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if _is_write(mode, flags) and not _inside(path, run.scratch):
            return SandboxRefused(WRITE_OUTSIDE_SCRATCH, f"write to {path!r} is outside scratch")
        return None
    positions = _PATH_EVENTS.get(event)
    if positions:
        for pos in positions:
            if pos < len(args) and not _inside(args[pos], run.scratch):
                return SandboxRefused(
                    WRITE_OUTSIDE_SCRATCH, f"{event} on {args[pos]!r} is outside scratch"
                )
    return None


def _hook(event: str, args: tuple[Any, ...]) -> None:
    run = _ACTIVE.get()
    if run is None:
        return
    token = _ACTIVE.set(None)  # the check itself must not re-enter the hook
    try:
        refusal = _check(run, event, args)
    finally:
        _ACTIVE.reset(token)
    if refusal is not None:
        run.refusals.append(refusal)
        raise refusal


def _install_hook() -> None:
    global _HOOK_INSTALLED
    with _HOOK_LOCK:
        if not _HOOK_INSTALLED:
            sys.addaudithook(_hook)
            _HOOK_INSTALLED = True


def plain_data(value: Any, *, where: str = "inputs") -> Any:
    """Copy ``value`` as JSON-shaped data, or refuse a live object."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): plain_data(v, where=f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_data(v, where=f"{where}[{i}]") for i, v in enumerate(value)]
    kind = type(value).__name__
    if any(hasattr(value, attr) for attr in ("execute", "cursor", "sql", "commit")):
        raise SandboxRefused(SOURCE_HANDLE, f"{where} is a {kind}; a loop tool never gets a source handle")
    raise SandboxRefused(NON_DATA_INPUT, f"{where} is a {kind}; only plain data enters a loop tool")


@contextmanager
def sandboxed() -> Iterator[_Run]:
    """Run the body under the sandbox with a fresh scratch folder (removed after)."""
    _install_hook()
    scratch = Path(os.path.realpath(tempfile.mkdtemp(prefix="c_loop_scratch_")))
    run = _Run(scratch=scratch)
    token = _ACTIVE.set(run)
    try:
        yield run
    finally:
        _ACTIVE.reset(token)
        shutil.rmtree(scratch, ignore_errors=True)
