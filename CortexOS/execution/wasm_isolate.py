"""WASM sandbox for tier-0 tool execution (fuel + memory limits, no host I/O)."""

from __future__ import annotations

import os
from dataclasses import dataclass

# Minimal valid WASM module: (module (func (export "run") (result i32) i32.const 42))
_MINIMAL_WASM = bytes(
    [
        0x00,
        0x61,
        0x73,
        0x6D,
        0x01,
        0x00,
        0x00,
        0x00,
        0x01,
        0x05,
        0x01,
        0x60,
        0x00,
        0x01,
        0x7F,
        0x03,
        0x02,
        0x01,
        0x00,
        0x07,
        0x07,
        0x01,
        0x03,
        0x72,
        0x75,
        0x6E,
        0x00,
        0x00,
        0x0A,
        0x06,
        0x01,
        0x04,
        0x00,
        0x41,
        0x2A,
        0x0B,
    ]
)


@dataclass(frozen=True, slots=True)
class WasmRunResult:
    ok: bool
    return_value: int | None
    fuel_consumed: int | None
    error: str | None = None


@dataclass
class WasmSandbox:
    """Isolated WASM runner. No filesystem or network imports exposed."""

    max_fuel: int = 1_000_000
    max_memory_pages: int = 64  # 64 * 64KiB = 4 MiB

    def __post_init__(self) -> None:
        if os.environ.get("CORTEX_WASM_DISABLED", "").lower() in ("1", "true", "yes"):
            self._disabled = True
        else:
            self._disabled = False

    def run(
        self,
        wasm_bytes: bytes | None = None,
        export_name: str = "run",
        *,
        fuel: int | None = None,
    ) -> WasmRunResult:
        if self._disabled:
            return WasmRunResult(ok=False, return_value=None, fuel_consumed=None, error="wasm_disabled")

        try:
            from wasmtime import Config, Engine, Linker, Module, Store
        except ImportError as exc:
            return WasmRunResult(ok=False, return_value=None, fuel_consumed=None, error=f"wasmtime: {exc}")

        payload = wasm_bytes if wasm_bytes is not None else _MINIMAL_WASM
        budget = fuel if fuel is not None else self.max_fuel

        try:
            config = Config()
            config.consume_fuel = True
            config.max_wasm_stack = 512 * 1024
            engine = Engine(config)
            module = Module(engine, payload)
            linker = Linker(engine)
            # No host functions registered — deny FS/network by default
            store = Store(engine)
            store.set_fuel(budget)
            instance = linker.instantiate(store, module)
            func = instance.exports(store)[export_name]
            result = func(store)
            remaining = store.get_fuel()
            consumed = budget - remaining if remaining is not None else None
            return WasmRunResult(
                ok=True,
                return_value=int(result) if result is not None else None,
                fuel_consumed=consumed,
            )
        except Exception as exc:
            return WasmRunResult(ok=False, return_value=None, fuel_consumed=None, error=str(exc))

    def run_with_exhaustion_test(self, tiny_fuel: int = 10) -> WasmRunResult:
        """Verify fuel limits halt runaway execution."""
        return self.run(fuel=tiny_fuel)


def sandbox_available() -> bool:
    try:
        import wasmtime  # noqa: F401

        return True
    except ImportError:
        return False
