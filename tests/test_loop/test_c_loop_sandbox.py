"""C-LOOP (#291) tool sandbox: no network, no source handle, scratch-only writes.

Must-fails (each also fails with the sandbox removed — see
test_c_loop_guard_mutations.py): a registered tool that reaches the network,
receives or opens a database connection, or writes outside its scratch folder
is refused with a named code, even when the tool swallows the exception.
"""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from CortexOS.loop import sandbox
from CortexOS.loop.tools import CHART_SPEC, DATA_MAP, ToolDef, ToolRegistry, default_registry


def _registry(fn) -> ToolRegistry:  # noqa: ANN001
    return ToolRegistry((ToolDef("probe", "test probe", fn),))


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_must_fail_sandbox_refuses_network() -> None:
    port = _closed_port()

    def reach_out(inputs: Any, scratch: Path) -> dict[str, Any]:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return {"reached": True}

    run = _registry(reach_out).run("probe", {})
    assert run.served_status == "refused", run
    assert run.served_reason.startswith(sandbox.NETWORK), run


def test_must_fail_sandbox_refuses_swallowed_network_attempt() -> None:
    port = _closed_port()

    def sneaky(inputs: Any, scratch: Path) -> dict[str, Any]:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True}

    run = _registry(sneaky).run("probe", {})
    assert run.served_status == "refused", run
    assert run.served_reason.startswith(sandbox.NETWORK), run


def test_must_fail_sandbox_refuses_source_handle_input() -> None:
    con = sqlite3.connect(":memory:")
    seen: list[Any] = []

    def use_handle(inputs: Any, scratch: Path) -> dict[str, Any]:
        seen.append(inputs["db"])
        return {"rows": inputs["db"].execute("select 1").fetchall()}

    run = _registry(use_handle).run("probe", {"db": con})
    assert run.served_status == "refused", run
    assert run.served_reason.startswith(sandbox.SOURCE_HANDLE), run
    assert seen == [], "the tool must never start with a connection in hand"


def test_must_fail_sandbox_refuses_opening_a_database(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    sqlite3.connect(source).close()

    def open_db(inputs: Any, scratch: Path) -> dict[str, Any]:
        sqlite3.connect(str(source)).close()
        return {"opened": True}

    run = _registry(open_db).run("probe", {})
    assert run.served_status == "refused", run
    assert run.served_reason.startswith(sandbox.SOURCE_HANDLE), run


def test_must_fail_sandbox_refuses_write_outside_scratch(tmp_path: Path) -> None:
    target = tmp_path / "escaped.txt"

    def write_out(inputs: Any, scratch: Path) -> dict[str, Any]:
        target.write_text("leak", encoding="utf-8")
        return {"wrote": str(target)}

    run = _registry(write_out).run("probe", {})
    assert run.served_status == "refused", run
    assert run.served_reason.startswith(sandbox.WRITE_OUTSIDE_SCRATCH), run
    assert not target.exists()


def test_sandbox_refuses_a_subprocess() -> None:
    import subprocess

    def spawn(inputs: Any, scratch: Path) -> dict[str, Any]:
        subprocess.run(["true"], check=False)
        return {}

    run = _registry(spawn).run("probe", {})
    assert run.served_status == "refused" and run.served_reason.startswith(sandbox.SUBPROCESS)


def test_sandbox_allows_scratch_writes_and_reads() -> None:
    seen: list[Path] = []

    def scratch_io(inputs: Any, scratch: Path) -> dict[str, Any]:
        seen.append(scratch)
        path = scratch / "work" / "notes.txt"
        path.parent.mkdir()
        path.write_text("ok", encoding="utf-8")
        return {"read": path.read_text(encoding="utf-8")}

    run = _registry(scratch_io).run("probe", {"rows": [{"a": 1}]})
    assert run.served_status == "ok", run
    assert run.output == {"read": "ok"}
    assert seen and not seen[0].exists(), "scratch is removed after the run"


def test_sandbox_is_inactive_outside_a_tool_run(tmp_path: Path) -> None:
    (tmp_path / "free.txt").write_text("ok", encoding="utf-8")
    sqlite3.connect(":memory:").close()


def test_non_data_input_is_refused() -> None:
    run = _registry(lambda i, s: {}).run("probe", {"obj": object()})
    assert run.served_status == "refused" and run.served_reason.startswith(sandbox.NON_DATA_INPUT)


def test_unknown_tool_and_failing_tool_are_stamped() -> None:
    reg = _registry(lambda i, s: 1 / 0)
    assert reg.run("nope", {}).served_status == "refused"
    failed = reg.run("probe", {})
    assert failed.served_status == "failed" and "ZeroDivisionError" in failed.served_reason


def test_default_registry_holds_chart_and_data_map() -> None:
    reg = default_registry()
    assert reg.ids() == ("chart_spec", "data_map")
    assert reg.get("chart_spec") is CHART_SPEC and reg.get("data_map") is DATA_MAP


@pytest.mark.parametrize(
    ("rows", "expected_type"),
    [
        ([{"location_code": "KL", "revenue_myr": 10.0}, {"location_code": "PG", "revenue_myr": 4}], "bar"),
        ([{"month": "2026-01", "units": 3}, {"month": "2026-02", "units": 5}], "line"),
        ([{"total": 9}], "bignum"),
        ([{"sku": "A"}], "none"),
        ([], "none"),
    ],
)
def test_chart_spec_tool(rows: list[dict[str, Any]], expected_type: str) -> None:
    run = default_registry().run("chart_spec", {"rows": rows, "question": "q"})
    assert run.served_status == "ok", run
    assert run.output is not None and run.output["type"] == expected_type
    if expected_type in {"bar", "line"}:
        assert [p["value"] for p in run.output["data"]] == [float(r[run.output["y_label"]]) for r in rows]
