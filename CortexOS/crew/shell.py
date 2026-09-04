"""Crew dual-path exec: laptop local-exec or Cloudflare Computer isolate.

Laptop reuses the existing approved ``gh`` tools (github.py) behind
``policy.decide_runtime``. The isolate path is an adapter toward
``workspace.runtime.exec`` from https://github.com/cloudflare/computer —
PREVIEW only, not production. Feature flag ``CREW_CF_COMPUTER`` is off by
default. Host credentials are never copied into isolate env unless the
operator sets ``CREW_CF_COMPUTER_FORWARD_HOST_ENV=1``, and even then
secret-shaped keys (and the CF token itself) are stripped.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from CortexOS.crew import policy
from CortexOS.crew.config import (
    BACKEND_CF_COMPUTER,
    BACKEND_LAPTOP,
    FLAG_CF_COMPUTER,
    CrewSettings,
    parse_runtime_backend,
)

CF_COMPUTER_SOURCE = "https://github.com/cloudflare/computer"
# Matches workspace.runtime.exec command backends in the preview package.
CF_PREVIEW_RUNTIME_BACKEND = "worker-shell"

_SECRET_KEY_RE = re.compile(
    r"(API_KEY|ACCESS_KEY|SECRET|TOKEN|PASSWORD|PASSWD|AUTHORIZATION|CREDENTIAL|PRIVATE_KEY)$",
    re.I,
)

RunFn = Callable[..., subprocess.CompletedProcess[str]]
PostFn = Callable[[str, dict[str, Any], str, float], dict[str, Any]]


class RuntimeAdapter(Protocol):
    """One exec plane. Tests inject fakes; production uses laptop or CF preview."""

    name: str

    def exec(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> ExecResult: ...


@dataclass(frozen=True)
class ExecResult:
    ok: bool
    backend: str
    argv: tuple[str, ...]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 1
    denied: bool = False
    confirm: bool = False
    reason: str = ""
    preview: bool = False
    production: bool = False
    isolate_env_keys: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "argv": list(self.argv),
            "stdout": self.stdout[:8000],
            "stderr": self.stderr[:2000],
            "exit_code": self.exit_code,
            "denied": self.denied,
            "confirm": self.confirm,
            "reason": self.reason,
            "preview": self.preview,
            "production": self.production,
            "isolate_env_keys": list(self.isolate_env_keys),
        }


def _is_secret_key(name: str) -> bool:
    if name.upper() in {FLAG_CF_COMPUTER, "CREW_CF_COMPUTER_TOKEN", "CREW_CF_COMPUTER_URL"}:
        return True
    return bool(_SECRET_KEY_RE.search(name))


def isolate_env(
    explicit: dict[str, str] | None,
    *,
    forward_host: bool,
    extra_block: frozenset[str] | None = None,
) -> dict[str, str]:
    """Build isolate env. Default is empty. Never copies host secrets."""
    blocked = extra_block or frozenset()
    out: dict[str, str] = {}
    if forward_host:
        for key, value in os.environ.items():
            if key in blocked or _is_secret_key(key):
                continue
            out[key] = value
    for key, value in (explicit or {}).items():
        if key in blocked or _is_secret_key(key):
            continue
        out[key] = value
    out.pop("CREW_CF_COMPUTER_TOKEN", None)
    return out


def _run_local(argv: list[str], timeout: float = 20.0, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _post_json(url: str, body: dict[str, Any], token: str, timeout: float) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return {"ok": False, "detail": "non-object response"}
    return parsed


@dataclass
class LaptopAdapter:
    """Host subprocess of allowlisted argv. Same shape as github.py ``_run``."""

    runner: RunFn = field(default=_run_local)
    name: str = BACKEND_LAPTOP

    def exec(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> ExecResult:
        del env  # laptop keeps host env; isolate path is the leak boundary
        try:
            result = self.runner(argv, timeout=timeout, cwd=cwd)
        except TypeError:
            result = self.runner(argv, timeout=timeout)
        except FileNotFoundError:
            return ExecResult(
                ok=False,
                backend=self.name,
                argv=tuple(argv),
                reason=f"{argv[0]} not installed",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ExecResult(
                ok=False,
                backend=self.name,
                argv=tuple(argv),
                reason=f"{type(exc).__name__}: {exc}",
            )
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return ExecResult(
            ok=result.returncode == 0,
            backend=self.name,
            argv=tuple(argv),
            stdout=stdout,
            stderr=stderr,
            exit_code=int(result.returncode),
            reason="" if result.returncode == 0 else (stderr or stdout or "nonzero exit")[:400],
        )


@dataclass
class CloudflareComputerAdapter:
    """HTTP adapter toward a preview ``workspace.runtime.exec`` gateway.

    Cloudflare Computer is a Workers library, not a public production API.
    This posts the documented runtime.exec JSON. Missing URL/token is a
    preview-unavailable result, not a silent host fallback.
    """

    endpoint: str
    token: str = ""
    forward_host_env: bool = False
    post: PostFn = field(default=_post_json)
    name: str = BACKEND_CF_COMPUTER

    def exec(
        self,
        argv: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 20.0,
    ) -> ExecResult:
        blocked = frozenset({k for k, v in os.environ.items() if v and v == self.token} if self.token else [])
        sent_env = isolate_env(
            env,
            forward_host=self.forward_host_env,
            extra_block=blocked,
        )
        if not self.endpoint:
            return ExecResult(
                ok=False,
                backend=self.name,
                argv=tuple(argv),
                preview=True,
                production=False,
                isolate_env_keys=tuple(sorted(sent_env)),
                reason=(
                    "Cloudflare Computer preview gateway not configured "
                    "(set CREW_CF_COMPUTER_URL). PREVIEW only, not production. "
                    f"Source: {CF_COMPUTER_SOURCE}"
                ),
            )
        url = self.endpoint.rstrip("/")
        if not url.endswith("/runtime/exec"):
            url = url + "/runtime/exec"
        body = {
            "source": shlex.join(argv),
            "backend": CF_PREVIEW_RUNTIME_BACKEND,
            "timeoutMs": int(timeout * 1000),
            "env": sent_env,
        }
        if cwd:
            body["cwd"] = cwd
        try:
            payload = self.post(url, body, self.token, timeout)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return ExecResult(
                ok=False,
                backend=self.name,
                argv=tuple(argv),
                preview=True,
                production=False,
                isolate_env_keys=tuple(sorted(sent_env)),
                reason=f"preview gateway error: {type(exc).__name__}: {exc}",
            )
        stdout = str(payload.get("stdout") or "")
        stderr = str(payload.get("stderr") or payload.get("detail") or "")
        status = str(payload.get("status") or "")
        raw_code = payload.get("exitCode", payload.get("exit_code", 1))
        try:
            exit_code = int(raw_code) if isinstance(raw_code, (int, str)) else 1
        except (TypeError, ValueError):
            exit_code = 1
        ok = bool(payload.get("ok", status == "completed" or exit_code == 0))
        return ExecResult(
            ok=ok,
            backend=self.name,
            argv=tuple(argv),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            preview=True,
            production=False,
            isolate_env_keys=tuple(sorted(sent_env)),
            reason="" if ok else (stderr or status or "preview exec failed")[:400],
        )


class CrewShell:
    """Operator-selected dual path. Policy runs before either adapter."""

    def __init__(
        self,
        settings: CrewSettings,
        *,
        runner: RunFn | None = None,
        transport: PostFn | None = None,
    ) -> None:
        self.settings = settings
        self.laptop = LaptopAdapter(runner=runner or _run_local)
        self.isolate = CloudflareComputerAdapter(
            endpoint=settings.cf_computer_url,
            token=settings.cf_computer_token,
            forward_host_env=settings.cf_computer_forward_host_env,
            post=transport or _post_json,
        )

    def set_backend(self, name: str) -> dict[str, Any]:
        raw = (name or "").strip().lower().replace("_", "-")
        aliases = {
            "local": BACKEND_LAPTOP,
            "host": BACKEND_LAPTOP,
            "this-pc": BACKEND_LAPTOP,
            "cf": BACKEND_CF_COMPUTER,
            "cf-computer": BACKEND_CF_COMPUTER,
            "cloudflare": BACKEND_CF_COMPUTER,
            "isolate": BACKEND_CF_COMPUTER,
            "computer": BACKEND_CF_COMPUTER,
        }
        backend = aliases.get(raw, raw)
        if backend not in {BACKEND_LAPTOP, BACKEND_CF_COMPUTER}:
            raise ValueError(f"backend must be {BACKEND_LAPTOP} or {BACKEND_CF_COMPUTER}")
        self.settings.runtime_backend = backend
        return self.public()

    def public(self) -> dict[str, Any]:
        token_set = bool(self.settings.cf_computer_token)
        return {
            "backend": self.settings.runtime_backend,
            "backends": [BACKEND_LAPTOP, BACKEND_CF_COMPUTER],
            "flag": FLAG_CF_COMPUTER,
            "cf_computer": self.settings.cf_computer_enabled,
            "preview": True,
            "production": False,
            "endpoint_configured": bool(self.settings.cf_computer_url),
            "token_configured": token_set,
            "forward_host_env": self.settings.cf_computer_forward_host_env,
            "source": CF_COMPUTER_SOURCE,
            "laptop_allow": sorted(policy.LOCAL_SHELL_ALLOWLIST),
        }

    def exec(
        self,
        argv: list[str],
        *,
        approved: bool = False,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 20.0,
        backend: str | None = None,
    ) -> ExecResult:
        chosen = parse_runtime_backend(backend or self.settings.runtime_backend)
        decision, reason = policy.decide_runtime(
            argv,
            backend=chosen,
            isolate_enabled=self.settings.cf_computer_enabled,
            approved=approved,
        )
        if decision == policy.DENY:
            return ExecResult(
                ok=False,
                backend=chosen,
                argv=tuple(argv),
                denied=True,
                reason=reason,
                preview=chosen == BACKEND_CF_COMPUTER,
                production=False,
            )
        if decision == policy.CONFIRM:
            return ExecResult(
                ok=False,
                backend=chosen,
                argv=tuple(argv),
                confirm=True,
                reason=reason,
                preview=chosen == BACKEND_CF_COMPUTER,
                production=False,
            )
        adapter: RuntimeAdapter = self.isolate if chosen == BACKEND_CF_COMPUTER else self.laptop
        result = adapter.exec(argv, cwd=cwd, env=env, timeout=timeout)
        if chosen == BACKEND_CF_COMPUTER:
            # Belt-and-suspenders: isolate results never claim production.
            return ExecResult(
                ok=result.ok,
                backend=result.backend,
                argv=result.argv,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                denied=result.denied,
                confirm=result.confirm,
                reason=result.reason,
                preview=True,
                production=False,
                isolate_env_keys=result.isolate_env_keys,
            )
        return result


def public_status(settings: CrewSettings) -> dict[str, Any]:
    return CrewShell(settings).public()
