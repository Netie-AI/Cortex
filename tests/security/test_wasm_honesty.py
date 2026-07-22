"""C-SEC-6 — WASM isolate honesty tests.

Proves what the sandbox ACTUALLY guarantees today (kill-switch, fuel accounting,
no host functions linked) and documents — via skips with reasons — what is NOT
yet proven (crafted adversarial modules, hard memory-cap enforcement). The
sandbox is a SCAFFOLD; do not claim Firecracker parity (PARKING_LOT P2).
"""
from __future__ import annotations

import pytest

from CortexOS.execution.wasm_isolate import WasmSandbox, sandbox_available


def test_kill_switch_denies_execution(monkeypatch):
    monkeypatch.setenv("CORTEX_WASM_DISABLED", "1")
    res = WasmSandbox().run()
    assert not res.ok and res.error == "wasm_disabled"   # fail-closed kill switch


def test_no_host_functions_linked():
    # The linker registers zero host imports — FS/network are unreachable by
    # construction. This asserts the code path stays that way.
    import inspect

    from CortexOS.execution import wasm_isolate

    src = inspect.getsource(wasm_isolate)
    assert "define_wasi" not in src and "WasiConfig" not in src, "WASI must not be enabled"
    assert "No host functions registered" in src


@pytest.mark.skipif(not sandbox_available(), reason="wasmtime not installed")
def test_fuel_accounting_is_wired():
    res = WasmSandbox().run()
    assert res.ok
    # Honest scaffold guarantee: fuel is metered (consume_fuel=True) and reported.
    assert res.fuel_consumed is not None and res.fuel_consumed >= 0


@pytest.mark.skip(reason="SCAFFOLD: adversarial module (infinite loop / huge alloc / "
                         "host-import probe) needs a WAT toolchain; hardening is PARKING_LOT P2")
def test_adversarial_modules_placeholder():
    ...
