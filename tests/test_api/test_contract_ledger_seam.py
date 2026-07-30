"""The contract ledger routes resolve through the engine seam, not through packs.

C2 boundary: ``CortexOS`` may not import ``packs.*``. The audit ledger reaches
the contract routes by inversion — ``packs.dms`` registers into
``CortexOS.audit``, and the routes pull the provider back out.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

from CortexOS.api import contract_routes
from CortexOS.audit import ledger_registry

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def isolate_registry():
    """The registry is process-wide state — hand it back exactly as we found it."""
    previous = ledger_registry.registered_ledger()
    yield
    if previous is None:
        ledger_registry.clear_ledger()
    else:
        ledger_registry.register_ledger(previous)


class _StubLedger:
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, dict]] = []
        self.verified: list[int] = []

    def append(self, actor: str, event_type: str, payload: dict) -> dict:
        self.appended.append((actor, event_type, payload))
        return {
            "id": "e1",
            "seq": len(self.appended),
            "actor": actor,
            "event_type": event_type,
            "payload": payload,
            "prev_hash": "0" * 64,
            "entry_hash": "a" * 64,
            "created_at": "2026-07-30T00:00:00Z",
        }

    def verify(self, *, start_seq: int = 0) -> dict:
        self.verified.append(start_seq)
        return {"ok": True, "broken_at": None}


def test_register_then_resolve_round_trips() -> None:
    stub = _StubLedger()
    ledger_registry.register_ledger(stub)

    assert ledger_registry.resolve_ledger() is stub


def test_resolve_raises_when_active_pack_ships_no_ledger() -> None:
    # conftest pins PACK=ruma, which registers no audit seam.
    ledger_registry.clear_ledger()

    with pytest.raises(ledger_registry.LedgerNotRegistered):
        ledger_registry.resolve_ledger()


def test_ledger_routes_go_through_the_registry() -> None:
    stub = _StubLedger()
    ledger_registry.register_ledger(stub)

    entry = asyncio.run(
        contract_routes.contract_ledger_append(
            contract_routes.LedgerAppendRequest(
                actor="tester", event_type="demo.event", payload={"k": "v"}
            )
        )
    )
    chain = asyncio.run(
        contract_routes.contract_ledger_verify(
            contract_routes.LedgerVerifyRequest(start_seq=3)
        )
    )

    assert stub.appended == [("tester", "demo.event", {"k": "v"})]
    assert stub.verified == [3]
    assert entry.actor == "tester"
    assert chain.ok is True


@pytest.mark.parametrize(
    ("handler", "body"),
    [
        (contract_routes.contract_ledger_append, contract_routes.LedgerAppendRequest(
            actor="a", event_type="e", payload={}
        )),
        (contract_routes.contract_ledger_verify, contract_routes.LedgerVerifyRequest()),
    ],
)
def test_ledger_routes_report_501_without_a_provider(handler, body) -> None:
    ledger_registry.clear_ledger()

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(handler(body))

    assert excinfo.value.status_code == 501
    assert excinfo.value.detail["feature"] == "contract.ledger"


def test_dms_register_engine_seams_installs_the_real_ledger() -> None:
    import packs.dms

    ledger_registry.clear_ledger()
    packs.dms.register_engine_seams()

    resolved = ledger_registry.resolve_ledger()
    assert resolved.__name__ == "packs.dms.audit.ledger"
    for op in ("append", "verify", "list_entries"):
        assert callable(getattr(resolved, op))


@pytest.mark.parametrize(
    "snippet",
    [
        # Cold process, pack imported explicitly: import alone lights the seam.
        "import packs.dms",
        # Cold process, nobody touched the pack: the registry loads PACK itself.
        "pass",
    ],
)
def test_cold_process_resolves_the_dms_ledger(snippet: str) -> None:
    env = {**os.environ, "PACK": "dms"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"{snippet}\n"
            "from CortexOS.audit import resolve_ledger\n"
            "print(resolve_ledger().__name__)\n",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "packs.dms.audit.ledger"
