"""Replace placeholder wasm tests."""

import pytest

from CortexOS.execution.wasm_isolate import WasmSandbox, sandbox_available


@pytest.mark.skipif(not sandbox_available(), reason="wasmtime not installed")
def test_wasm_isolate_not_placeholder() -> None:
    sb = WasmSandbox()
    res = sb.run()
    assert res.ok
    assert res.return_value == 42


def test_wasm_disabled_env() -> None:
    import os

    os.environ["CORTEX_WASM_DISABLED"] = "1"
    try:
        sb = WasmSandbox()
        res = sb.run()
        assert not res.ok
        assert res.error == "wasm_disabled"
    finally:
        del os.environ["CORTEX_WASM_DISABLED"]
