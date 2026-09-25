"""#272 local OpenVault stub. Builds on tests/freeroute_fake.FakeOpenVault.

Does not edit the guarded parent. Adds served_*, error.reason, local_providers,
and local_reason for LOCAL-1 tests only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


def _parent_fake():
    name = "_cortex_freeroute_fake"
    mod = sys.modules.get(name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("freeroute_fake.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return mod


_Parent = _parent_fake().FakeOpenVault


class LocalFakeOpenVault(_Parent):
    """FakeOpenVault plus OpenVault#71 local hop / chat / refusal fields."""

    def __init__(self, base: str | None = None) -> None:
        if base is None:
            super().__init__()
        else:
            super().__init__(base)
        self.local_providers: set[str] = set()
        self.local_reason: str = ""

    @staticmethod
    def hop(
        provider: str,
        priority: int,
        *,
        circuit: str = "closed",
        parked: bool = False,
        served_local: bool | None = None,
    ) -> dict[str, Any]:
        row = dict(_Parent.hop(provider, priority, circuit=circuit, parked=parked))
        if served_local is not None:
            row["served_local"] = served_local
        return row

    def reply(
        self,
        content: str = "",
        *,
        status: int = 200,
        model: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        error_type: str = "",
        message: str = "",
        error_reason: str = "",
        served_provider: str | None = None,
        served_model: str | None = None,
        served_local: bool | None = None,
    ) -> LocalFakeOpenVault:
        super().reply(
            content,
            status=status,
            model=model,
            tool_calls=tool_calls,
            error_type=error_type,
            message=message,
        )
        if not self.replies:
            return self
        code, body = self.replies[-1]
        if not isinstance(body, dict):
            return self
        patched = dict(body)
        if code != 200:
            err = dict(patched.get("error") or {})
            if error_reason:
                err["reason"] = error_reason
            patched["error"] = err
        else:
            if served_provider is not None:
                patched["served_provider"] = served_provider
            if served_model is not None:
                patched["served_model"] = served_model
            if served_local is not None:
                patched["served_local"] = served_local
        self.replies[-1] = (code, patched)
        return self

    def __call__(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 5.0,
        base: str | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        status, data = super().__call__(
            method, path, body=body, headers=headers, timeout=timeout, base=base
        )
        route = path.split("?", 1)[0]
        if route != "/api/freeroute/status" or status != 200 or not isinstance(data, dict):
            return status, data
        out = dict(data)
        spendable: list[dict[str, Any]] = []
        for spec in out.get("spendable") or []:
            if not isinstance(spec, dict):
                continue
            row = dict(spec)
            pid = str(row.get("id") or "")
            if pid in self.local_providers:
                row["served_local"] = True
            spendable.append(row)
        out["spendable"] = spendable
        if self.local_reason:
            out["local_reason"] = self.local_reason
        return status, out


__all__ = ["LocalFakeOpenVault"]
